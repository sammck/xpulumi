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

class XPulumiProject:
  _ctx: XPulumiContextBase
  _name: Optional[str] = None
  _project_dir: str
  _cfg_data: JsonableDict
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
      rel_dir = os.path.relpath(cwd, os.path.join(ctx.get_infra_dir(), 'project'))
      dparts = os.sep.split(rel_dir)
      if dparts[0] == '..':
        raise XPulumiError(f"Working directory not in an XPulumi project: {cwd}")
      name = dparts[0]
    self._name = name
    project_dir = self._ctx.get_project_infra_dir(name)
    self._project_dir = project_dir
    cfg_file = os.path.join(project_dir, "xpulumi-project.json")
    if not os.path.exists(cfg_file):
      raise XPulumiError(f"XPulumi project does not exist: {name}")
    with open(cfg_file) as f:
      cfg_data = json.load(f)
    self._cfg_data = cfg_data
    pulumi_project_name: Optional[str] = cfg_data.get("pulumi_project_name", None)
    if pulumi_project_name is None:
      pulumi_project_name = name
    self._pulumi_project_name = pulumi_project_name
    organization = cfg_data.get("organization", None)
    self._organization = organization
    backend_name = cfg_data['backend']
    self._backend_name = backend_name
    self._backend = XPulumiBackend(backend_name, ctx=ctx, cwd=project_dir)

  @property
  def ctx(self) -> XPulumiContext:
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
  def organization(self) -> str:
    return self._organization

  @property
  def cfg_data(self) -> JsonableDict:
    return self._cfg_data


  @property
  def abspath(self, pathname: str) -> str:
    return os.path.abspath(os.path.join(self._project_dir, os.path.expanduser(pathname)))

  def get_project_backend_url(self) -> str:
    return self.backend.get_project_backend_url(organization=self.organization, project=self.pulumi_project_name)

  def get_project_backend_url_parts(self, organization: Optional[str]=None, project: Optional[str]=None) -> ParseResult:
    return urlparse(self.get_project_backend_url())

  def get_stack_backend_url(
        self,
        stack: str,
      ) -> str:
    result = self.backend.get_stack_backend_url(stack, organization=self.organization, project=self.pulumi_project_name)

  def get_stack_backend_url_parts(
        self,
        stack: str,
      ) -> ParseResult:
    return urlparse(self.get_stack_backend_url(stack))

  def export_stack(
        self,
        stack: str,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    return self.backend.export_stack(
        self.pulumi_project_name,
        stack,
        organization=self.organization,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def get_stack_outputs(
        self,
        stack: str,
        decrypt_secrets: bool=False,
        bypass_pulumi: bool=True,
      ) -> JsonableDict:
    return self.backend.get_stack_outputs(
        self.pulumi_project_name,
        stack,
        organization=self.organization,
        decrypt_secrets=decrypt_secrets,
        bypass_pulumi=bypass_pulumi
      )

  def __str__(self) -> str:
    return f"<XPulumi project {self.name}>"

