# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Base context implementation for working with Pulumi.

Allows the application to provide certain requirements such as passphrases, defaults, etc.
on demand.

"""

from typing import Optional, cast, Dict, Tuple, List, Callable, Union, Any
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

from .context import XPulumiContext, BotoAwsSession, BotocoreSession
from .util import file_url_to_pathname
from .exceptions import XPulumiError
from .constants import PULUMI_STANDARD_BACKEND
from .config import XPulumiConfig

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
    result = s._xpulumi_caller_dentity  # type: ignore[attr-defined]
  else:
    sts = s.client('sts')
    result = sts.get_caller_identity()
    s._xpulumi_caller_identity = result  # type: ignore[attr-defined]
  return result

def get_aws_account(s: BotoAwsSession) -> str:
  return get_aws_identity(s)['Account']

class XPulumiContextBase(XPulumiContext):

  XPULUMI_INFRA_DIRNAME = 'xpulumi.d'

  _aws_account_region_map: Dict[Tuple[Optional[str], Optional[str]], BotoAwsSession]
  """Maps an aws account name and region to an AWS session"""

  _environ: Dict[str, str]
  """local copy of environment variables that can be overridden"""

  _cwd: str
  """Working directory for this context"""

  _pulumi_cli: Optional[str] = None
  """Location of Pulumi CLI program. By default, located in PATH."""

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

  def __init__(self, config: Optional[XPulumiConfig]=None, cwd: Optional[str]=None):
    super().__init__()
    self._aws_account_region_map = {}
    self._environ = dict(os.environ)
    self._cwd = os.getcwd() if cwd is None else os.path.abspath(os.path.expanduser(cwd))
    self._passphrase_by_backend_org_project_stack = {}
    self._passphrase_by_id = {}
    self._passphrase_by_salt_state = {}
    self._access_token_map = {}
    if not config is None:
      self.init_from_config(config)

  def init_from_config(self, config: XPulumiConfig) -> None:
    self._config = config
    self._project_root_dir = config.project_root_dir

  def get_config(self) -> XPulumiConfig:
    if self._config is None:
      config = XPulumiConfig(starting_dir=self._cwd)
      self.init_from_config(config)
    return self._config

  def get_project_root_dir(self) -> str:
    if self._project_root_dir is None:
      self.get_config()
    return self._project_root_dir

  def get_project_root_dir(self) -> str:
    if self._project_root_dir is None:
      self.get_config()
    return self._project_root_dir

  def get_infra_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), self.XPULUMI_INFRA_DIRNAME)

  def get_backend_infra_dir(self, backend: str) -> str:
    return os.path.join(self.get_infra_dir(), 'backend', backend)

  def get_project_infra_dir(self, project: str) -> str:
    return os.path.join(self.get_infra_dir(), 'project', project)


  def load_aws_session(
        self,
        aws_account: Optional[str]=None,
        aws_region: Optional[str]=None
      ) -> BotoAwsSession:
    # TODO: Find a profile in the desired AWS account. For now, just use the default profile
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

  def get_environ(self) -> Dict[str, str]:
    return self._environ

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
    result: JsonableDict
    if self._credentials_data is None:
      credentials_file = self.get_credentials_filename()
      if not credentials_file is None:
        try:
          with open(credentials_file) as f:
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
    raise XPulumiError(f"Unable to determine secrets passphrase for backend={backend_url}, organization={organization}, project={project}, stack={stack}, passphrase_id={passphrase_id}, stalt_state={salt_state}")

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
    result = self.get_environ().get("PULUMI_HOME", None)
    if result is None or result == '':
      result = "~/.pulumi"
    result = self.abspath(result)
    return result

  def set_pulumi_home(self, pulumi_home: str):
    pulumi_home = self.abspath(pulumi_home)
    self.get_environ()["PULUMI_HOME"] = pulumi_home

  def get_pulumi_cli(self) -> str:
    if self._pulumi_cli is None:
      self._pulumi_cli = distutils.spawn.find_executable('pulumi')
      if self._pulumi_cli is None:
        raise XPulumiError("Unable to locate pulumi CLI executable in PATH")
    return self._pulumi_cli

  def set_pulumi_cli(self, cli_executable: str):
    self._pulumi_cli = self.abspath(cli_executable)

  def get_pulumi_install_dir(self) -> str:
    return self.get_pulumi_home()

  def abspath(self, pathname: str) -> str:
    result = os.path.abspath(os.path.join(self.get_cwd(), os.path.expanduser(pathname)))
    return result

  def get_cwd(self) -> str:
    return self._cwd

  def set_cwd(self, cwd: str):
    self._cwd = self.abspath(cwd)

