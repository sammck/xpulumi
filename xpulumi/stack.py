# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract pulumi stack.

Allows the application to work with a particular Pulumi stack configuration.

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
from .backend import XPulumiBackend
from .project import XPulumiProject

import yaml
try:
  from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
  from yaml import Loader, Dumper  #type: ignore[misc]

class XPulumiStack:
  _project: XPulumiProject
  _stack_name: Optional[str] = None
  _xcfg_file: str
  _xcfg_data: JsonableDict
  _cfg_data: JsonableDict
  _pulumi_cfg_file: str
  _pulumi_cfg_data: JsonableDict

  def __init__(
        self,
        stack_name: Optional[str]=None,
        ctx: Optional[XPulumiContextBase]=None,
        cwd: Optional[str]=None,
        project: Optional[XPulumiProject]=None,
        project_name: Optional[str]=None,
      ):
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
      project = XPulumiProject(project_name, ctx=ctx, cwd=cwd)
    if project_name is None:
      project_name = project.name
    else:
      if project_name != project.name:
        raise XPulumiError(f"project_name \"{project_name}\" conflicts provided XPulumiProject name \"{project.name}")
    ctx = project.ctx
    cwd = project.project_dir
    stack_name = project.get_stack_name(stack_name)

    self._project = project
    self._stack_name = stack_name

    project_dir = project.project_dir
    xcfg_data: Jsonable = None
    cfg_file_json = os.path.join(project_dir, f"xpulumi-stack.{stack_name}.json")
    cfg_file_yaml = os.path.join(project_dir, f"xpulumi-stack.{stack_name}.yaml")
    if os.path.exists(cfg_file_json):
      self._xcfg_file = cfg_file_json
      with open(cfg_file_json) as f:
        xcfg_data = json.load(f)
    elif os.path.exists(cfg_file_yaml):
      self._xcfg_file = cfg_file_yaml
      with open(cfg_file_yaml) as f:
        xcfg_text = f.read()
      xcfg_data = yaml.load(xcfg_text, Loader=Loader)
    self._xcfg_data = xcfg_data
    pulumi_cfg_data: Jsonable = None
    pulumi_cfg_file = os.path.join(project_dir, f'Pulumi.{stack_name}.yaml')
    self._pulumi_cfg_file = pulumi_cfg_file
    if os.path.exists(pulumi_cfg_file):
      with open(pulumi_cfg_file) as f:
        pulumi_cfg_text = f.read()
      pulumi_cfg_data = yaml.load(pulumi_cfg_text, Loader=Loader)
      self._pulumi_cfg_data = pulumi_cfg_data

    cfg_data: JsonableDict = {}
    if not pulumi_cfg_data is None:
      cfg_data['pulumi_config'] = deepcopy(pulumi_cfg_data)
    if not xcfg_data is None:
      cfg_data.update(xcfg_data)
    cfg_data['project_dir'] = project_dir
    self._cfg_data = cfg_data

  @property
  def ctx(self) -> XPulumiContextBase:
    return self.project.ctx

  @property
  def project(self) -> XPulumiProject:
    return self._project

  @property
  def stack_name(self) -> str:
    return self._stack_name

  @property
  def project_name(self) -> str:
    return self.project.name

  @property
  def full_stack_name(self) -> str:
    return f"{self.project_name}:{self.stack_name}"

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
  def organization(self) -> str:
    return self.project.organization

  @property
  def cfg_data(self) -> JsonableDict:
    return self._cfg_data

  @property
  def abspath(self, pathname: str) -> str:
    return self.project.abspath(pathname)

  def get_project_backend_url(self) -> str:
    return self.project.get_project_backend_url()

  def get_stack_backend_url(self) -> str:
    result = self.project.get_stack_backend_url(self.stack_name)

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
    return self.project.get_stack_outputs(
        self.stack_name,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def get_pulumi_config(self) -> JsonableDict:
    result = self._pulumi_cfg_data
    if result is None:
      result = {}
    return result

  def get_config_values(self) -> JsonableDict:
    pc = self.get_pulumi_config()
    result: JsonableDict = pc.get('config', {})
    return result

  def get_config_value(self, name: str, default: Jsonable=None) -> Jsonable:
    if name.startswith(':'):
      name = self.pulumi_project_name + name
    result: Jsonable = self.get_config_values().get(name, default)
    return result

  def require_config_value(self, name: str) -> Jsonable:
    if name.startswith(':'):
      name = self.pulumi_project_name + name
    result: Jsonable = self.get_config_values()[name]
    return result

  def get_aws_region(self, default: Optional[str]=None) -> Optional[str]:
    result: Optional[str] = self.get_config_value('aws:region', default=default)
    return result

  def __str__(self) -> str:
    return f"<XPulumi stack {self.full_stack_name}>"
