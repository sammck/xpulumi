#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for standard Pulumi CLI that passes xpulumi envionment forward"""

from typing import (Any, Dict, List, Optional, Union, Set)

from copy import deepcopy
from lib2to3.pgen2.token import OP
import os
import sys
import json
import subprocess

# NOTE: this module runs with -m; do not use relative imports
from xpulumi.backend import XPulumiBackend
from xpulumi.project import XPulumiProject
from xpulumi.base_context import XPulumiContextBase
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import JsonableTypes
from xpulumi.pulumi_cli.help_metadata import (
    PulumiMetadata,
    ParsedPulumiCmd,
  )

class CmdExitError(RuntimeError):
  exit_code: int

class PulumiWrapper:
  _cwd: str
  _ctx: Optional[XPulumiContextBase] = None
  _stack_name: Optional[str] = None
  _project: Optional[XPulumiProject] = None
  _backend: Optional[XPulumiBackend] = None
  _base_env: Dict[str, str]
  _debug: bool = False
  _metadata: Optional[PulumiMetadata] = None
  _parsed: Optional[ParsedPulumiCmd] = None
  _pulumi_dir: Optional[str] = None
  _arglist: List[str]
  _raw_pulumi: bool = False
  _raw_env: bool = False

  def __init__(
        self,
        arglist: List[str],
        ctx: Optional[XPulumiContextBase]=None,
        backend: Optional[Union[str,XPulumiBackend]]=None,
        project: Optional[Union[str,XPulumiProject]]=None,
        stack_name: Optional[str]=None,
        cwd: Optional[str]=None,
        env: Optional[Dict[str, str]] = None,
        pulumi_dir: Optional[str] = None,
        debug: Optional[bool] = None
      ):
    if env is None:
      env = dict(os.environ)
    else:
      env = dict(env)
    debug = env.get('XPULUMI_DEBUG_PULUMI', '') != '' if debug is None else debug
    self._debug = debug

    self._arglist = arglist
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
    self._base_env = env
    self._raw_pulumi = env.get('XPULUMI_RAW_PULUMI', '') != ''
    self._raw_env = self._raw_pulumi
    if pulumi_dir is None:
      pulumi_dir = ctx.get_pulumi_home()
    self._pulumi_dir = pulumi_dir

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

  @property
  def pulumi_dir(self) -> str:
    return self._pulumi_dir

  @property
  def pulumi_bin_dir(self) -> str:
    return os.path.join(self.pulumi_dir, 'bin')

  @property
  def pulumi_prog(self) -> str:
    return os.path.join(self.pulumi_bin_dir, 'pulumi')

  @property
  def arglist(self) -> List[str]:
    return self._arglist

  @property
  def raw_pulumi(self) -> bool:
    return self._raw_pulumi

  @property
  def raw_env(self) -> bool:
    return self._raw_env

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self._cwd, os.path.expanduser(path)))

  def precreate_project_backend(self) -> None:
    self.project.precreate_project_backend()

  def get_environ(self, stack_name: Optional[str]=None) -> Dict[str, str]:
    if self._raw_env:
      return self._base_env
    ctx = self.ctx
    if stack_name is None:
      stack_name = self.stack_name
    env = dict(self._base_env)
    env['XPULUMI_RAW_PULUMI'] = '1'  # Any nested invocations will just pass through
    env['PULUMI_HOME'] = self.pulumi_dir
    # Pulumi dynamic resource plugins *must* be in the path to work.
    env['PATH'] = self.pulumi_bin_dir + ':' + env['PATH']
    project = self.project
    backend = self.backend
    if not backend is None and (backend.scheme in [ 'http', 'https' ]):
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

  def modify_metadata(self, metadata: PulumiMetadata) -> None:
    main_topic = metadata.main_topic
    main_topic.add_option([ '--debug-cli' ], description='[xpulumi] Debug pulumi CLI wrapper', is_persistent = True)
    main_topic.add_option([ '--raw-pulumi' ], description='[xpulumi] Run raw pulumi without modifying commandline', is_persistent = True)
    main_topic.add_option([ '--raw-env' ], description='[xpulumi] Run pulumi without modifying environment', is_persistent = True)

  def get_metadata(self) -> PulumiMetadata:
    if self._metadata is None:
      self._metadata = PulumiMetadata(pulumi_dir=self.pulumi_dir, env=self.get_environ())
      self.modify_metadata(self._metadata)
    return self._metadata

  def get_parsed(self) -> ParsedPulumiCmd:
    if self._parsed is None:
      md = self.get_metadata()
      self._parsed = md.parse_command(self.arglist)
      for topic in self._parsed.metadata.iter_topics():
        topic.title += ' (xpulumi wrapper)'
      cmd_raw_pulumi = self._parsed.pop_option_optional_bool('--raw-pulumi')
      cmd_raw_env = self._parsed.pop_option_optional_bool('--raw-env')
      cmd_debug_cli = self._parsed.pop_option_optional_bool('--debug-cli')
      self._raw_pulumi = self.raw_pulumi or cmd_raw_pulumi
      self._raw_env = self.raw_env or cmd_raw_env
      self._debug = self._debug or cmd_debug_cli
    return self._parsed

  stack_arg_cmds: Set[str] = set(["cancel", "stack init", "stack rm", "stack select"])
  """Subcommands that accept a positional argument that does the same thing as --stack"""

  precreate_project_backend_cmds: Set[str] = set(["state", "stack", "preview", "up", "destroy"])
  """Subcommands that require the project backend directory to be precreated"""

  def call(self) -> int:
    cmd = [ self.pulumi_prog ]
    precreate_required = False

    if self._raw_pulumi:
      env = self.get_environ()
      cmd += self.arglist
    else:
      explicit_stack_name: Optional[str] = None
      parsed = self.get_parsed()
      if parsed.topic.full_subcmd in self.stack_arg_cmds and parsed.num_pos_args() > 0:
        explicit_stack_name = parsed.get_pos_args()[0]
      elif parsed.allows_option('--stack'):
        explicit_stack_name = parsed.get_option_str('--stack')
      stack_name = self.stack_name if explicit_stack_name is None else explicit_stack_name
      if not self._raw_pulumi and explicit_stack_name is None and not stack_name is None and parsed.allows_option('--stack'):
        parsed.set_option_str('--stack', stack_name)
      env = self.get_environ(stack_name=stack_name)
      cmd += parsed.arglist
      for precreate_cmd in self.precreate_project_backend_cmds:
        if (parsed.topic.full_subcmd + ' ').startswith(precreate_cmd + ' '):
          precreate_required = True
          break

    if self._debug:
      print(f"Pulumi env = {json.dumps(env, indent=2, sort_keys=True)}", file=sys.stderr)

    if not self._raw_pulumi and parsed.allows_option('--help') and parsed.get_option_bool('--help'):
      parsed.topic.print_help()
      result = 0
    else:
      if precreate_required:
        if self._debug:
          print(f"Making sure project backend dir is precreated...", file=sys.stderr)
        self.precreate_project_backend()

      if self._debug:
        print(f"Invoking raw pulumi command {cmd}", file=sys.stderr)
      result = subprocess.call(cmd, env=env)

    return result
