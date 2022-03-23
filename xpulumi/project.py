# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract pulumi project.

Allows the application to work with a particular Pulumi project configuration.

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
import yaml
try:
  from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
  from yaml import Loader, Dumper  #type: ignore[misc]

class XPulumiProject:
  _ctx: XPulumiContextBase
  _name: Optional[str] = None
  _project_dir: str
  _xcfg_file: str
  _xcfg_data: JsonableDict
  _cfg_data: JsonableDict
  _pulumi_cfg_file: str
  _pulumi_cfg_data: JsonableDict
  _pulumi_project_name: str
  _backend_name: str
  _backend: XPulumiBackend

  def __init__(
        self,
        name: Optional[str]=None,
        ctx: Optional[XPulumiContextBase]=None,
        cwd: Optional[str]=None
      ):
    if ctx is None:
      ctx = XPulumiContextBase(cwd=cwd)
    self._ctx = ctx
    if cwd is None:
      cwd = ctx.get_cwd()
    if name is None:
      name = ctx.get_project_name(cwd=cwd)
    self._name = name
    project_dir = self._ctx.get_project_infra_dir(name)
    self._project_dir = project_dir
    xcfg_data: Jsonable = None
    cfg_file_json = os.path.join(project_dir, "xpulumi-project.json")
    cfg_file_yaml = os.path.join(project_dir, "xpulumi-project.yaml")
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
    pulumi_cfg_file = os.path.join(project_dir, 'Pulumi.yaml')
    self._pulumi_cfg_file = pulumi_cfg_file
    if os.path.exists(pulumi_cfg_file):
      with open(pulumi_cfg_file) as f:
        pulumi_cfg_text = f.read()
      pulumi_cfg_data = yaml.load(pulumi_cfg_text, Loader=Loader)
      self._pulumi_cfg_data = pulumi_cfg_data

    if xcfg_data is None and pulumi_cfg_data is None:
      raise XPulumiError(f"XPulumi project does not exist: {name}")
    cfg_data: JsonableDict = {}
    if not pulumi_cfg_data is None:
      cfg_data['pulumi_config'] = deepcopy(pulumi_cfg_data)
      if 'name' in pulumi_cfg_data:
        cfg_data['pulumi_project_name'] = pulumi_cfg_data['name']
      if 'description' in pulumi_cfg_data:
        cfg_data['description'] = pulumi_cfg_data['description']
      if 'url' in pulumi_cfg_data:
        cfg_data['pulumi_resolved_backend_url'] = pulumi_cfg_data['url']
      if 'options' in pulumi_cfg_data:
        poptions: JsonableDict = pulumi_cfg_data['options']
        if 'template' in poptions:
          ptemplate: JsonableDict = poptions['template']
          if 'config' in ptemplate:
            ptconfig = ptemplate['config']
            cfg_data['stack_template_config'] = ptconfig
            if 'xpulumi:backend' in ptconfig:
              xpbe: JsonableDict = ptconfig['xpulumi:backend']
              if 'value' in xpbe:
                cfg_data['backend'] = xpbe['value'] 
    if not xcfg_data is None:
      cfg_data.update(xcfg_data)
    if not 'pulumi_project_name' in cfg_data:
      cfg_data['pulumi_project_name'] = name
    if not 'name' in cfg_data:
      cfg_data['name'] = name
    cfg_data['project_dir'] = project_dir
    self._cfg_data = cfg_data
    pulumi_project_name: Optional[str] = cfg_data["pulumi_project_name"]
    self._pulumi_project_name = pulumi_project_name
    organization = cfg_data.get("organization", None)
    self._organization = organization
    if not 'backend' in cfg_data:
      raise XPulumiError(f"Pulumi project in {project_dir} is not configured with an xpulumi backend")
    backend_name = cfg_data['backend']
    self._backend_name = backend_name
    self._backend = XPulumiBackend(backend_name, ctx=ctx, cwd=project_dir)

  @property
  def ctx(self) -> XPulumiContextBase:
    return self._ctx

  @property
  def name(self) -> str:
    return self._name

  @property
  def backend(self) -> XPulumiBackend:
    return self._backend

  @property
  def pulumi_project_name(self) -> str:
    return self._pulumi_project_name

  @property
  def project_dir(self) -> str:
    return self._project_dir

  @property
  def organization(self) -> str:
    return self._organization

  @property
  def cfg_data(self) -> JsonableDict:
    return self._cfg_data


  @property
  def abspath(self, pathname: str) -> str:
    return os.path.abspath(os.path.join(self._project_dir, os.path.expanduser(pathname)))

  def get_optional_stack_name(self, stack_name: Optional[str]=None) -> Optional[str]:
    return self.ctx.get_optional_stack_name(stack_name)

  def get_stack_name(self, stack_name: Optional[str]=None) -> str:
    return self.ctx.get_stack_name(stack_name)

  def get_project_backend_url(self) -> str:
    return self.backend.get_project_backend_url(organization=self.organization, project=self.pulumi_project_name)

  def get_project_backend_url_parts(self, organization: Optional[str]=None, project: Optional[str]=None) -> ParseResult:
    return urlparse(self.get_project_backend_url())

  def get_stack_backend_url(
        self,
        stack_name: Optional[str]=None,
      ) -> str:
    result = self.backend.get_stack_backend_url(self.get_stack_name(stack_name), organization=self.organization, project=self.pulumi_project_name)

  def get_stack_backend_url_parts(
        self,
        stack_name: Optional[str]=None,
      ) -> ParseResult:
    return urlparse(self.get_stack_backend_url(stack_name))

  def export_stack(
        self,
        stack_name: Optional[str]=None,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    stack_name = self.get_stack_name(stack_name)
    return self.backend.export_stack(
        self.pulumi_project_name,
        stack_name,
        organization=self.organization,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def get_stack_outputs(
        self,
        stack_name: Optional[str]=None,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    stack_name = self.get_stack_name(stack_name)
    return self.backend.get_stack_outputs(
        self.pulumi_project_name,
        stack_name,
        organization=self.organization,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def __str__(self) -> str:
    return f"<XPulumi project {self.name}>"

