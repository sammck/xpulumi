#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for standard Pulumi CLI that passes xpulumi envionment forward"""

from typing import (Any, Dict, List, Optional, Union, Set, Type)

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

class PulumiCommandHandler:
  wrapper: 'PulumiWrapper'
  _explicit_stack_name: Optional[str] = None
  _explicit_stack_name_known: bool = False

  def __init__(
        self,
        wrapper: 'PulumiWrapper',
      ):
    self.wrapper = wrapper

  @property
  def should_precreate_backend_project(self) -> bool:
    return False

  @property
  def allows_pos_arg_stack_name(self) -> bool:
    return False

  @property
  def ctx(self) -> XPulumiContextBase:
    return self.wrapper.ctx

  @property
  def cwd(self) -> str:
    return self.wrapper.cwd

  @property
  def base_env(self) -> Dict[str, str]:
    return self.wrapper.base_env

  @property
  def project(self) -> Optional[XPulumiProject]:
    return self.wrapper.project

  @property
  def backend(self) -> Optional[XPulumiBackend]:
    return self.wrapper.backend

  @property
  def default_stack_name(self) -> Optional[str]:
    return self.wrapper.stack_name

  @property
  def stack_name(self) -> Optional[str]:
    result = self.get_explicit_stack_name()
    if result is None:
      result = self.default_stack_name
    return result

  @property
  def pulumi_dir(self) -> str:
    return self.wrapper.pulumi_dir

  @property
  def pulumi_bin_dir(self) -> str:
    return self.wrapper.pulumi_bin_dir

  @property
  def pulumi_prog(self) -> str:
    return self.wrapper.pulumi_prog

  @property
  def arglist(self) -> List[str]:
    return self.wrapper.arglist

  @property
  def raw_env(self) -> bool:
    return self.wrapper.raw_env

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self.cwd, os.path.expanduser(path)))

  def precreate_project_backend(self) -> None:
    if not self.project is None:
      self.project.precreate_project_backend()

  def get_environ(self, stack_name: Optional[str]=None) -> Dict[str, str]:
    if self.raw_env:
      return self.base_env
    ctx = self.ctx
    if stack_name is None:
      stack_name = self.stack_name
    env = dict(self.base_env)
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

  def get_metadata(self) -> PulumiMetadata:
    return self.wrapper.get_metadata()

  def get_parsed(self) -> ParsedPulumiCmd:
    return self.wrapper.get_parsed()

  def get_explicit_stack_name(self) -> Optional[str]:
    if not self._explicit_stack_name_known:
      explicit_stack_name: Optional[str] = None
      parsed = self.get_parsed()
      if self.allows_pos_arg_stack_name and parsed.num_pos_args() > 0:
        if parsed.allows_option('--stack') and not parsed.get_option_str('--stack') is None:
          raise XPulumiError("Explicit stack name passed both as --stack and as positional arg")
        explicit_stack_name = parsed.get_pos_args()[0]
      elif parsed.allows_option('--stack'):
        explicit_stack_name = parsed.get_option_str('--stack')
      self._explicit_stack_name = explicit_stack_name
      self._explicit_stack_name_known = True
    return self._explicit_stack_name

  def fix_parsed_stack_name(self) -> None:
    explicit_stack_name = self.get_explicit_stack_name()
    final_stack_name = self.stack_name
    parsed = self.get_parsed()
    if not final_stack_name is None and explicit_stack_name != final_stack_name and parsed.allows_option('--stack'):
      parsed.set_option_str('--stack', final_stack_name)

  def tweak_parsed(self) -> None:
    self.fix_parsed_stack_name()

  def pretweak(self) -> None:
    self.get_explicit_stack_name()  # lock in before tweaking

  def get_final_arglist(self) -> List[str]:
    return self.get_parsed().arglist

  def get_final_env(self) -> Dict[str, str]:
    env = self.get_environ(stack_name=self.stack_name)
    return env

  def do_help(self) -> int:
    self.get_parsed().topic.print_help()
    return 0

  def do_final_cmd(self, cmd: List[str], env: Dict[str, str]) -> int:
    if self.is_debug:
      print(f"Invoking raw pulumi command {cmd}", file=sys.stderr)
    result = subprocess.call(cmd, env=env)
    return result

  def do_cmd(self) -> int:
    env = self.get_final_env()
    cmd = [ self.pulumi_prog ]
    cmd += self.get_final_arglist()
    if self.is_debug:
      print(f"Pulumi env = {json.dumps(env, indent=2, sort_keys=True)}", file=sys.stderr)
    if self.should_precreate_backend_project:
      if self.is_debug:
        print("Making sure project backend dir is precreated...", file=sys.stderr)
      self.precreate_project_backend()
    result = self.do_final_cmd(cmd, env)
    return result

  def __call__(self) -> int:
    parsed = self.get_parsed()
    if not parsed.allows_option('--help') and parsed.get_option_bool('--help'):
      result = self.do_help()
    else:
      self.pretweak()
      self.tweak_parsed()
      result = self.do_cmd()

    return result

  @property
  def is_debug(self) -> bool:
    return self.wrapper.is_debug


class PosStackArgPulumiCommandHandler(PulumiCommandHandler):
  @property
  def allows_pos_arg_stack_name(self) -> bool:
    return True

class PrecreatePulumiCommandHandler(PulumiCommandHandler):
  @property
  def should_precreate_backend_project(self) -> bool:
    return True

class PrecreatePosStackArgPulumiCommandHandler(PulumiCommandHandler):
  @property
  def should_precreate_backend_project(self) -> bool:
    return True

  @property
  def allows_pos_arg_stack_name(self) -> bool:
    return True

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
  _command_handlers: Dict[str, Type[PulumiCommandHandler]]

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
    self._command_handlers = {}
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
    self._base_env = env
    self._raw_pulumi = env.get('XPULUMI_RAW_PULUMI', '') != ''
    self._raw_env = self._raw_pulumi
    env_stack = env.get('PULUMI_STACK', '')
    if env_stack != '':
      self._stack_name = env_stack
    elif not project is None:
      self._stack_name = project.get_optional_stack_name(stack_name)
    else:
      self._stack_name = ctx.get_optional_stack_name(stack_name)
    if pulumi_dir is None:
      pulumi_dir = ctx.get_pulumi_home()
    self._pulumi_dir = pulumi_dir
    self.register_custom_handlers()

  def register_command_handler(self, full_subcmd: str, handler: Type[PulumiCommandHandler]) -> None:
    self._command_handlers[full_subcmd] = handler

  def register_custom_handlers(self) -> None:
    from .custom_handlers import custom_handlers
    for full_subcmd, handler in custom_handlers.items():
      self.register_command_handler(full_subcmd, handler)

  @property
  def ctx(self) -> XPulumiContextBase:
    assert not self._ctx is None
    return self._ctx

  @property
  def base_env(self) -> Dict[str, str]:
    return self._base_env

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
    if not self.project is None:
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

  stack_pos_arg_cmds: Set[str] = set(["cancel", "stack init", "stack rm", "stack select"])
  """Subcommands that accept a positional argument that does the same thing as --stack"""
  def is_stack_pos_arg_cmd(self, full_subcmd: str) -> bool:
    fss = full_subcmd + ' '
    for prefix in self.stack_pos_arg_cmds:
      if fss.startswith(prefix + ' '):
        return True
    return False


  precreate_project_backend_cmds: Set[str] = set(["state", "stack", "preview", "up", "destroy"])
  """Subcommands that require the project backend directory to be precreated"""
  def is_precreate_cmd(self, full_subcmd: str) -> bool:
    fss = full_subcmd + ' '
    for prefix in self.precreate_project_backend_cmds:
      if fss.startswith(prefix + ' '):
        return True
    return False

  def get_standard_handler(self, full_subcmd: str) -> Type[PulumiCommandHandler]:
    if self.is_stack_pos_arg_cmd(full_subcmd):
      if self.is_precreate_cmd(full_subcmd):
        result = PrecreatePosStackArgPulumiCommandHandler
      else:
        result = PosStackArgPulumiCommandHandler
    elif self.is_precreate_cmd(full_subcmd):
      result = PrecreatePulumiCommandHandler
    else:
      result = PulumiCommandHandler
    return result

  def call(self) -> int:
    if self._raw_pulumi:
      env = self.get_environ()
      cmd = [ self.pulumi_prog ] + self.arglist
      if self.is_debug:
        print(f"Pulumi env = {json.dumps(env, indent=2, sort_keys=True)}", file=sys.stderr)
        print(f"Invoking raw pulumi command {cmd}", file=sys.stderr)
      result = subprocess.call(cmd, env=env)
    else:
      parsed = self.get_parsed()
      full_subcmd = parsed.topic.full_subcmd
      handler_class = self._command_handlers.get(full_subcmd, None)
      if handler_class is None:
        handler_class = self.get_standard_handler(full_subcmd)
      if self.is_debug:
        print(f"handler_classs={handler_class}")
      handler = handler_class(self)
      if self.is_debug:
        print(f"Subcmd {full_subcmd}; delegating to {handler}", file=sys.stderr)
      result = handler()

    return result

  @property
  def is_debug(self) -> bool:
    return self._debug
