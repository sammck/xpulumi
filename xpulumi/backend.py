# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract backend for working with Pulumi.

Allows the application to work with a particular backend configuration.
"""

from typing import Optional, cast, Dict
from .internal_types import Jsonable, JsonableDict

import os
from abc import ABC, abstractmethod
from pulumi import automation as pauto
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
from copy import deepcopy
import boto3.session
from boto3.session import Session as BotoAwsSession
#from botocore.session import Session as BotocoreSession
import tempfile
import json
import requests

from .util import file_url_to_pathname, full_name_of_type, full_type, pathname_to_file_url
from .exceptions import XPulumiError
from .context import XPulumiContext
from .base_context import XPulumiContextBase
from .api_client import PulumiApiClient
from .passphrase import PassphraseCipher
from .constants import PULUMI_STANDARD_BACKEND, PULUMI_JSON_SECRET_PROPERTY_NAME, PULUMI_JSON_SECRET_PROPERTY_VALUE


class XPulumiBackend:
  _ctx: XPulumiContextBase
  _name: Optional[str] = None
  _url: str
  _url_parts: ParseResult
  _scheme: str
  _cfg_data: JsonableDict
  _options: JsonableDict
  _requests_session: Optional[requests.Session] = None
  _access_token: Optional[str] = None
  _pulumi_account_name: Optional[str] = None
  _api_client: Optional[PulumiApiClient] = None
  _includes_organization: bool
  _includes_project: bool
  _default_organization: Optional[str] = None

  def __init__(
        self,
        name: Optional[str] = None,
        ctx: Optional[XPulumiContextBase]=None,
        url: Optional[str]=None,
        options: Optional[JsonableDict]=None,
        cwd: Optional[str]=None
      ):
    if ctx is None:
      ctx = XPulumiContextBase(cwd=cwd)
    self._ctx = ctx
    if not name is None:
      if not url is None or not options is None:
        raise XPulumiError("if Backend name is provided, then url and options must be None")
      self.init_from_name(name)
    else:
      self._cfg_data = dict(options=options)
      self.final_init(url, options=options, cwd=cwd)

  def final_init(
        self,
        url: Optional[str]=None,
        options: Optional[JsonableDict]=None,
        cwd: Optional[str]=None
      ) -> None:
    if url is None:
      url = PULUMI_STANDARD_BACKEND
    self._url = url
    self._url_parts = urlparse(url)
    self._options = {} if options is None else deepcopy(options)
    if self.scheme == "file":
      # make file: URLs absolute. Use cwd option if provided.
      if cwd is None:
        cwd = cast(Optional[str], self.options.get('cwd', None))
        assert cwd is None or isinstance(cwd, str)
      self._url = pathname_to_file_url(self.abspath(file_url_to_pathname(url, cwd=cwd)))
      self._url_parts = urlparse(url)
    if self.scheme == 'https':
      self._requests_session = requests.Session()
      self._includes_organization = True
      self._includes_project = True
    else:
      self._includes_organization = cast(bool, self.options.get('includes_organization', False))
      assert isinstance(self._includes_organization, bool)
      self._includes_project = cast(bool, self.options.get('includes_project', False))
      assert isinstance(self._includes_project, bool)
    self._default_organization = cast(Optional[str], self.options.get("default_organization", None))
    assert self._default_organization is None or isinstance(self._default_organization, str)

  def init_from_name(self, name: str) -> None:
    backend_dir = self._ctx.get_backend_infra_dir(name)
    cfg_file = os.path.join(backend_dir, "backend.json")
    if not os.path.exists(cfg_file):
      raise XPulumiError(f"XPulumi backend does not exist: {name}")
    with open(cfg_file) as f:
      cfg_data = json.load(f)
    self._cfg_data = cfg_data
    url: Optional[str] = cfg_data.get('uri', None)
    options: Optional[JsonableDict] = cfg_data.get('options', None)
    self.final_init(url, options=options, cwd=backend_dir)

  @property
  def ctx(self) -> XPulumiContext:
    return self._ctx

  @property
  def name(self) -> str:
    assert not self._name is None
    return self._name

  @property
  def url(self) -> str:
    return self._url

  @property
  def url_parts(self) -> ParseResult:
    return self._url_parts

  @property
  def scheme(self) -> str:
    return self._url_parts.scheme

  @property
  def options(self) -> JsonableDict:
    return self._options

  def api_client(self) -> PulumiApiClient:
    if self._api_client is None:
      if not self.scheme in ('http', 'https'):
        raise XPulumiError(f"API client not available for pulumi backend {self.url}")
      access_token = self.require_access_token()
      self._api_client = PulumiApiClient(self.url, access_token=access_token)
    return self._api_client

  def abspath(self, pathname: str) -> str:
    cwd = self.options.get('cwd', '.')
    assert isinstance(cwd, str)
    cwd = os.path.abspath(os.path.expanduser(cwd))
    return os.path.join(cwd, os.path.expanduser(pathname))

  @property
  def includes_organization(self) -> bool:
    return self._includes_organization

  @property
  def includes_project(self) -> bool:
    return self._includes_project

  @property
  def default_organization(self) -> Optional[str]:
    return self._default_organization

  @property
  def is_standard(self) -> bool:
    return self.url == PULUMI_STANDARD_BACKEND

  def get_project_backend_url(self, organization: Optional[str]=None, project: Optional[str]=None) -> str:
    result = self.url
    if not self.includes_organization:
      if organization is None:
        raise XPulumiError(f"An organization name is required for this backend: {self.url}")
      if not result.endswith('/'):
        result += '/'
      result += organization
    if not self.includes_project:
      if project is None:
        raise XPulumiError(f"A project name is required for this backend: {self.url}")
      if not result.endswith('/'):
        result += '/'
      result += project
    return result

  def get_project_backend_url_parts(self, organization: Optional[str]=None, project: Optional[str]=None) -> ParseResult:
    return urlparse(self.get_project_backend_url(organization=organization, project=project))

  def get_stack_backend_url(
        self,
        stack: str,
        organization: Optional[str]=None,
        project: Optional[str]=None
      ) -> str:
    if self.url_parts.scheme == 'https':
      raise XPulumiError(f"Cannot get stack URL for scheme {self.url_parts.scheme}")
    result = self.get_project_backend_url(organization=organization, project=project)
    if not result.endswith('/'):
      result += '/'
    result += f".pulumi/stacks/{stack}.json"
    return result

  def get_stack_backend_url_parts(
        self,
        stack: str,
        organization: Optional[str]=None,
        project: Optional[str]=None
      ) -> ParseResult:
    return urlparse(self.get_stack_backend_url(stack, organization=organization, project=project))

  def read_uri_file_blob(self, blob_uri: str) -> bytes:
    parts = urlparse(blob_uri)
    if parts.scheme != 'file':
      raise XPulumiError(f"Invalid 'file:' URL: {blob_uri}")
    pathname = file_url_to_pathname(blob_uri, self.ctx.get_cwd())
    with open(pathname, 'rb') as f:
      bin_data = f.read()
    return bin_data

  def read_uri_s3_blob(self, blob_uri: str) -> bytes:
    parts = urlparse(blob_uri)
    if parts.scheme != 's3':
      raise XPulumiError(f"Invalid 'file:' URL: {blob_uri}")
    bucket = parts.netloc
    key = parts.path
    while key.startswith('/'):
      key = key[1:]
    aws_account = self.options.get("aws_account", None)
    assert aws_account is None or isinstance(aws_account, str)
    aws_region = self.options.get("aws_region", None)
    assert aws_region is None or isinstance(aws_region, str)
    aws = self.ctx.get_aws_session(aws_account=aws_account, aws_region=aws_region)
    s3 = aws.client('s3')
    resp = s3.get_object(Bucket=bucket, Key=key)
    bin_data = resp['Body'].read()
    assert isinstance(bin_data, bytes)
    return bin_data

  def read_uri_blob(self, blob_uri: str) -> bytes:
    scheme = urlparse(blob_uri).scheme
    if scheme == 'file':
      result = self.read_uri_file_blob(blob_uri)
    elif scheme == 's3':
      result = self.read_uri_s3_blob(blob_uri)
    else:
      raise XPulumiError(f"Direct blob reading not supported for scheme '{scheme}': {blob_uri}")
    return result

  def require_access_token(self) -> str:
    if self._access_token is None:
      if not self.scheme in ("https", "http"):
        raise XPulumiError(f"Access token not available for non-http backend {self.url}")
      access_token = self.ctx.get_environ().get('PULUMI_ACCESS_TOKEN', None)
      if access_token == '':
        access_token = None
      if access_token is None:
        access_token = self.ctx.get_pulumi_access_token(self.url)
      if access_token is None:
        raise XPulumiError(f"Could not determine access token for Pulumi backend {self.url}")
      self._access_token = access_token
    return self._access_token

  def require_pulumi_account_name(self) -> str:
    if self._pulumi_account_name is None:
      result = self.api_client().require_username()
      self._pulumi_account_name = result
    return self._pulumi_account_name

  def export_stack_with_cli(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
      ) -> JsonableDict:
    env = dict(self.ctx.get_environ())
    if self.scheme in ('https', 'http'):
      env['PULUMI_ACCESS_TOKEN'] = self.require_access_token()
    # TODO: determine secret provider and passphrase_id from stack config
    secrets_provider = "service" if self.scheme == 'https' else "passphrase"
    if secrets_provider == "passphrase":
      env['PULUMI_CONFIG_PASSPHRASE'] = self.ctx.get_pulumi_secret_passphrase(self.url, organization=organization, project=project, stack=stack)
    project_backend = pauto.ProjectBackend(self.get_project_backend_url(project=project, organization=organization))
    project_settings = pauto.ProjectSettings(project, "python", backend=project_backend)
    stack_settings = pauto.StackSettings(secrets_provider=secrets_provider)
    stacks_settings = {}
    stacks_settings[stack] = stack_settings
    export_data: Jsonable
    with tempfile.TemporaryDirectory() as work_dir:
      ws = pauto.LocalWorkspace(
          work_dir=work_dir,
          pulumi_home=self.ctx.get_pulumi_home(),
          env_vars=env,
          secrets_provider=secrets_provider,
          project_settings=project_settings,
          stack_settings=stacks_settings)
      
      if decrypt_secrets:
        deployment = ws.export_stack(stack)
        export_data = dict(version=deployment.version, deployment=cast(JsonableDict, deployment.deployment))
      else:
        resp = ws._run_pulumi_cmd_sync(
            ["stack", "export", "--stack", stack]
        )
        export_data = json.loads(resp.stdout)
    if not isinstance(export_data, dict) or not 'deployment' in export_data:
      raise RuntimeError(f"Could not locate stack resource via CLI in stack state for backend {self.url}, org={organization}, project={project}, stack={stack}")
    return export_data

  def jsonable_contains_encrypted_secrets(self, value: Jsonable) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
      pass
    elif isinstance(value, list):
      for v in value:
        if self.jsonable_contains_encrypted_secrets(v):
          return True
    elif isinstance(value, dict):
      if (PULUMI_JSON_SECRET_PROPERTY_NAME in value
              and value[PULUMI_JSON_SECRET_PROPERTY_NAME] == PULUMI_JSON_SECRET_PROPERTY_VALUE
              and 'ciphertext' in value):
        return True
      for v in value.values():
        if self.jsonable_contains_encrypted_secrets(v):
          return True
    else:
      raise XPulumiError(f"Value is not Jsonable: {full_type(value)}")
    return False

  def decrypt_jsonable(self, value: Jsonable, decrypter: PassphraseCipher) -> Jsonable:
    result: Jsonable
    if value is None or isinstance(value, (str, int, float, bool)):
      result = value
    elif isinstance(value, list):
      result = [ self.decrypt_jsonable(v, decrypter) for v in value ]
    elif isinstance(value, dict):
      if (PULUMI_JSON_SECRET_PROPERTY_NAME in value
              and value[PULUMI_JSON_SECRET_PROPERTY_NAME] == PULUMI_JSON_SECRET_PROPERTY_VALUE
              and 'ciphertext' in value):
        ciphertext = value['ciphertext']
        plaintext = decrypter.decrypt(ciphertext)
        result = { PULUMI_JSON_SECRET_PROPERTY_NAME: PULUMI_JSON_SECRET_PROPERTY_VALUE, "plaintext": plaintext }
      else:
        result = {}
        for k, v in value.items():
          if not isinstance(k, str):
            raise XPulumiError(f"Property name is not Jsonable: {full_type(k)}")
          result[k] = self.decrypt_jsonable(v, decrypter=decrypter)
    return result

  def export_stack_with_rest_api(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
      ) -> JsonableDict:
    if self.scheme not in ('http', 'https'):
      raise XPulumiError(f"Scheme {self.scheme} not supported using REST API: {self.url}")
    if decrypt_secrets:
      raise XPulumiError(f"Secret decryption not supported using REST API: {self.url}")
    export_data = self.api_client().export_stack_deployment(project, stack, organization=organization)
    if not isinstance(export_data, dict) or not 'deployment' in export_data:
      raise RuntimeError(f"Could not locate stack resource via REST API stack state data for backend {self.url}, org={organization}, project={project}, stack={stack}")
    return export_data

  def export_stack_with_blob_backend(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
      ) -> JsonableDict:
    if self.scheme not in ('file', 's3'):
      raise XPulumiError(f"Scheme {self.scheme} not supported using blob read: {self.url}")
    if decrypt_secrets:
      raise XPulumiError(f"Secret decryption not supported using blob read: {self.url}")
    stack_url = self.get_stack_backend_url(stack=stack, project=project, organization=organization)
    stack_blob = self.read_uri_blob(stack_url)
    stack_state = json.loads(stack_blob.decode('utf-8'))
    export_data: Jsonable = None
    if isinstance(stack_state, dict):
      version = stack_state.get('version', None)
      checkpoint = stack_state.get('checkpoint', None)
      if isinstance(checkpoint, dict):
        checkpoint_stack = checkpoint.get("stack", None)
        if not checkpoint_stack is None and checkpoint_stack != stack:
          raise RuntimeError(f"Backend checkpoint stack \"{checkpoint_stack}\" does not match requested stack for backend {self.url}, org={organization}, project={project}, stack={stack}")
        latest = checkpoint.get('latest', None)
        if latest is None:
          raise XPulumiError(f"Stack \"{stack}\" exists but has not yet been deployed for backend {self.url}, org={organization}, project={project}")
        if isinstance(latest, dict):
          export_data = dict(deployment=latest, version=version)
    if not isinstance(export_data, dict):
      raise RuntimeError(f"Malformed backend state file for backend {self.url}, org={organization}, project={project}, stack={stack}")
    return export_data

  def export_stack(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    if not bypass_pulumi or self.scheme not in ('https', 's3', 'file', 'http'):
      export_data = self.export_stack_with_cli(
          project,
          stack,
          organization=organization,
          decrypt_secrets=decrypt_secrets,
        )
    else:
      if self.scheme in ('https', 'http'):
        export_data = self.export_stack_with_rest_api(project, stack, organization=organization)
      elif self.scheme in ('file', 's3'):
        export_data = self.export_stack_with_blob_backend(project, stack, organization=organization)
      else:
        raise NotImplementedError(f"Unable to bypass pulumi CLI for scheme {self.scheme}://")
      if decrypt_secrets and self.jsonable_contains_encrypted_secrets(export_data):
        deployment = export_data['deployment']
        assert isinstance(deployment, dict)
        secrets_providers = deployment['secrets_providers']
        secret_provider_type = secrets_providers['type']
        if secret_provider_type == 'passphrase':
          salt_state = secrets_providers['state']['salt']
          passphrase = self.ctx.get_pulumi_secret_passphrase(self.url, project=project, stack=stack, organization=organization, salt_state=salt_state)
          decrypter = PassphraseCipher(passphrase, salt_state)
          export_data = cast(JsonableDict, self.decrypt_jsonable(export_data, decrypter))
        else:
          # TODO: support other secrets providers.  For now, just rerun the request using the CLI
          export_data = self.export_stack_with_cli(project, stack, organization=organization, decrypt_secrets=decrypt_secrets)
    if not isinstance(export_data, dict) or not 'deployment' in export_data:
      raise RuntimeError(f"Could not locate stack resource in stack state file for backend {self.url}, org={organization}, project={project}, stack={stack}")
    return export_data

  def get_stack_outputs(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    export_data = self.export_stack(project, stack, organization=organization, decrypt_secrets=decrypt_secrets, bypass_pulumi=bypass_pulumi)
    deployment = export_data['deployment']
    assert isinstance(deployment, dict)
    resources_data = deployment.get('resources', None)
    assert isinstance(resources_data, list)
    stack_data: Optional[JsonableDict] = None
    for resource_data in resources_data:
      if 'type' in resource_data and resource_data['type'] == 'pulumi:pulumi:Stack':
        stack_data = resource_data
        break
    if stack_data is None:
      raise RuntimeError(f"Could not locate stack resource in stack state file for backend {self.url}, org={organization}, project={project}, stack={stack}")
    stack_outputs = stack_data['outputs']
    if not isinstance(stack_outputs, dict):
      raise RuntimeError(f"Malformed outputs in stack state file for backend {self.url}, org={organization}, project={project}, stack={stack}")
    stack_outputs = dict(stack_outputs)
    for k, v in list(stack_outputs.items()):
      if (isinstance(v, dict)
              and PULUMI_JSON_SECRET_PROPERTY_NAME in v
              and v[PULUMI_JSON_SECRET_PROPERTY_NAME] == PULUMI_JSON_SECRET_PROPERTY_VALUE):
        if 'ciphertext' in v:
          stack_outputs[k] = "[secret]"
        elif 'plaintext' in v:
          stack_outputs[k] = json.loads(v['plaintext'])
    return stack_outputs

  def __str__(self) -> str:
    return f"<XPulumi backend {self.name} ==> {self.url}>"

