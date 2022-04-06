#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for standard Pulumi CLI that passes xpulumi envionment forward"""

import argparse
import base64
from copy import deepcopy
import json
from lib2to3.pgen2.token import OP
import os
import subprocess
import sys
from base64 import b64decode, b64encode
from io import StringIO, TextIOWrapper
from pathlib import Path
from typing import (Any, Dict, Iterable, Iterator, List, Mapping,
                    MutableMapping, Optional, Sequence, TextIO, Tuple, Union,
                    cast, Set)
from urllib.parse import ParseResult, urlparse

import argcomplete  # type: ignore[import]
import boto3
import boto3.session
import colorama  # type: ignore[import]
import ruamel.yaml # type: ignore[import]
import yaml
import yaml.parser
from mypy_boto3_s3 import Client as S3Client, ServiceResource as S3Resource
from botocore.exceptions import ClientError
from colorama import Back, Fore, Style
from secret_kv import create_kv_store, get_kv_store_passphrase
from xpulumi.backend import XPulumiBackend
from xpulumi.project import XPulumiProject
from xpulumi.base_context import XPulumiContextBase
from xpulumi.config import XPulumiConfig
from xpulumi.constants import (XPULUMI_CONFIG_DIRNAME,
                               XPULUMI_CONFIG_FILENAME_BASE)
from xpulumi.context import XPulumiContext
from xpulumi.exceptions import XPulumiError
from xpulumi.installer.util import file_contents
# NOTE: this module runs with -m; do not use relative imports
from xpulumi.internal_types import JsonableTypes

try:
  from yaml import CDumper as YamlDumper
  from yaml import CLoader as YamlLoader
except ImportError:
  from yaml import Loader as YamlLoader, Dumper as YamlDumper  # type: ignore[misc]

class CmdExitError(RuntimeError):
  exit_code: int

value_options: Dict[str, List[str]] = {
    "":  [ '--color', '-C', '--cwd', '--profiling', '--tracing', '-v', '--verbose'],
    "cancel":  [ '-s', '--stack' ],
    "config":  [ '--config-file', '-s', '--stack' ],
    "console":  [ '-s', '--stack' ],
    "destroy":  [
        '--config-file', '-m', '--message', '-p', '--parallel', '-r',
        '--refresh', '-s', '--stack', '--suppress-permalink',
        '-t', '--target'
      ],

    "import":  [
        '--config-file', '-f', '--file', '-m', '--message', '-o', '--out', '-p', '--parallel', '--parent',
        '--properties', '--provider', '-s', '--stack', '--suppress-permalink'
      ],

    "login":  ['-c', '--cloud-url', '--default-org'],
    "logout":  ['-c', '--cloud-url'],
    "logs":  [
        '--config-file', '-r', '--resource', '--since', '-s', '--stack'
      ],
    "new":  [ '-c', '--config', '-d', '--description', '--dir', '-n', '--name', '--secrets-provider', '-s', '--stack' ],
    "org":  [ ],
    "org get-default":  [ ],
    "org set-default":  [ ],
    "plugin":  [ ],
    "plugin install":  [ '-f', '--file', '--server' ],
    "plugin ls":  [ ],
    "plugin rm":  [ ],
    "policy":  [ ],
    "policy disable":  [ '--policy-group', '--version' ],
    "policy enable":  [ '--config', '--policy-group' ],
    "policy group":  [ ],
    "policy group ls":  [ ],
    "policy ls":  [ ],
    "policy new":  [ '--dir' ],
    "policy publish":  [ ],
    "policy rm":  [ ],
    "policy validate config":  [ '--config' ],
    "preview":  [
        '-c', '--config', '--config-file', '-m', '--message', '-p', '--parallel', '--policy-pack', '--policy-pack-config', '-r',
        '--refresh', '--replace', '-s', '--stack', '--suppress-permalink', '-t', '--target', '--target-replace'
      ],
    "refresh":  [
        '--config-file', '-m', '--message', '-p', '--parallel', '-s', '--stack',
        '--suppress-permalink', '-t', '--target'
      ],
    "schema":  [ ],
    "schema check":  [ ],
    "stack":  [ '-s', '--stack' ],
    "stack change-secrets-provider":  [ ],
    "stack export":  [ '--file' ],
    "stack graph":  [ '--dependency-edge-color', '--parent-edge-color' ],
    "stack history":  [ '--page', '--page-size' ],
    "stack import":  [ '--file' ],
    "stack init":  [ '--copy-config-from', '--secrets-provider' ],
    "stack ls":  [ '-o', '--organization', '-p', '--project', '-t', '--tag' ],
    "stack output":  [  ],
    "stack rename":  [  ],
    "stack rm":  [  ],
    "stack select":  [ '--secrets-provider' ],
    "stack tag":  [ ],
    "stack tag get":  [ ],
    "stack tag ls":  [ ],
    "stack tag rm":  [ ],
    "stack tag set":  [ ],
    "state":  [ ],
    "state delete":  [ '-s', '--stack' ],
    "state rename":  [ '-s', '--stack' ],
    "state unprotect":  [ '-s', '--stack' ],
    "up":  [
        '-c', '--config', '--config-file', '-m', '--message', '-p', '--parallel', '--policy-pack', '--policy-pack-config', '-r',
        '--refresh', '--replace', '--secrets-provider', '-s', '--stack', '--suppress-permalink', '-t', '--target', '--target-replace'
      ],
    "version":  [ ],
    "watch":  [
        '-c', '--config', '--config-file', '-m', '--message', '-p', '--parallel', '--path',
        '--policy-pack', '--policy-pack-config', '--secrets-provider', '-s', '--stack'
      ],
    "whoami":  [ ],
  }

value_options_set: Set[str] = set()
for olist in value_options.values():
  value_options_set.update(olist)

value_options_list = sorted(value_options_set)

class CmdOption:
  option: str
  value: Optional[str] = None

  def __init__(self, option: str, value: Optional[str]=None):
    self.option = option
    self.value = value

  def to_cmd_args(self) -> List[str]:
    result: List[str] = [ self.option ]
    if not self.value is None:
      result.append(self.value)
    return result

  def __str__(self) -> str:
    return ' '.join(self.to_cmd_args())

  def __repr__(self) -> str:
    return f"<CmdOption {str(self)}>"

class PulumiCmd:
  raw_args: List[str]
  tokens: List[Union[str, CmdOption]]
  pos_args: List[str]
  options: List[CmdOption]
  subcmd: str
  subcmd_valid_options: Set[str]

  def __init__(self, args: List[str]):
    self.init_from_raw_args(args)

  def init_from_raw_args(self, args: List[str]) -> None:
    self.raw_args = args
    self.tokens = []
    no_more_args: bool = False
    i = 0
    while i < len(args):
      arg = args[i]
      i += 1
      if not no_more_args and arg.startswith('-'):
        option = arg
        value: Optional[str] = None
        if arg.startswith('--') and '=' in arg:
          option, value = arg.split('=')
        elif arg in value_options_set:
          if i >= len(args):
            raise XPulumiError(f"Option \"{arg}\" requires a value")
          value = args[i]
          i += 1
        self.tokens.append(CmdOption(option, value))
        if option == '--':
          no_more_args = True
      else:
        self.tokens.append(arg)

    self.pos_args = [ x for x in self.tokens if isinstance(x, str) ]
    self.options = [ x for x in self.tokens if not isinstance(x, str) ]

    full_subcmd = ""
    for i in range(len(self.pos_args), 0, -1):
      subcmd = " ".join(self.pos_args[:i])
      if subcmd in value_options:
        full_subcmd = subcmd
        break
    self.subcmd = full_subcmd
    subcmd_parts = [] if full_subcmd == '' else full_subcmd.split(' ')
    subcmd_options: Set[str] = set(value_options[""])
    for i in range(len(subcmd_parts)):
      ss = ' '.join(subcmd_parts[:i+1])
      subcmd_options.update(cast(dict, value_options[ss]))
    self.subcmd_valid_options = subcmd_options

  def init_from_tokens(self, tokens: List[Union[str, CmdOption]]):
    raw_args: List[str] = []
    for t in tokens:
      if isinstance(t, str):
        raw_args.append(t)
      else:
        raw_args.extend(t.to_cmd_args())
    self.init_from_raw_args(raw_args)

  def get_option_value_as_list(self, *options: str) -> List[Union[str, bool]]:
    result: List[Union[str, bool]] = []
    for o in self.options:
      if o.option in options:
        result.append(True if o.value is None else o.value)
    return result

  def has_option_value(self, *options: str) -> bool:
    return len(self.get_option_value_as_list(*options)) > 0

  def get_option_value(self, *options: str) -> Optional[Union[str, bool]]:
    results = self.get_option_value_as_list(*options)
    if len(results) > 1:
      raise XPulumiError(f"Multiple instances of pulumi command option {', '.join(options)}")
    if len(results) == 0:
      return None
    return results[0]

  def remove_option(self, *options: str, alt_option: Optional[str]=None) -> None:
    tokens: List[Union[str, CmdOption]] = []
    for token in self.tokens:
      if not isinstance(token, CmdOption) or not token.option in options:
        tokens.append(token)
    self.init_from_tokens(tokens)

  def prefix_option(self, option: str, value: Optional[str]=None) -> None:
    tokens = cast(List[Union[str, CmdOption]], [ CmdOption(option, value) ])
    tokens.extend(self.tokens)
    self.init_from_tokens(tokens)

  def set_option(self, *options: str, value: Optional[str]=None) -> None:
    self.remove_option(*options)
    newOpt = CmdOption(options[0], value)
    tokens = cast(List[Union[str, CmdOption]], [ newOpt ])
    tokens.extend(self.tokens)
    self.init_from_tokens(tokens)

  def option_is_allowed(self, *options: str) -> bool:
    for option in options:
      if option in self.subcmd_valid_options:
        return True
    return False

  def __str__(self) -> str:
    return f"<pulumi_cmd {self.raw_args}>"

  def __repr__(self) -> str:
    return f"<pulumi_cmd {self.raw_args}>"


class PulumiWrapper:
  _cwd: str
  _ctx: Optional[XPulumiContextBase] = None
  _stack_name: Optional[str] = None
  _project: Optional[XPulumiProject] = None
  _backend: Optional[XPulumiBackend] = None
  _base_env: Dict[str, str]
  _debug: bool = False

  def __init__(
        self,
        ctx: Optional[XPulumiContextBase]=None,
        backend: Optional[Union[str,XPulumiBackend]]=None,
        project: Optional[Union[str,XPulumiProject]]=None,
        stack_name: Optional[str]=None,
        cwd: Optional[str]=None,
        env: Optional[Dict[str, str]] = None,
        debug: bool = False
      ):
    self._debug = debug
    if ctx is None:
      ctx = XPulumiContextBase(cwd=cwd)
    self._ctx = ctx
    if cwd is None:
      cwd = ctx.get_cwd()
    self._cwd = cwd
    if project is None or isinstance(project, str):
      project = ctx.get_optional_project(project, cwd=cwd)
    self._project = project

    if backend is None or isinstance(backend, str):
      backend = ctx.get_optional_backend(backend, cwd=cwd)
    if backend is None and not project is None:
      backend = project.backend
    self._backend = backend
    if not project is None:
      self._stack_name = project.get_optional_stack_name(stack_name)
    else:
      self._stack_name = ctx.get_optional_stack_name(stack_name)
    if env is None:
      env = dict(os.environ)
    else:
      env = deepcopy(env)
    self._base_env = env

  @property
  def ctx(self) -> XPulumiContextBase:
    assert not self._ctx is None
    return self._ctx

  @property
  def cwd(self) -> str:
    return self._cwd

  @property
  def project(self) -> Optional[XPulumiProject]:
    return self._project

  @property
  def backend(self) -> Optional[XPulumiBackend]:
    return self._backend

  @property
  def stack_name(self) -> Optional[str]:
    return self._stack_name

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self._cwd, os.path.expanduser(path)))

  def get_environ(self, stack_name: Optional[str]=None) -> Dict[str, str]:
    ctx = self.ctx
    if stack_name is None:
      stack_name = self.stack_name
    env = dict(self._base_env)
    env['PULUMI_HOME'] = ctx.get_pulumi_home()
    project = self.project
    backend = self.backend
    if not backend is None and (backend.scheme == 'http' or backend.scheme == 'https'):
      env['PULUMI_BACKEND_URL'] = backend.url
      env['PULUMI_ACCESS_TOKEN'] = backend.require_access_token()
    else:
      if 'PULUMI_BACKEND_URL' in env:
        del env['PULUMI_BACKEND_URL']
      if 'PULUMI_ACCESS_TOKEN' in env:
        del env['PULUMI_ACCESS_TOKEN']
    if not project is None:
      env['PULUMI_BACKEND_URL'] = project.get_project_backend_url()
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
          assert passphrase_v is None or isinstance(passphrase_v, str)
          passphrase = passphrase_v
        except Exception:
          pass
      if not passphrase is None:
        env['PULUMI_CONFIG_PASSPHRASE'] = passphrase
    return env

  def _fix_raw_popen_args(self, arglist: List[str], kwargs: Dict[str, Any], stack_name: Optional[str]=None) -> List[str]:
    if stack_name is None:
      stack_name = self.stack_name
    pc = PulumiCmd(arglist)
    if not stack_name is None and not pc.has_option_value('-s', '--stack') and pc.option_is_allowed('-s', '--stack'):
      pc.set_option('-s', '--stack', value=stack_name)
    arglist = [ self.ctx.get_pulumi_cli() ] + pc.raw_args
    env = self.get_environ(stack_name=stack_name)
    call_env = kwargs.pop('env', None)
    if not call_env is None:
      env.update(call_env)
    kwargs['env'] = env
    project = self.project
    if project is None:
      cwd = self.cwd
    else:
      cwd = project.project_dir
    kwargs['cwd'] = cwd
    if self._debug:
      print(f"Invoking raw pulumi: {arglist}", file=sys.stderr)
    return arglist

  def Popen(self, arglist: List[str], **kwargs) -> subprocess.Popen:
    stack_name: Optional[str] = kwargs.pop('stack_name', None)
    arglist = self._fix_raw_popen_args(arglist, kwargs, stack_name=stack_name)
    return subprocess.Popen(arglist, **kwargs)

  def check_call(self, arglist: List[str], **kwargs) -> int:
    stack_name: Optional[str] = kwargs.pop('stack_name', None)
    arglist = self._fix_raw_popen_args(arglist, kwargs, stack_name=stack_name)
    return subprocess.check_call(arglist, **kwargs)

  def call(self, arglist: List[str], **kwargs) -> int:
    stack_name: Optional[str] = kwargs.pop('stack_name', None)
    arglist = self._fix_raw_popen_args(arglist, kwargs, stack_name=stack_name)
    return subprocess.call(arglist, **kwargs)

  def check_output(self, arglist: List[str], **kwargs) -> Union[str, bytes]:
    stack_name: Optional[str] = kwargs.pop('stack_name', None)
    arglist = self._fix_raw_popen_args(arglist, kwargs, stack_name=stack_name)
    return subprocess.check_output(arglist, **kwargs)
