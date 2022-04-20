# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract pulumi project.

Allows the application to work with a particular Pulumi project configuration.

"""

from typing import Optional, cast, Dict, List, TYPE_CHECKING, Set
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
import subprocess

from project_init_tools import file_url_to_pathname, full_name_of_type, full_type, pathname_to_file_url
from .exceptions import XPulumiError
from .context import XPulumiContext
from .base_context import XPulumiContextBase
from .passphrase import PassphraseCipher
from .constants import PULUMI_STANDARD_BACKEND, PULUMI_JSON_SECRET_PROPERTY_NAME, PULUMI_JSON_SECRET_PROPERTY_VALUE
from .backend import XPulumiBackend
import yaml
try:
  from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
  from yaml import Loader, Dumper  #type: ignore[misc]

if TYPE_CHECKING:
  from .stack import XPulumiStack

class XPulumiProject:
  _ctx: XPulumiContextBase
  _name: str
  _project_dir: str
  _xcfg_file: str
  _xcfg_data: JsonableDict
  _cfg_data: JsonableDict
  _pulumi_cfg_file: str
  _pulumi_cfg_data: JsonableDict
  _pulumi_project_name: str
  _backend_name: str
  _backend: XPulumiBackend
  _organization: Optional[str] = None
  _cloud_subaccount: Optional[str] = None
  _stacks: Dict[str, 'XPulumiStack'] = None
  _all_stacks_known: bool = False
  _project_dependencies: List[str]
  _stacks_metadata: Optional[Dict[str, JsonableDict]] = None

  def __init__(
        self,
        name: Optional[str]=None,
        ctx: Optional[XPulumiContextBase]=None,
        cwd: Optional[str]=None
      ):
    self._stacks = {}
    if ctx is None:
      ctx = XPulumiContextBase(cwd=cwd)
    self._ctx = ctx
    if cwd is None:
      cwd = ctx.get_cwd()
    if name is None:
      name = ctx.get_project_name(cwd=cwd)
    assert not name is None
    self._name = name
    project_dir = self._ctx.get_project_infra_dir(name)
    self._project_dir = project_dir
    xcfg_data: Optional[JsonableDict] = None
    cfg_file_json = os.path.join(project_dir, "xpulumi-project.json")
    cfg_file_yaml = os.path.join(project_dir, "xpulumi-project.yaml")
    if os.path.exists(cfg_file_json):
      self._xcfg_file = cfg_file_json
      with open(cfg_file_json, encoding='utf-8') as f:
        xcfg_data = cast(JsonableDict, json.load(f))
    elif os.path.exists(cfg_file_yaml):
      self._xcfg_file = cfg_file_yaml
      with open(cfg_file_yaml, encoding='utf-8') as f:
        xcfg_text = f.read()
      xcfg_data = cast(JsonableDict, yaml.load(xcfg_text, Loader=Loader))
    assert isinstance(xcfg_data, dict)
    self._xcfg_data = xcfg_data
    pulumi_cfg_data: Optional[JsonableDict] = None
    pulumi_cfg_file = os.path.join(project_dir, 'Pulumi.yaml')
    self._pulumi_cfg_file = pulumi_cfg_file
    if os.path.exists(pulumi_cfg_file):
      with open(pulumi_cfg_file, encoding='utf-8') as f:
        pulumi_cfg_text = f.read()
      pulumi_cfg_data = cast(JsonableDict, yaml.load(pulumi_cfg_text, Loader=Loader))
      assert isinstance(pulumi_cfg_data, dict)
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
        poptions = cast(JsonableDict, pulumi_cfg_data['options'])
        assert isinstance(poptions, dict)
        if 'template' in poptions:
          ptemplate = cast(JsonableDict, poptions['template'])
          assert isinstance(ptemplate, dict)
          if 'config' in ptemplate:
            ptconfig = cast(JsonableDict, ptemplate['config'])
            assert isinstance(ptconfig, dict)
            cfg_data['stack_template_config'] = ptconfig
            if 'xpulumi:backend' in ptconfig:
              xpbe = cast(JsonableDict, ptconfig['xpulumi:backend'])
              assert isinstance(xpbe, dict)
              if 'value' in xpbe:
                bev = cast(str, xpbe['value'])
                assert isinstance(bev, str)
                cfg_data['backend'] = bev
    if not xcfg_data is None:
      assert isinstance(xcfg_data, dict)
      cfg_data.update(xcfg_data)
    if not 'pulumi_project_name' in cfg_data:
      cfg_data['pulumi_project_name'] = name
    if not 'project_dependencies' in cfg_data:
      cfg_data['project_dependencies'] = []
    if not 'name' in cfg_data:
      cfg_data['name'] = name
    cfg_data['project_dir'] = project_dir
    self._cfg_data = cfg_data
    cloud_subaccount = cast(Optional[str], cfg_data.get("cloud_subaccount", None))
    if cloud_subaccount is None:
      cloud_subaccount = ctx.get_default_cloud_subaccount()
    self._cloud_subaccount = cloud_subaccount
    pulumi_project_name = cast(Optional[str], cfg_data.get("pulumi_project_name", None))
    assert pulumi_project_name is None or isinstance(pulumi_project_name, str)
    if pulumi_project_name is None:
      pulumi_project_name = name
    self._pulumi_project_name = pulumi_project_name
    organization = cast(Optional[str], cfg_data.get("organization", None))
    assert organization is None or isinstance(organization, str)
    self._organization = organization
    if not 'backend' in cfg_data:
      raise XPulumiError(f"Pulumi project in {project_dir} is not configured with an xpulumi backend")
    backend_name = cast(str, cfg_data['backend'])
    assert isinstance(backend_name, str)
    self._backend_name = backend_name
    self._backend = XPulumiBackend(backend_name, ctx=ctx, cwd=project_dir)
    project_dependencies = cast(Optional[List[str]], cfg_data.get("project_dependencies", None))
    if project_dependencies is None:
      project_dependencies = []
    assert isinstance(project_dependencies, list)
    project_dependencies = project_dependencies[:]
    self._project_dependencies = project_dependencies

  @property
  def ctx(self) -> XPulumiContextBase:
    return self._ctx

  @property
  def name(self) -> str:
    assert isinstance(self._name, str)
    return self._name

  @property
  def backend(self) -> XPulumiBackend:
    return self._backend

  @property
  def cloud_subaccount(self) -> Optional[str]:
    return self._cloud_subaccount

  @property
  def pulumi_project_name(self) -> str:
    return self._pulumi_project_name

  @property
  def project_dir(self) -> str:
    return self._project_dir

  @property
  def organization(self) -> Optional[str]:
    return self._organization

  @property
  def cfg_data(self) -> JsonableDict:
    return self._cfg_data

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
    result = self.backend.get_stack_backend_url(
        self.get_stack_name(stack_name),
        organization=self.organization,
        project=self.pulumi_project_name
      )
    return result

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

  def precreate_project_backend(self) -> None:
    self.backend.precreate_project_backend(organization=self.organization, project=self.pulumi_project_name)

  def get_pulumi_prog(self) -> str:
    return self.ctx.get_pulumi_wrapped_cli()

  def check_call_project_pulumi(self, args: List[str]):
    subprocess.check_call([ self.get_pulumi_prog() ] + args, cwd=self.project_dir)

  def call_project_pulumi(self, args: List[str]) -> int:
    return subprocess.call([ self.get_pulumi_prog() ] + args, cwd=self.project_dir)

  def check_output_project_pulumi(self, args: List[str]) -> str:
    return subprocess.check_output([ self.get_pulumi_prog() ] + args, cwd=self.project_dir).decode('utf-8')

  def get_stacks_metadata(self) -> Dict[str, JsonableDict]:
    if self._stacks_metadata is None:
      result = self.backend.get_stacks_metadata(self.name, organization=self.organization)
      #text = self.check_output_project_pulumi([ 'stack', 'ls', '-j' ])
      #md = json.loads(text)
      #result: Dict[str, JsonableDict] = {}
      #for smd in md:
      #  result[smd['name']] = smd
      self._stacks_metadata = result
    return self._stacks_metadata

  def invalidate_stacks_metadata(self) -> None:
    self._stacks_metadata = None

  def get_stack_names(self) -> List[str]:
    return sorted(self.get_stacks().keys())

  def get_stack(self, stack_name: str, create: bool=False) -> 'XPulumiStack':
    if not create:
      self.get_stacks()
    stack = self._stacks.get(stack_name, None)
    if stack is None:
      if create:
        from .stack import XPulumiStack
        stack = XPulumiStack(stack_name, ctx=self.ctx, project=self)
        self._stacks[stack_name] = stack
      else:
        raise XPulumiError(f"Stack {stack_name} is not known in project {self.name}")
    return stack

  def get_stacks(self) -> Dict[str, 'XPulumiStack']:
    if not self._all_stacks_known:
      md = self.get_stacks_metadata()
      for stack_name in md.keys(): # pylint: disable=consider-iterating-dictionary
        if not stack_name in self._stacks:
          self.get_stack(stack_name, create=True)
      project_files = os.listdir(self.project_dir)
      for project_file in project_files:
        if len(project_file) > len('Pulumi.yaml') + 1 and project_file.startswith('Pulumi.') and project_file.endswith('.yaml'):
          stack_name = project_file[len('Pulumi.'):-len('.yaml')]
          if len(stack_name) > 0:
            if not stack_name in self._stacks:
              self.get_stack(stack_name, create=True)
        elif (len(project_file) > len('xpulumi-stack.yaml') + 1 and
              project_file.startswith('xpulumi-stack.') and (
              project_file.endswith('.yaml') or project_file.endswith('.json'))):
          stack_name = project_file[len('xpulumi-stack.'):-len('.yaml')]
          if len(stack_name) > 0:
            if not stack_name in self._stacks:
              self.get_stack(stack_name, create=True)
    return self._stacks

  def get_inited_stacks(self) -> Dict[str, 'XPulumiStack']:
    md = self.get_stacks_metadata()
    result: Dict[str, 'XPulumiStack'] = {}
    for stack_name in md.keys():  # pylint: disable=consider-iterating-dictionary
      stack = self.get_stack(stack_name, create=True)
      result[stack_name] = stack
    return result

  def get_stack_metadata(self, stack_name: str) -> Optional[JsonableDict]:
    result = self.get_stacks_metadata().get(stack_name, None)
    return result

  def stack_exists(self, stack_name: str) -> bool:
    return stack_name in self.get_stacks()

  def stack_is_inited(self, stack_name: str) -> bool:
    return not self.get_stack_metadata(stack_name) is None

  def get_stack_resource_count(self, stack_name: str) -> int:
    md = self.get_stack_metadata(stack_name)
    result = 0 if md is None else md.get('resourceCount', 0)
    assert isinstance(result, int) and result >= 0
    return result

  def stack_is_deployed(self, stack_name: str) -> bool:
    return self.get_stack_resource_count(stack_name) > 0

  def init_stack(self, stack_name: str):
    if not self.stack_is_inited(stack_name):
      self.invalidate_stacks_metadata()
      self.check_call_project_pulumi([ 'stack', 'init', '-s', stack_name, '--non-interactive' ])

  def get_stack_dependencies(self, stack_name: str) -> List['XPulumiStack']:
    """Get the list of XPulumiStacks that a single stack in this project
       is directly dependent upon. If the backend for this project
       is created by another XPulumiStack, then that XPulumiStack is
       included in the returned list.

       Indirect dependencies are not included.

       The returned list is in no particular order.

    Returns:
        List[XPulumiStack]: a list of stacks that a given stack depends on
    """
    backend = self.backend
    dependency_list: List['XPulumiStack'] = backend.get_stack_dependencies()[:]
    dependency_set: Set[str] = set(x.full_stack_name for x in dependency_list)

    for xstack_name in self._project_dependencies:
      stack = self.ctx.get_stack_from_xstack_name(
          xstack_name,
          default_stack_name=stack_name,
          default_project_name=self.name,
          lone_is_project=True
        )
      if not stack.full_stack_name in dependency_set:
        dependency_list.append(stack)
        dependency_set.add(stack.full_stack_name)
    return dependency_list

  def __str__(self) -> str:
    return f"<XPulumi project {self.name}>"

  def __repr__(self) -> str:
    return f"<XPulumi project {self.name}, id={id(self)}>"
