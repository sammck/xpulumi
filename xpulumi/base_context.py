# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Base context implementation for working with Pulumi.

Allows the application to provide certain requirements such as passphrases, defaults, etc.
on demand.

"""

from typing import TYPE_CHECKING, Optional, cast, Dict, Tuple, List, Callable, Union, Any
from .internal_types import Jsonable, JsonableDict

import os
from abc import ABC, abstractmethod
#from pulumi import automation as pauto
import subprocess
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
from copy import deepcopy
import distutils.spawn
import boto3.session
#from botocore.session import Session as BotocoreSession
import json
from secret_kv import open_kv_store, KvStore
from .context import XPulumiContext, BotoAwsSession, BotocoreSession
from project_init_tools import file_url_to_pathname
from .exceptions import XPulumiError
from .constants import PULUMI_STANDARD_BACKEND
from .config import XPulumiConfig

if TYPE_CHECKING:
  from .project import XPulumiProject
  from .backend import XPulumiBackend
  from .stack import XPulumiStack

#SessionVarEntry = Tuple[Optional[str], Optional[Union[List[str], str]], Any, Optional[Callable[[Any], Any]]]

def get_aws_identity(s: BotoAwsSession) -> Dict[str, str]:
  """Fetches AWS identity including the account number associated with an AWS session.

  The first time it is done for a session, requires a network request to AWS.
  After that, the result is cached on the session object.

  Args:
      s (BotoAwsSession): The AWS session in question.

  Returns:
      A dictionary with:
         ['Arn']  the AWS user's Arn
         ['Account'] The AWS account number
         ['UserId'] The user's AWS user ID
  """
  result: Dict[str, str]
  if hasattr(s, "_xpulumi_caller_identity"):
    result = s._xpulumi_caller_dentity  # type: ignore[attr-defined] # pylint: disable=protected-access
  else:
    sts = s.client('sts')
    result = sts.get_caller_identity()
    s._xpulumi_caller_identity = result  # type: ignore[attr-defined] # pylint: disable=protected-access
  return result

def get_aws_account(s: BotoAwsSession) -> str:
  return get_aws_identity(s)['Account']

class XPulumiContextBase(XPulumiContext):

  XPULUMI_INFRA_DIRNAME = 'xp'

  _aws_account_region_map: Dict[Tuple[Optional[str], Optional[str]], BotoAwsSession]
  """Maps an aws account name and region to an AWS session"""

  _environ: Dict[str, str]
  """local copy of environment variables that can be overridden"""

  _cwd: str
  """Working directory for this context"""

  _pulumi_home: Optional[str] = None
  """Location of pulumi installation"""

  _pulumi_cli: Optional[str] = None
  """Location of Pulumi CLI program. By default, located in PATH."""

  _pulumi_wrapped_cli: Optional[str] = None
  """Location of Wrapped Pulumi CLI program. located in our virtualenv."""

  _passphrase_by_id: Dict[str, str]
  """Cached map from passphrase unique ID to passphrase"""

  _passphrase_by_salt_state: Dict[str, str]
  """Cached map from salt state to passphrase"""

  _passphrase_by_backend_org_project_stack: Dict[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]], str]
  """Cached map from (backend, org, project, stack) to passphrase. "None" values used to provide
     defaults for project, backend, or entire context."""

  _access_token_map: Dict[str, Tuple[Optional[str], Optional[str]]]
  """Cached map from backend URL to access token and optional username"""

  _credentials_data: Optional[JsonableDict] = None

  _config: Optional[XPulumiConfig] = None

  _project_root_dir: Optional[str] = None

  _kv_store: Optional[KvStore] = None

  _default_backend_name: Optional[str] = None

  _default_stack_name: Optional[str] = None

  _default_cloud_subaccount: Optional[str] = None

  _project_cache: Dict[str, 'XPulumiProject']

  def __init__(self, config: Optional[XPulumiConfig]=None, cwd: Optional[str]=None):
    super().__init__()
    self._project_cache = {}
    self._aws_account_region_map = {}
    self._environ = dict(os.environ)
    self._cwd = os.getcwd() if cwd is None else os.path.abspath(os.path.normpath(os.path.expanduser(cwd)))
    self._passphrase_by_backend_org_project_stack = {}
    self._passphrase_by_id = {}
    self._passphrase_by_salt_state = {}
    self._access_token_map = {}
    if not config is None:
      self.init_from_config(config)

  def get_kv_store(self) -> KvStore:
    if self._kv_store is None:
      self._kv_store = open_kv_store(self.get_project_root_dir())
    return self._kv_store

  def get_simple_kv_secret(self, name: str) -> Jsonable:
    v = self.get_kv_store().get_value(name)
    if v is None:
      return None
    return v.as_simple_jsonable()

  def init_from_config(self, config: XPulumiConfig) -> None:
    self._config = config
    self._project_root_dir = config.project_root_dir
    self._pulumi_home = config.pulumi_home
    self._default_backend_name = config.default_backend_name
    self._default_stack_name = config.default_stack_name
    self._default_cloud_subaccount = config.default_cloud_subaccount

  @property
  def default_cloud_subaccount(self) -> Optional[str]:
    return self._default_cloud_subaccount

  def get_default_cloud_subaccount(self) -> Optional[str]:
    return self.default_cloud_subaccount

  @property
  def default_backend_name(self) -> Optional[str]:
    return self._default_backend_name

  @property
  def default_stack_name(self) -> Optional[str]:
    return self._default_stack_name

  def get_config(self) -> XPulumiConfig:
    if self._config is None:
      config = XPulumiConfig(starting_dir=self._cwd)
      self.init_from_config(config)
      assert not self._config is None
    return self._config

  def get_project_root_dir(self) -> str:
    if self._project_root_dir is None:
      self.get_config()
    assert not self._project_root_dir is None
    return self._project_root_dir

  def get_infra_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), self.XPULUMI_INFRA_DIRNAME)

  def get_project_infra_dir(self, project_name: Optional[str]=None, cwd: Optional[str]=None) -> str:
    return os.path.join(self.get_infra_dir(), 'project', self.get_project_name(project_name, cwd=cwd))

  def get_optional_project_name(self, project_name: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
    if project_name is None:
      if cwd is None:
        cwd = self.get_cwd()
      else:
        cwd = self.abspath(cwd)
      rdir = os.path.relpath(cwd, os.path.join(self.get_infra_dir(), 'project'))
      rdir_parts = rdir.split(os.sep)
      if rdir == '.' or rdir_parts[0] == '..':
        project_name = None
      else:
        project_name = rdir_parts[0]

    return project_name

  def get_project_name(self, project_name: Optional[str]=None, cwd: Optional[str]=None) -> str:
    project_name = self.get_optional_project_name(project_name, cwd=cwd)
    if project_name is None:
      if cwd is None:
        cwd = self.get_cwd()
      else:
        cwd = self.abspath(cwd)
      raise XPulumiError(f"Working directory is not inside an XPulumi project: {cwd}")

    return project_name

  def get_optional_project(self, project_name: Optional[str]=None, cwd: Optional[str]=None) -> Optional['XPulumiProject']:
    project_name = self.get_optional_project_name(project_name, cwd=cwd)
    if project_name is None:
      return None
    return self.get_project(project_name=project_name, cwd=cwd)

  def get_project(self, project_name: Optional[str]=None, cwd: Optional[str]=None) -> 'XPulumiProject':
    project_name = self.get_project_name(project_name, cwd=cwd)
    if project_name is None:
      raise XPulumiError("No project name was provided, and the directory is not an XPulumi project directory")
    project = self._project_cache.get(project_name, None)
    if project is None:
      from .project import XPulumiProject
      project = XPulumiProject(project_name, ctx=self, cwd=cwd)
      self._project_cache[project_name] = project
    return project

  def parse_xstack_name(
        self,
        xstack_name: str,
        default_stack_name: Optional[str]=None,
        default_project_name: Optional[str]=None,
        lone_is_project: bool = True,
        cwd: Optional[str] = None,
        use_env_defaults: bool = False,
      ) -> Tuple[Optional[str], Optional[str]]:
    parts = xstack_name.split(':')
    if len(parts) > 2:
      raise XPulumiError(f'Malformed xstack name: "{xstack_name}"')
    if len(parts) > 1:
      project_name, stack_name  = parts
    elif lone_is_project:
      project_name = xstack_name
      stack_name = ''
    else:
      project_name = ''
      stack_name = xstack_name
    project_name = project_name.strip()
    stack_name = stack_name.strip()
    if project_name == '':
      project_name = default_project_name
    if project_name is None and use_env_defaults:
      project_name = self.get_optional_project_name(cwd=cwd)
    if stack_name == '':
      stack_name = default_stack_name
    if stack_name is None and use_env_defaults:
      stack_name = self.get_optional_stack_name()
    return project_name, stack_name

  def parse_complete_xstack_name(
        self,
        xstack_name: str,
        default_stack_name: Optional[str]=None,
        default_project_name: Optional[str]=None,
        lone_is_project: bool = True,
        cwd: Optional[str] = None,
        use_env_defaults: bool = False,
      ) -> Tuple[str, str]:
    project_name, stack_name = self.parse_xstack_name(
        xstack_name,
        default_project_name=default_project_name,
        default_stack_name=default_stack_name,
        lone_is_project=lone_is_project,
        cwd = cwd,
        use_env_defaults=use_env_defaults,
      )
    if project_name is None or stack_name is None:
      raise XPulumiError(f'Malformed complete xstack name: "{xstack_name}"')
    return project_name, stack_name

  def get_stack_from_xstack_name(
        self,
        xstack_name: str,
        default_stack_name: Optional[str]=None,
        default_project_name: Optional[str]=None,
        lone_is_project: bool = True,
        cwd: Optional[str] = None,
        use_env_defaults: bool = False,
      ) -> 'XPulumiStack':
    project_name, stack_name = self.parse_complete_xstack_name(
        xstack_name,
        default_project_name=default_project_name,
        default_stack_name=default_stack_name,
        lone_is_project=lone_is_project,
        cwd = cwd,
        use_env_defaults=use_env_defaults,
      )
    project = self.get_project(project_name=project_name, cwd=cwd)
    stack = project.get_stack(stack_name, create=True)
    return stack

  def get_backend_infra_dir(self, backend_name: Optional[str]=None, cwd: Optional[str]=None) -> str:
    backend_name = self.get_backend_name(backend_name, cwd=cwd)
    return os.path.join(self.get_infra_dir(), 'backend', backend_name)

  def get_optional_backend_name(self, backend_name: Optional[str]=None, cwd: Optional[str]=None) -> Optional[str]:
    if backend_name is None:
      if cwd is None:
        cwd = self.get_cwd()
      else:
        cwd = self.abspath(cwd)
      rdir = os.path.relpath(cwd, os.path.join(self.get_infra_dir(), 'backend'))
      rdir_parts = rdir.split(os.sep)
      if rdir_parts[0] == '..':
        project = self.get_optional_project(cwd=cwd)
        if project is None:
          backend_name = None
        else:
          backend_name = project.backend.name
      else:
        backend_name = rdir_parts[0]

    return backend_name

  def get_backend_name(self, backend_name: Optional[str]=None, cwd: Optional[str]=None) -> str:
    backend_name = self.get_optional_backend_name(backend_name, cwd=cwd)
    if backend_name is None:
      if cwd is None:
        cwd = self.get_cwd()
      else:
        cwd = self.abspath(cwd)
      raise XPulumiError(f"Working directory is not inside an XPulumi backend or project: {cwd}")
    return backend_name

  def get_optional_backend(self, backend_name: Optional[str]=None, cwd: Optional[str]=None) -> Optional['XPulumiBackend']:
    backend_name = self.get_optional_backend_name(backend_name, cwd=cwd)
    if backend_name is None:
      return None
    from .backend import XPulumiBackend
    backend = XPulumiBackend(backend_name, ctx=self, cwd=cwd)
    return backend

  def get_backend(self, backend_name: Optional[str]=None, cwd: Optional[str]=None) -> 'XPulumiBackend':
    backend_name = self.get_backend_name(backend_name, cwd=cwd)
    from .backend import XPulumiBackend
    backend = XPulumiBackend(backend_name, ctx=self, cwd=cwd)
    return backend

  def get_optional_stack_name(self, stack_name: Optional[str]=None) -> Optional[str]:
    if stack_name is None:
      stack_name = self.default_stack_name
    return stack_name

  def get_stack_name(self, stack_name: Optional[str]=None) -> str:
    stack_name = self.get_optional_stack_name(stack_name)
    if stack_name is None:
      raise XPulumiError("A pulumi stack name is required")
    return stack_name

  def load_aws_session(
        self,
        aws_account: Optional[str]=None,
        aws_region: Optional[str]=None
      ) -> BotoAwsSession:
    # TODO: Find a profile in the desired AWS account. For now, just use the default profile # pylint:disable=fixme
    s = BotoAwsSession(region_name=aws_region)
    return s

  def get_aws_session(self, aws_account: Optional[str]=None, aws_region: Optional[str]=None) -> BotoAwsSession:
    s = self._aws_account_region_map.get((aws_account, aws_region), None)
    if s is None:
      s = self.load_aws_session(aws_account=aws_account, aws_region=aws_region)
      actual_aws_region = s.region_name
      if not aws_region is None and aws_region != actual_aws_region:
        raise XPulumiError(f"Loaded AWS session region {actual_aws_region} does not match required region {aws_region}")
      actual_aws_account = get_aws_account(s)
      if not aws_account is None and aws_account != actual_aws_account:
        raise XPulumiError(f"Loaded AWS session account {actual_aws_account} does not match required account {aws_account}")
      self._aws_account_region_map[(aws_account, aws_region)] = s

      # also add the actual account and region permutations into the map so they can be looked up quickly
      for k in [(aws_account, actual_aws_region), (actual_aws_account, aws_region), (actual_aws_account, actual_aws_region)]:
        if not k in self._aws_account_region_map:
          self._aws_account_region_map[k] = s
    return s

  def get_pulumi_access_token_and_username(self, backend_url: Optional[str]=None) -> Tuple[Optional[str], Optional[str]]:
    if backend_url is None:
      backend_url = PULUMI_STANDARD_BACKEND
    if backend_url in self._access_token_map:
      access_token, username = self._access_token_map[backend_url]
    else:
      access_token = self.get_environ().get("PULUMI_ACCESS_TOKEN", None)
      if access_token == '':
        access_token = None
      if access_token is None:
        access_token, username = self.get_credentials_backend_data(backend_url)
      self._access_token_map[backend_url] = (access_token, username)
    return access_token, username

  def get_pulumi_access_token(self, backend_url: Optional[str]=None) -> Optional[str]:
    return self.get_pulumi_access_token_and_username(backend_url=backend_url)[0]

  def get_pulumi_cred_username(self, backend_url: Optional[str]=None) -> Optional[str]:
    return self.get_pulumi_access_token_and_username(backend_url=backend_url)[1]

  def get_credentials_filename(self) -> Optional[str]:
    pulumi_home = self.get_pulumi_home()
    result = os.path.join(pulumi_home, "credentials.json")
    return result

  def get_credentials_data(self) -> JsonableDict:
    if self._credentials_data is None:
      credentials_file = self.get_credentials_filename()
      if not credentials_file is None:
        try:
          with open(credentials_file, encoding='utf-8') as f:
            json_text = f.read()
        except FileNotFoundError:
          pass
        json_data = json.loads(json_text)
        if isinstance(json_data, dict):
          self._credentials_data = json_data
      if self._credentials_data is None:
        self._credentials_data = {}
    return self._credentials_data

  def get_credentials_backend_data(self, backend_url: str) -> Tuple[Optional[str], Optional[str]]:
    creds = self.get_credentials_data()
    accounts = creds.get('accounts', None)
    if isinstance(accounts, dict):
      be_data = accounts.get(backend_url, None)
      if isinstance(be_data, dict):
        access_token = be_data.get('accessToken', None)
        username = be_data.get('username', None)
        assert access_token is None or isinstance(access_token, str)
        assert username is None or isinstance(username, str)
        return access_token, username
    return None, None

  def load_pulumi_secret_passphrase(
        self,
        backend_url: Optional[str]=None,
        organization: Optional[str]=None,
        project: Optional[str]=None,
        stack: Optional[str]=None,
        passphrase_id: Optional[str] = None,
        salt_state: Optional[str] = None,
      ) -> str:
    raise XPulumiError(
        f"Unable to determine secrets passphrase for backend={backend_url}, organization={organization}, "
        f"project={project}, stack={stack}, passphrase_id={passphrase_id}, stalt_state={salt_state}"
      )

  def set_pulumi_secret_passphrase(
        self,
        passphrase: str,
        backend_url: Optional[str]=None,
        organization: Optional[str]=None,
        project: Optional[str]=None,
        stack: Optional[str]=None,
        passphrase_id: Optional[str] = None,
        salt_state: Optional[str] = None,
      ):
    if not salt_state is None:
      self._passphrase_by_salt_state[salt_state] = passphrase
    self._passphrase_by_backend_org_project_stack[(backend_url, organization, project, stack)] = passphrase
    if not passphrase_id is None:
      self._passphrase_by_id[passphrase_id] = passphrase

  def set_pulumi_secret_passphrase_by_id(
        self,
        passphrase: str,
        passphrase_id: str
      ):
    if not passphrase_id is None:
      self._passphrase_by_id[passphrase_id] = passphrase

  def set_pulumi_secret_passphrase_by_salt_state(
        self,
        passphrase: str,
        salt_state: str
      ):
    if not salt_state is None:
      self._passphrase_by_salt_state[salt_state] = passphrase

  def get_pulumi_secret_passphrase(
        self,
        backend_url: Optional[str]=None,
        organization: Optional[str]=None,
        project: Optional[str]=None,
        stack: Optional[str]=None,
        passphrase_id: Optional[str] = None,
        salt_state: Optional[str] = None,
      ) -> str:
    result = None
    if not salt_state is None:
      result = self._passphrase_by_salt_state.get(salt_state, None)
    if result is None:
      result = self._passphrase_by_backend_org_project_stack.get((backend_url, organization, project, stack), None)
    if result is None and not passphrase_id is None:
      result = self._passphrase_by_id.get(passphrase_id, None)
    if result is None and not stack is None:
      result = self._passphrase_by_backend_org_project_stack.get((backend_url, organization, project, None), None)
    if result is None and not project is None:
      result = self._passphrase_by_backend_org_project_stack.get((backend_url, organization, None, None), None)
    if result is None and not organization is None:
      result = self._passphrase_by_backend_org_project_stack.get((backend_url, None, None, None), None)
    if result is None and not backend_url is None:
      result = self._passphrase_by_backend_org_project_stack.get((None, None, None, None), None)
    if result is None:
      result = self.load_pulumi_secret_passphrase(
          backend_url=backend_url,
          organization=organization,
          project=project,
          stack=stack,
          passphrase_id=passphrase_id
        )
    if not (backend_url, organization, project, stack) in self._passphrase_by_backend_org_project_stack:
      self._passphrase_by_backend_org_project_stack[(backend_url, organization, project, stack)] = result
    if not passphrase_id is None and not passphrase_id in self._passphrase_by_id:
      self._passphrase_by_id[passphrase_id] = result
    if not salt_state is None and not salt_state in self._passphrase_by_salt_state:
      self._passphrase_by_salt_state[salt_state] = result
    return result

  def get_pulumi_home(self) -> str:
    if self._pulumi_home is None:
      pulumi_home = self.get_environ().get("PULUMI_HOME", None)
      if pulumi_home is None or pulumi_home == '':
        pulumi_home = "~/.pulumi"
      pulumi_home = self.abspath(pulumi_home)
      self._pulumi_home = pulumi_home
      self.get_environ()["PULUMI_HOME"] = pulumi_home
    return self._pulumi_home

  def set_pulumi_home(self, pulumi_home: str):
    self._pulumi_home = self.abspath(pulumi_home)
    self.get_environ()["PULUMI_HOME"] = self._pulumi_home

  def get_pulumi_cli(self) -> str:
    if self._pulumi_cli is None:
      self._pulumi_cli = os.path.join(self.get_pulumi_home(), 'bin', 'pulumi')
    return self._pulumi_cli

  def get_pulumi_wrapped_cli(self) -> str:
    if self._pulumi_wrapped_cli is None:
      self._pulumi_wrapped_cli = os.path.join(self.get_project_root_dir(), '.venv', 'bin', 'pulumi')
    return self._pulumi_wrapped_cli

  def set_pulumi_cli(self, cli_executable: str):
    self._pulumi_cli = self.abspath(cli_executable)

  def get_pulumi_install_dir(self) -> str:
    return self.get_pulumi_home()

  def abspath(self, pathname: str) -> str:
    result = os.path.abspath(os.path.normpath(os.path.join(self.get_cwd(), os.path.expanduser(pathname))))
    return result

  def get_cwd(self) -> str:
    return self._cwd

  def set_cwd(self, cwd: str):
    self._cwd = self.abspath(cwd)

  def get_environ(self) -> Dict[str, str]:
    if self._environ is None:
      ctx = self
      env = dict(os.environ)
      self._environ = env
      env['PULUMI_HOME'] = ctx.get_pulumi_home()
      project = self.get_optional_project()
      backend: Optional['XPulumiBackend'] = None
      if project is None:
        if 'PULUMI_BACKEND_URL' in env:
          del env['PULUMI_BACKEND_URL']
        if 'PULUMI_ACCESS_TOKEN' in env:
          del env['PULUMI_ACCESS_TOKEN']
      else:
        env['PULUMI_BACKEND_URL'] = project.get_project_backend_url()
        backend = project.backend
        if backend.scheme in [ 'https', 'http' ]:
          env['PULUMI_ACCESS_TOKEN'] = backend.require_access_token()
        else:
          if 'PULUMI_ACCESS_TOKEN' in env:
            del env['PULUMI_ACCESS_TOKEN']
        stack_name = self.get_optional_stack_name()
      if not 'PULUMI_CONFIG_PASSPHRASE' in env:
        passphrase: Optional[str] = None
        if not backend is None:
          try:
            passphrase = ctx.get_pulumi_secret_passphrase(
                backend_url=backend.url,
                organization=None if project is None else project.organization,
                project=None if project is None else project.name,
                stack=stack_name
              )
          except XPulumiError:
            pass
        if passphrase is None:
          try:
            passphrase_v = ctx.get_simple_kv_secret('pulumi/passphrase')
            if not passphrase_v is None and not isinstance(passphrase_v, str):
              raise XPulumiError("secret-kv value is not None or a string: pulumi/passphrase")
            passphrase = passphrase_v
          except Exception:
            pass
        if not passphrase is None:
          env['PULUMI_CONFIG_PASSPHRASE'] = passphrase
    return self._environ

  def _fix_raw_popen_args(self, arglist: List[str], kwargs: Dict[str, Any]) -> List[str]:
    arglist = [ self.get_pulumi_cli() ] + arglist
    env = self.get_environ()
    call_env = kwargs.pop('env', None)
    if not call_env is None:
      env.update(call_env)
    kwargs['env'] = env
    project = self.get_optional_project()
    if project is None:
      cwd = self._cwd
    else:
      cwd = project._project_dir  # pylint: disable=protected-access
    kwargs['cwd'] = cwd
    return arglist

  def raw_pulumi_Popen(self, arglist: List[str], **kwargs) -> subprocess.Popen:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.Popen(arglist, **kwargs)

  def raw_pulumi_check_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.check_call(arglist, **kwargs)

  def raw_pulumi_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.call(arglist, **kwargs)
