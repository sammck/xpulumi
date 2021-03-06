# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract pulumi stack.

Allows the application to work with a particular Pulumi stack configuration.

"""

from typing import Optional, cast, Dict, Tuple, Union, List, Set, Mapping
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
from threading import Lock

from project_init_tools import file_url_to_pathname, full_name_of_type, full_type, pathname_to_file_url
from .exceptions import XPulumiError
from .context import XPulumiContext
from .base_context import XPulumiContextBase
from .passphrase import PassphraseCipher
from .constants import PULUMI_STANDARD_BACKEND, PULUMI_JSON_SECRET_PROPERTY_NAME, PULUMI_JSON_SECRET_PROPERTY_VALUE
from .backend import XPulumiBackend
from .project import XPulumiProject

import yaml
try:
  from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
  from yaml import Loader, Dumper  #type: ignore[misc]

def parse_stack_name(
      stack_name: Optional[str]=None,
      project_name: Optional[str]=None,
      project: Optional[XPulumiProject]=None,
      ctx: Optional[XPulumiContextBase]=None,
      cwd: Optional[str]=None,
      default_stack_name: Optional[str]=None,
      default_project_name: Optional[str]=None,
    ) -> Tuple[str, str]:
  if stack_name == '':
    stack_name = None
  if project_name == '':
    project_name = None
  if not stack_name is None:
    # support fully qualified "<project>:<stack>" stack name
    if ':' in stack_name:
      stack_name_parts = stack_name.split(':')
      if len(stack_name_parts) != 2:
        raise XPulumiError(f"Malformed stack name: {stack_name}")
      if stack_name_parts[0] != '':
        if not project_name is None and project_name != stack_name_parts[0]:
          raise XPulumiError(f"project_name \"{project_name}\" conflicts with fully qualified stack name \"{stack_name}")
        if not project is None and project.name != stack_name_parts[0]:
          raise XPulumiError(f"project name \"{project.name}\" conflicts with fully qualified stack name \"{stack_name}")
        project_name = stack_name_parts[0]
      stack_name = None if stack_name_parts[1] == '' else stack_name_parts[1]
  if project_name is None and project is None:
    project_name = default_project_name
  if project_name is None:
    if project is None:
      if ctx is None:
        ctx = XPulumiContextBase(cwd=cwd)
      project_name = ctx.get_project_name(cwd=cwd)
    else:
      project_name = project.name
  else:
    if not project is None and project_name != project.name:
      raise XPulumiError(f"project_name \"{project_name}\" conflicts provided XPulumiProject name \"{project.name}")
  if stack_name is None:
    stack_name = default_stack_name
    if stack_name is None:
      if ctx is None:
        ctx = XPulumiContextBase(cwd=cwd)
      stack_name = ctx.get_stack_name()
      if stack_name is None:
        raise XPulumiError("No stack name provided and no default exists")
  return project_name, stack_name

class XPulumiStack:
  _project: XPulumiProject
  _stack_name: Optional[str] = None
  _xcfg_file: str
  _xcfg_data: JsonableDict
  _cfg_data: JsonableDict
  _pulumi_cfg_file: str
  _pulumi_cfg_data: Optional[JsonableDict] = None
  _cached_stack_outputs_lock: Lock
  _cached_stack_outputs: Dict[Tuple[bool, bool], JsonableDict]
  _cloud_subaccount: Optional[str] = None
  _include_in_all_up: bool = True
  _include_in_destroy_all: bool = True
  _decrypted_pulumi_config_values: Optional[JsonableDict] = None

  def __init__(
        self,
        stack_name: Optional[str]=None,
        ctx: Optional[XPulumiContextBase]=None,
        cwd: Optional[str]=None,
        project: Optional[XPulumiProject]=None,
        project_name: Optional[str]=None,
        default_stack_name: Optional[str]=None,
        default_project_name: Optional[str]=None,
      ):
    self._cached_stack_outputs_lock = Lock()
    self._cached_stack_outputs = {}
    if stack_name == '':
      stack_name = None
    if project_name == '':
      project_name = None
    if not stack_name is None:
      # support fully qualified "<project>:<stack>" stack name
      if ':' in stack_name:
        stack_name_parts = stack_name.split(':')
        if len(stack_name_parts) != 2:
          raise XPulumiError(f"Malformed stack name: {stack_name}")
        if stack_name_parts[0] != '':
          if not project_name is None and project_name != stack_name_parts[0]:
            raise XPulumiError(f"project_name \"{project_name}\" conflicts with fully qualified stack name \"{stack_name}")
          if not project is None and project.name != stack_name_parts[0]:
            raise XPulumiError(f"project name \"{project.name}\" conflicts with fully qualified stack name \"{stack_name}")
          project_name = stack_name_parts[0]
        stack_name = None if stack_name_parts[1] == '' else stack_name_parts[1]
    if project is None:
      if project_name is None:
        project_name = default_project_name
      project = XPulumiProject(project_name, ctx=ctx, cwd=cwd)
      project_name = project.name
    else:
      if project_name is None:
        project_name = project.name
      elif project_name != project.name:
        raise XPulumiError(f"project_name \"{project_name}\" conflicts with provided XPulumiProject name \"{project.name}")
    ctx = project.ctx
    cwd = project.project_dir
    if stack_name is None:
      stack_name = default_stack_name
    stack_name = project.get_stack_name(stack_name)

    self._project = project
    self._stack_name = stack_name

    project_dir = project.project_dir
    xcfg_data: Optional[JsonableDict] = None
    cfg_file_json = os.path.join(project_dir, f"xpulumi-stack.{stack_name}.json")
    cfg_file_yaml = os.path.join(project_dir, f"xpulumi-stack.{stack_name}.yaml")
    if os.path.exists(cfg_file_json):
      self._xcfg_file = cfg_file_json
      with open(cfg_file_json, encoding='utf-8') as f:
        xcfg_data = cast(JsonableDict, json.load(f))
    elif os.path.exists(cfg_file_yaml):
      self._xcfg_file = cfg_file_yaml
      with open(cfg_file_yaml, encoding='utf-8') as f:
        xcfg_text = f.read()
      xcfg_data = cast(JsonableDict, yaml.load(xcfg_text, Loader=Loader))
    if xcfg_data is None:
      xcfg_data = {}
    assert isinstance(xcfg_data, dict)
    self._xcfg_data = xcfg_data
    pulumi_cfg_data: Optional[JsonableDict] = None
    pulumi_cfg_file = os.path.join(project_dir, f'Pulumi.{stack_name}.yaml')
    self._pulumi_cfg_file = pulumi_cfg_file
    if os.path.exists(pulumi_cfg_file):
      with open(pulumi_cfg_file, encoding='utf-8') as f:
        pulumi_cfg_text = f.read()
      pulumi_cfg_data = cast(JsonableDict, yaml.load(pulumi_cfg_text, Loader=Loader))
      assert isinstance(pulumi_cfg_data, dict)
      self._pulumi_cfg_data = pulumi_cfg_data

    cfg_data: JsonableDict = {}
    if not pulumi_cfg_data is None:
      cfg_data['pulumi_config'] = deepcopy(pulumi_cfg_data)
    if not xcfg_data is None:
      cfg_data.update(xcfg_data)
    cfg_data['project_dir'] = project_dir
    self._cfg_data = cfg_data
    cloud_subaccount = cast(Optional[str], cfg_data.get('cloud_subaccount', None))
    if cloud_subaccount is None:
      cloud_subaccount = project.cloud_subaccount
    self._cloud_subaccount = cloud_subaccount
    include_in_all_up = cast(bool, cfg_data.get("include_in_all_up", True))
    assert isinstance(include_in_all_up, bool)
    self._include_in_all_up = include_in_all_up and project.include_in_all_up
    include_in_destroy_all = cast(bool, cfg_data.get("include_in_destroy_all", True))
    assert isinstance(include_in_destroy_all, bool)
    self._include_in_destroy_all = include_in_destroy_all and project.include_in_destroy_all

  @property
  def include_in_all_up(self) -> bool:
    return self._include_in_all_up

  @property
  def include_in_destroy_all(self) -> bool:
    return self._include_in_destroy_all

  @property
  def ctx(self) -> XPulumiContextBase:
    return self.project.ctx

  @property
  def project(self) -> XPulumiProject:
    return self._project

  @property
  def stack_name(self) -> str:
    assert not self._stack_name is None
    return self._stack_name

  @property
  def project_name(self) -> str:
    return self.project.name

  @property
  def full_stack_name(self) -> str:
    return f"{self.project_name}:{self.stack_name}"

  @property
  def cloud_subaccount(self) -> Optional[str]:
    return self._cloud_subaccount

  @property
  def backend(self) -> XPulumiBackend:
    return self.project.backend

  @property
  def pulumi_project_name(self) -> str:
    return self.project.pulumi_project_name

  @property
  def project_dir(self) -> str:
    return self.project.project_dir

  @property
  def organization(self) -> Optional[str]:
    return self.project.organization

  @property
  def cfg_data(self) -> JsonableDict:
    return self._cfg_data

  def abspath(self, pathname: str) -> str:
    return self.project.abspath(pathname)

  def get_project_backend_url(self) -> str:
    return self.project.get_project_backend_url()

  def get_stack_backend_url(self) -> str:
    result = self.project.get_stack_backend_url(self.stack_name)
    return result

  def get_stack_backend_url_parts(self) -> ParseResult:
    return self.project.get_stack_backend_url_parts(self.stack_name)

  def export_stack(
        self,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    return self.project.export_stack(
        self.stack_name,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def get_stack_outputs(
        self,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    with self._cached_stack_outputs_lock:
      result = self._cached_stack_outputs.get((decrypt_secrets, bypass_pulumi))
      if result is None:
        result = self.project.get_stack_outputs(
            self.stack_name,
            decrypt_secrets=decrypt_secrets,
            bypass_pulumi=bypass_pulumi
          )
        self._cached_stack_outputs[(decrypt_secrets, bypass_pulumi)] = result
    return result

  def pulumi_config_exists(self) -> bool:
    return not self._pulumi_cfg_data is None

  def is_initable(self) -> bool:
    """Returns True if it is allowed to create this stack in the pulumi backend"""
    return self.project.deployment_of_stack_is_allowed(self.stack_name)

  def is_deployable(self) -> bool:
    """Returns True if a pulumi config exists for this stack and the project
    allows deployment of this stack."""
    return self.pulumi_config_exists() and self.is_initable()

  def get_pulumi_config(self) -> JsonableDict:
    result = self._pulumi_cfg_data
    if result is None:
      result = {}
    return result

  def get_decrypted_config_values(self) -> JsonableDict:
    if self._decrypted_pulumi_config_values is None:
      # avoid slow passphrase hash generation if there are not
      # any secrets in this stack's config
      unencrypted = self.get_config_values(decrypt_secrets=False)
      has_encrypted = False
      for v in unencrypted.values():
        if isinstance(v, dict) and 'secure' in v:
          has_encrypted = True
          break
      if has_encrypted:
        decrypted_text = self.check_output_stack_pulumi(['config', '--show-secrets', '-j'])
        decrypted_data = cast(JsonableDict, json.loads(decrypted_text))
        assert isinstance(decrypted_data, dict)
        result: JsonableDict = {}
        for k, v in decrypted_data.items():
          assert isinstance(v, dict) and 'value' in v
          result[k] = v['value']
      else:
        result = deepcopy(unencrypted)
      self._decrypted_pulumi_config_values = result
    return self._decrypted_pulumi_config_values

  def get_config_values(self, decrypt_secrets: bool=True) -> JsonableDict:
    if decrypt_secrets:
      result = self.get_decrypted_config_values()
    else:
      pc = self.get_pulumi_config()
      result = cast(JsonableDict, pc.get('config', {}))
      assert isinstance(result, dict)
    return result

  def get_config_value(self, name: str, default: Jsonable=None, decrypt_secrets: bool=True) -> Jsonable:
    if name.startswith(':'):
      name = self.pulumi_project_name + name
    # we avoid fetching decrypted values unless necessary
    unencrypted = self.get_config_values(decrypt_secrets=False)
    result: Jsonable = unencrypted.get(name, None)
    if result is None:
      result = default
    elif decrypt_secrets and isinstance(result, dict) and 'secure' in result:
      result = self.get_decrypted_config_values().get(name, default)
    return result

  def require_config_value(self, name: str, decrypt_secrets: bool=True) -> Jsonable:
    if name.startswith(':'):
      name = self.pulumi_project_name + name
    # we avoid fetching decrypted values unless necessary
    unencrypted = self.get_config_values(decrypt_secrets=False)
    if not name in unencrypted:
      raise KeyError(f"Pulumi configuration property '{name}' does not exist in stack {self.full_stack_name}")
    result: Jsonable = unencrypted[name]
    if decrypt_secrets and isinstance(result, dict) and 'secure' in result:
      result = self.get_decrypted_config_values()[name]
    return result

  def get_aws_region(self, default: Optional[str]=None) -> Optional[str]:
    result: Optional[str] = cast(Optional[str], self.get_config_value('aws:region', default=default))
    assert result is None or isinstance(result, str)
    return result

  def get_stack_dependencies(self) -> List['XPulumiStack']:
    project = self.project
    result = project.get_stack_dependencies(self.stack_name)
    return result

  def get_stack_build_order(self, include_self: bool=False) -> List['XPulumiStack']:
    return self.ctx.get_stack_build_order(self, include_self=include_self)

  def get_stack_destroy_order(self, include_self=False) -> List['XPulumiStack']:
    return self.ctx.get_stack_destroy_order(self, include_self=include_self)

  def get_stack_metadata(self) -> Optional[JsonableDict]:
    return self.project.get_stack_metadata(self.stack_name)

  def is_inited(self) -> bool:
    return self.project.stack_is_inited(self.stack_name)

  def init_stack(self) -> None:
    return self.project.init_stack(self.stack_name)

  def is_deployed(self) -> bool:
    return self.project.stack_is_deployed(self.stack_name)

  def get_resource_count(self) -> int:
    return self.project.get_stack_resource_count(self.stack_name)

  def __str__(self) -> str:
    return f"<XPulumi stack {self.full_stack_name}>"

  def __repr__(self) -> str:
    return f"<XPulumi stack {self.full_stack_name}, id={id(self)}>"

  def check_call_stack_pulumi(
        self,
        args: List[str],
        env: Optional[Mapping[str, str]] = None,
      ) -> None:
    self.project.check_call_project_pulumi(args, env=env, stack_name=self.stack_name)

  def call_stack_pulumi(
        self,
        args: List[str],
        env: Optional[Mapping[str, str]] = None,
      ) -> int:
    return self.project.call_project_pulumi(args, env=env, stack_name=self.stack_name)

  def check_output_stack_pulumi(
        self,
        args: List[str],
        env: Optional[Mapping[str, str]] = None,
      ) -> str:
    return self.project.check_output_project_pulumi(args, env=env, stack_name=self.stack_name)
