# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract context for working with Pulumi.

Allows the application to provide certain requirements such as passphrases, defaults, etc.
on demand.

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

from .util import file_url_to_pathname, pathname_to_file_url
from .exceptions import XPulumiError
from .context import XPulumiContext
from .api_client import PulumiApiClient
from .constants import PULUMI_STANDARD_BACKEND

class XPulumiBackend:
  _ctx: XPulumiContext
  _url: str
  _url_parts: ParseResult
  _scheme: str
  _options: JsonableDict
  _requests_session: Optional[requests.Session] = None
  _access_token: Optional[str] = None
  _pulumi_account_name: Optional[str] = None
  _api_client: Optional[PulumiApiClient] = None

  def __init__(self, ctx: XPulumiContext, url: str=PULUMI_STANDARD_BACKEND, options: Optional[JsonableDict]=None):
    self._ctx = ctx
    self._url = url
    self._url_parts = urlparse(url)
    self._options = {} if options is None else deepcopy(options)
    if self.scheme == "file":
      # make file: URLs absolute. User cwd option if provided.
      self._url = pathname_to_file_url(self.abspath(file_url_to_pathname(url)))
      self._url_parts = urlparse(url)
      if self.scheme == 'https':
        self._requests_session = requests.Session()

  @property
  def ctx(self) -> XPulumiContext:
    return self._ctx

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
    if self.scheme == 'https':
      return True
    result = self.options.get('includes_organization', False)
    assert isinstance(result, bool)
    return result

  @property
  def includes_project(self) -> bool:
    if self.scheme == 'https':
      return True
    result = self.options.get('includes_project', False)
    assert isinstance(result, bool)
    return result

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

  def get_s3_bucket(self) -> str:
    if self.scheme != 's3':
      raise XPulumiError(f"Not an S3 backend: {self.url}")
    return self.url_parts.netloc

  def get_s3_key(self) -> str:
    if self.scheme != 's3':
      raise XPulumiError(f"Not an S3 backend: {self.url}")
    key = self.url_parts.path
    while key.startswith('/'):
      key = key[1:]
    return key

  def get_s3_project_key(self, organization: Optional[str]=None, project: Optional[str]=None) -> str:
    if self.scheme != 's3':
      raise XPulumiError(f"Not an S3 backend: {self.url}")
    url_parts = self.get_project_backend_url_parts(organization=organization, project=project)
    key = url_parts.path
    while key.startswith('/'):
      key = key[1:]
    return key

  def get_s3_stack_key(self, stack: str, organization: Optional[str]=None, project: Optional[str]=None) -> str:
    if self.scheme != 's3':
      raise XPulumiError(f"Not an S3 backend: {self.url}")
    url_parts = self.get_stack_backend_url_parts(stack, organization=organization, project=project)
    key = url_parts.path
    while key.startswith('/'):
      key = key[1:]
    return key

  def get_file_backend_pathname(self, cwd: Optional[str]=None, allow_relative: bool=True) -> str:
    result = file_url_to_pathname(self.url, cwd=cwd, allow_relative=allow_relative)
    return result

  def get_file_project_backend_pathname(
        self,
        cwd: Optional[str]=None,
        allow_relative: bool=True,
        organization: Optional[str]=None,
        project: Optional[str]=None
      ) -> str:
    result = file_url_to_pathname(
        self.get_project_backend_url(organization=organization, project=project),
        cwd=cwd,
        allow_relative=allow_relative
      )
    return result

  def get_file_stack_backend_pathname(
        self,
        stack: str,
        cwd: Optional[str]=None,
        allow_relative: bool=True,
        organization: Optional[str]=None,
        project: Optional[str]=None
      ) -> str:
    result = file_url_to_pathname(
        self.get_stack_backend_url(stack, organization=organization, project=project),
        cwd=cwd,
        allow_relative=allow_relative
      )
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

  def export_stack(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    if not bypass_pulumi or decrypt_secrets or self.scheme not in ('https', 's3', 'file', 'http'):
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
    else:
      if self.scheme in ('https', 'http'):
        export_data = self.api_client().export_stack_deployment(project, stack, organization=organization)
      else:
        raise NotImplementedError(f"Unable to bypass pulumi CLI for scheme {self.scheme}://")
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
    return stack_outputs

  def __str__(self) -> str:
    return f"<Pulumi backend {self.url}>"

