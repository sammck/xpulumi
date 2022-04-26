#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for standard Pulumi CLI that passes xpulumi envionment forward"""

from typing import (Any, Dict, List, Optional, Union, Set, Type, TextIO, Sequence, Tuple)

from copy import deepcopy
from lib2to3.pgen2.token import OP
import os
import sys
import json
import subprocess
import colorama # type: ignore[import]
from colorama import Fore, Back, Style
from project_init_tools import deactivate_virtualenv, find_command_in_path, get_git_root_dir

# NOTE: this module runs with -m; do not use relative imports
from xpulumi.backend import XPulumiBackend
from xpulumi.context import XPulumiContext
from xpulumi.project import XPulumiProject
from xpulumi.stack import XPulumiStack
from xpulumi.base_context import XPulumiContextBase
from xpulumi.config import XPulumiConfig
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import JsonableTypes
from xpulumi.pulumi_cli.help_metadata import (
    PulumiMetadata,
    ParsedPulumiCmd,
  )

def is_colorizable(stream: TextIO) -> bool:
  is_a_tty = hasattr(stream, 'isatty') and stream.isatty()
  return is_a_tty

class CmdExitError(RuntimeError):
  exit_code: int

  def __init__(self, exit_code: int, msg: Optional[str]=None):
    if msg is None:
      msg = f"Command exited with return code {exit_code}"
    super().__init__(msg)
    self.exit_code = exit_code

class PulumiCommandHandler:
  wrapper: 'PulumiWrapper'
  _explicit_stack_name: Optional[str] = None
  _explicit_stack_name_known: bool = False
  _stack: Optional[XPulumiStack] = None

  def __init__(
        self,
        wrapper: 'PulumiWrapper',
      ):
    self.wrapper = wrapper

  @classmethod
  def modify_metadata(cls, wrapper: 'PulumiWrapper', metadata: PulumiMetadata) -> None:
    pass

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

  def get_stack(self) -> Optional[XPulumiStack]:
    if self._stack is None and not self.stack_name is None and not self.project is None:
      self._stack = self.project.get_stack(self.stack_name)
    return self._stack

  def require_stack(self) -> XPulumiStack:
    stack = self.get_stack()
    if stack is None:
      raise XPulumiError("A stack name is required, and no default is set")
    return stack

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

  def custom_tweak(self) -> None:
    pass

  def tweak_parsed(self) -> None:
    self.fix_parsed_stack_name()
    self.custom_tweak()

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

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    return None

  def do_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> int:
    if self.is_debug:
      print(f"Invoking raw pulumi command {cmd}", file=sys.stderr)
    result = subprocess.call(cmd, env=env)
    return result

  def do_post_raw_pulumi(self, exit_code: int) -> Optional[int]:
    return exit_code

  def do_cmd(self) -> Optional[int]:
    env = self.get_final_env()
    cmd = [ self.pulumi_prog ]
    cmd += self.get_final_arglist()
    if self.is_debug:
      print(f"Pulumi env = {json.dumps(env, indent=2, sort_keys=True)}", file=sys.stderr)
    result: Optional[int] = self.do_pre_raw_pulumi(cmd, env)
    if not result is None:
      return result
    if self.should_precreate_backend_project:
      if self.is_debug:
        print("Making sure project backend dir is precreated...", file=sys.stderr)
      self.precreate_project_backend()
    result = self.do_raw_pulumi(cmd, env)
    result = self.do_post_raw_pulumi(result)
    return result

  def __call__(self) -> int:
    parsed = self.get_parsed()
    if parsed.allows_option('--help') and parsed.get_option_bool('--help'):
      result: Optional[int] = self.do_help()
    else:
      self.pretweak()
      self.tweak_parsed()
      result = self.do_cmd()

    if result is None:
      result = 0
    return result

  @property
  def is_debug(self) -> bool:
    return self.wrapper.is_debug

  def ocolor(self, codes: str) -> str:
    return self.wrapper.ocolor(codes)

  def ecolor(self, codes: str) -> str:
    return self.wrapper.ecolor(codes)

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
  _pulumi_dir: str
  _arglist: List[str]
  _raw_pulumi: bool = False
  _raw_env: bool = False
  _command_handlers: Dict[str, Type[PulumiCommandHandler]]
  _colorize_stdout: bool = False
  _colorize_stderr: bool = False
  _raw_stdout: TextIO = sys.stdout
  _raw_stderr: TextIO = sys.stderr
  _monochrome: bool = True
  _traceback: bool = True

  def __init__(
        self,
        arglist: Sequence[str],
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

    self._arglist = list(arglist)
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

    self._raw_stdout = sys.stdout
    self._raw_stderr = sys.stderr


  def register_command_handler(self, full_subcmd: str, handler: Type[PulumiCommandHandler]) -> None:
    self._command_handlers[full_subcmd] = handler

  def register_custom_handlers(self) -> None:
    # import deferred to runtime to break circular imports
    from xpulumi.pulumi_cli.custom_handlers import get_custom_handlers # pylint: disable=cyclic-import
    custom_handlers = get_custom_handlers()
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
    main_topic.add_option([ '--tb' ], description='[xpulumi] Display full stack traceback on error', is_persistent = True)
    for handler in self._command_handlers.values():
      handler.modify_metadata(self, metadata)

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
      cmd_raw_pulumi = not not self._parsed.pop_option_optional_bool('--raw-pulumi')
      cmd_raw_env = not not self._parsed.pop_option_optional_bool('--raw-env')
      cmd_debug_cli = not not self._parsed.pop_option_optional_bool('--debug-cli')
      self._traceback = not not self._parsed.pop_option_optional_bool('--tb')
      self._raw_pulumi = self.raw_pulumi or cmd_raw_pulumi
      self._raw_env = self.raw_env or cmd_raw_env
      self._debug = self._debug or cmd_debug_cli
      cmd_color = self._parsed.get_option_str('--color', 'auto')
      self._monochrome = not cmd_color in ('auto', 'always')
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
    result: Type[PulumiCommandHandler]
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
      try:
        parsed = self.get_parsed()
        if self._raw_pulumi:
          env = self.get_environ()
          cmd = [ self.pulumi_prog ] + parsed.arglist
          if self.is_debug:
            print(f"Pulumi env = {json.dumps(env, indent=2, sort_keys=True)}", file=sys.stderr)
            print(f"Invoking raw pulumi command {cmd}", file=sys.stderr)
          result = subprocess.call(cmd, env=env)
        else:
          if not self._monochrome:
            self._colorize_stdout = is_colorizable(sys.stdout)
            self._colorize_stderr = is_colorizable(sys.stderr)
            if self._colorize_stdout or self._colorize_stderr:
              colorama.init(wrap=False)
              if self._colorize_stdout:
                new_stream = colorama.AnsiToWin32(sys.stdout)
                if new_stream.should_wrap():
                  sys.stdout = new_stream
              if self._colorize_stderr:
                new_stream = colorama.AnsiToWin32(sys.stderr)
                if new_stream.should_wrap():
                  sys.stderr = new_stream

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
      except Exception as ex:
        if isinstance(ex, CmdExitError):
          result = ex.exit_code
        else:
          result = 1
        if result != 0:
          if self._traceback:
            raise

          print(f"{self.ecolor(Fore.RED)}xpulumi: error: {ex}{self.ecolor(Style.RESET_ALL)}", file=sys.stderr)

    return result

  @property
  def is_debug(self) -> bool:
    return self._debug

  def ocolor(self, codes: str) -> str:
    return codes if self._colorize_stdout else ""

  def ecolor(self, codes: str) -> str:
    return codes if self._colorize_stderr else ""

def _get_base_prefix() -> str:
  return getattr(sys, "base_prefix", None) or getattr(sys, "real_prefix", None) or sys.prefix

def _get_virtualenv() -> Optional[str]:
  return None if sys.prefix == _get_base_prefix() else sys.prefix

def _get_raw_pulumi() -> Tuple[str, str]:
  pulumi_home: Optional[str] = os.environ.get('PULUMI_HOME', '')
  if pulumi_home == '':
    project_root_dir = get_git_root_dir()
    if not project_root_dir is None:
      project_pulumi_home = os.path.join(project_root_dir, '.local', '.pulumi')
      project_pulumi_prog = os.path.join(project_pulumi_home, 'bin', 'pulumi')
      if os.path.exists(project_pulumi_prog):
        return project_pulumi_prog, project_pulumi_home
    virtualenv_dir = _get_virtualenv()
    if not virtualenv_dir is None:
      project_root_dir = get_git_root_dir(starting_dir=virtualenv_dir)
      if not project_root_dir is None:
        project_pulumi_home = os.path.join(project_root_dir, '.local', '.pulumi')
        project_pulumi_prog = os.path.join(project_pulumi_home, 'bin', 'pulumi')
        if os.path.exists(project_pulumi_prog):
          return project_pulumi_prog, project_pulumi_home
    novenv = dict(os.environ)
    deactivate_virtualenv(novenv)
    path_pulumi_prog = find_command_in_path('pulumi', searchpath=novenv['PATH'])
    if not path_pulumi_prog is None:
      path_pulumi_home = os.path.dirname(os.path.dirname(path_pulumi_prog))
      return path_pulumi_prog, path_pulumi_home
  else:
    pulumi_prog = os.path.join(pulumi_home, 'bin', 'pulumi')
    if os.path.exists(pulumi_prog):
      return pulumi_prog, pulumi_home
  raise XPulumiError("Unable to locate wrapped pulumi executable")

  
def _run_raw_pulumi(arglist: List[str]) -> int:
  pulumi_prog, pulumi_home = _get_raw_pulumi()
  os.environ['PULUMI_HOME'] = pulumi_home
  result = subprocess.call([ pulumi_prog ] + arglist)
  return result

def run_pulumi_wrapper(arglist: List[str]) -> int:
  cfg: Optional[XPulumiConfig] = None
  try:
    cfg = XPulumiConfig()
  except FileNotFoundError:
    pass
  if cfg is None:
    return _run_raw_pulumi(arglist)
  ctx = XPulumiContextBase(config=cfg)
  result = PulumiWrapper(arglist, ctx=ctx).call()
  return result
