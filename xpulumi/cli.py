# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""xpulumi CLI"""

import base64
from typing import (
    TYPE_CHECKING, Optional, Sequence, List, Union, Dict, TextIO, Mapping, MutableMapping,
    cast, Any, Iterator, Iterable, Tuple, ItemsView, ValuesView, KeysView, Type )

import os
import sys
import argparse
import argcomplete # type: ignore[import]
import json
from base64 import b64encode, b64decode
import colorama # type: ignore[import]
from colorama import Fore, Back, Style
import subprocess
from io import TextIOWrapper
import yaml
from secret_kv import create_kv_store
from urllib.parse import urlparse, ParseResult
import ruamel.yaml # type: ignore[import]
from io import StringIO

from project_init_tools import (
    file_contents,
    full_name_of_type,
    full_type,
    get_git_root_dir,
    append_lines_to_file_if_missing,
    file_url_to_pathname,
    pathname_to_file_url,
    RoundTripConfig,
    sudo_call,
  )

from xpulumi.stack import XPulumiStack

from .config import XPulumiConfig
from .context import XPulumiContext
from .exceptions import XPulumiError
from .base_context import XPulumiContextBase
from .backend import XPulumiBackend
from .internal_types import JsonableTypes, Jsonable, JsonableDict, JsonableList
from .constants import XPULUMI_CONFIG_DIRNAME, XPULUMI_CONFIG_FILENAME_BASE
from .version import __version__ as pkg_version
from .project import XPulumiProject

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

class ArgparseExitError(CmdExitError):
  pass

class NoExitArgumentParser(argparse.ArgumentParser):
  def exit(self, status=0, message=None):
    if message:
      self._print_message(message, sys.stderr)
    raise ArgparseExitError(status, message)


class CommandLineInterface:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _cfg: Optional[XPulumiConfig] = None
  _ctx: Optional[XPulumiContextBase] = None
  _project_name: Optional[str] = None
  _backend_name: Optional[str] = None
  _project: Optional[XPulumiProject] = None
  _backend: Optional[XPulumiBackend] = None

  _raw_stdout: TextIO = sys.stdout
  _raw_stderr: TextIO = sys.stderr
  _raw: bool = False
  _compact: bool = False
  _output_file: Optional[str] = None
  _encoding: str = 'utf-8'
  _config_file: Optional[str] = None

  _colorize_stdout: bool = False
  _colorize_stderr: bool = False

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self._argv = argv

  def ocolor(self, codes: str) -> str:
    return codes if self._colorize_stdout else ""

  def ecolor(self, codes: str) -> str:
    return codes if self._colorize_stderr else ""

  @property
  def cwd(self) -> str:
    return self._cwd

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self.cwd, os.path.expanduser(path)))

  def pretty_print(
        self,
        value: Jsonable,
        compact: Optional[bool]=None,
        colorize: Optional[bool]=None,
        raw: Optional[bool]=None,
      ):

    if raw is None:
      raw = self._raw
    if raw:
      if isinstance(value, str):
        self._raw_stdout.write(value)
        return

    if compact is None:
      compact = self._compact
    if colorize is None:
      colorize = True

    def emit_to(f: TextIO):
      final_colorize = colorize and ((f is sys.stdout and self._colorize_stdout) or (f is sys.stderr and self._colorize_stderr))

      if not final_colorize:
        if compact:
          json.dump(value, f, separators=(',', ':'), sort_keys=True)
        else:
          json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')
      else:
        jq_input = json.dumps(value, separators=(',', ':'), sort_keys=True)
        cmd = [ 'jq' ]
        if compact:
          cmd.append('-c')
        cmd.append('.')
        with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=f) as proc:
          proc.communicate(input=jq_input.encode('utf-8'))
          exit_code = proc.returncode
        if exit_code != 0:
          raise subprocess.CalledProcessError(exit_code, cmd)

    output_file = self._output_file
    if output_file is None:
      emit_to(sys.stdout)
    else:
      with open(output_file, "w", encoding=self._encoding) as f:
        emit_to(f)

  def cmd_bare(self) -> int:
    print("A command is required", file=sys.stderr)
    return 1

  def cmd_test(self) -> int:
    from xpulumi.test_func import run_test
    result = run_test()
    self.pretty_print(result)
    return 0

  def cmd_version(self) -> int:
    self.pretty_print(pkg_version)
    return 0

  def cmd_project_root_dir(self) -> int:
    self.pretty_print(self.get_project_root_dir())
    return 0

  def cmd_update_pulumi(self) -> int:
    from project_init_tools.installer.pulumi import install_pulumi
    cfg = self.get_config()
    xpulumi_dir = os.path.join(cfg.project_root_dir, '.local', '.pulumi')
    install_pulumi(xpulumi_dir, min_version='latest')
    return 0

  def cmd_run(self) -> int:
    args = self._args
    group: Optional[str] = args.run_with_group
    use_sudo: bool = args.use_sudo
    sudo_reason: Optional[str] = args.sudo_reason
    cmd_and_args: List[str] = args.cmd_and_args

    if len(cmd_and_args) == 0:
      cmd_and_args = [ 'bash' ]

    if cmd_and_args[0].startswith('-'):
      raise XPulumiError(f"Unrecognized command option {cmd_and_args[0]}")

    exit_code = sudo_call(cmd_and_args, run_with_group=group, use_sudo=use_sudo, sudo_reason=sudo_reason)

    return exit_code

  def run_cmd_class(self, cmd_class: Type['CommandHandler']):
    exit_code = cmd_class(self)()
    return exit_code

  def cmd_init_env(self) -> int:
    from .cmd_init_env import CmdInitEnv as cmd_class # pylint: disable=cyclic-import
    return self.run_cmd_class(cmd_class)

  def get_config(self) -> XPulumiConfig:
    if self._cfg is None:
      self._cfg = XPulumiConfig(starting_dir=self.cwd)
    return self._cfg

  def get_config_file(self) -> str:
    return self.get_config().config_file

  def update_config(self, *args, **kwargs):
    cfg_file = self.get_config_file()
    rt = RoundTripConfig(cfg_file)
    rt.update(*args, **kwargs)
    rt.save()

  def get_context(self) -> XPulumiContextBase:
    if self._ctx is None:
      self._ctx = self.get_config().create_context()
    return self._ctx

  def get_project_name(self) -> str:
    if self._project_name is None:
      project_name = cast(Optional[str], self.args.project)
      assert project_name is None or isinstance(project_name, str)
      if project_name is None:
        project_name = self.get_context().get_project_name()
      self._project_name = project_name
    return self._project_name

  def get_project(self) -> XPulumiProject:
    if self._project is None:
      self._project = self.get_context().get_project(self.get_project_name())
    return self._project

  def get_backend_name(self) -> str:
    if self._backend_name is None:
      self._backend_name = self.get_context().get_backend_name()
    return self._backend_name

  def get_backend(self) -> XPulumiBackend:
    if self._backend is None:
      self._backend = self.get_context().get_backend(self.get_backend_name())
    return self._backend

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_xpulumi_data_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), 'xp')

  def get_project_dir(self, project_name: Optional[str]=None) -> str:
    if project_name is None:
      project_name = self.get_project_name()
    return self.get_context().get_project_infra_dir(project_name)

  def get_backend_dir(self, backend_name: Optional[str]=None) -> str:
    if backend_name is None:
      backend_name = self.get_backend_name()
    return self.get_context().get_backend_infra_dir(backend_name)

  def create_file_backend(self, new_backend: str, new_backend_uri: Optional[str]=None):
    backend_dir = self.get_backend_dir(new_backend)
    if os.path.exists(backend_dir):
      raise XPulumiError(f"Backend \"{new_backend}\" already exists")
    if new_backend_uri is None or new_backend_uri == "file" or new_backend_uri == "file:" or new_backend_uri == "file://":
      new_backend_uri = "file://./state"
    backend_pathname = file_url_to_pathname(new_backend_uri, cwd=backend_dir, allow_relative=True)
    new_backend_uri = pathname_to_file_url(backend_pathname)
    rel_backend_pathname = os.path.relpath(backend_pathname, backend_dir)
    rel_backend_uri = "file://./" + rel_backend_pathname.replace('\\', '/')

    backend_parent_dir = os.path.dirname(backend_pathname)
    if os.path.exists(backend_pathname):
      raise XPulumiError(f"File fackend at \"{new_backend_uri}\" already exists")
    if backend_parent_dir != backend_dir and not os.path.isdir(backend_parent_dir):
      raise XPulumiError(f"Parent directory of file backend {new_backend_uri} does not exist")
    os.makedirs(backend_dir)
    os.makedirs(backend_pathname)
    with open(os.path.join(backend_pathname, '.gitignore'), 'w', encoding='utf-8') as f:
      print("!.pulumi/", file=f)
    backend_data: JsonableDict = dict(
        name=new_backend,
        uri=rel_backend_uri,
        options=dict(
          includes_organization=False,
          includes_project=False,
        )
      )
    with open(os.path.join(backend_dir, "backend.json"), 'w', encoding='utf-8') as f:
      print(json.dumps(backend_data, indent=2, sort_keys=True), file=f)

  def create_s3_backend(
        self,
        new_backend: str,
        new_backend_uri: str,
        new_s3_bucket_project: Optional[str]=None,
        new_s3_bucket_backend: Optional[str]=None
      ):
    raise NotImplementedError()

  def get_selected_stack_name(self) -> Optional[str]:
    args = self._args
    stack_name: str = cast(Optional[str], args.stack)
    if stack_name is None:
      stack_name = self.get_context().get_stack_name()
    return stack_name

  def get_required_selected_stack_name(self) -> str:
    result = self.get_selected_stack_name()
    if result is None:
      raise XPulumiError("A stack name is required for this command, and no default has been set")
    return result

  def get_selected_stack(self) -> Optional[XPulumiStack]:
    stack_name = self.get_selected_stack_name()
    result = None if stack_name is None else self.get_project().get_stack(stack_name, create=True)
    return result

  def get_required_selected_stack(self) -> XPulumiStack:
    stack_name = self.get_required_selected_stack_name()
    result = self.get_project().get_stack(stack_name, create=True)
    return result

  def cmd_be_create(self) -> int:
    args = self._args
    new_s3_bucket_project = cast(Optional[str], args.new_s3_bucket_project)
    new_s3_bucket_backend = cast(Optional[str], args.backend)
    new_backend = cast(str, args.new_backend)
    new_backend_uri = cast(Optional[str], args.new_backend_uri)
    if new_backend_uri is None or new_backend_uri == "file" or new_backend_uri == "file:" or new_backend_uri == "file://":
      new_backend_uri = "file://./state"
    parts = urlparse(new_backend_uri)
    if parts.scheme == 'file':
      self.create_file_backend(new_backend, new_backend_uri)
    elif parts.scheme == 's3':
      self.create_s3_backend(
          new_backend,
          new_backend_uri,
          new_s3_bucket_project=new_s3_bucket_project,
          new_s3_bucket_backend=new_s3_bucket_backend
        )
    else:
      raise XPulumiError(f"Cannot create a new backend with scheme {parts.scheme}")
    return 0

  def cmd_be_select(self) -> int:
    args = self._args
    backend_name: str = args.default_backend
    backend = XPulumiBackend(backend_name)
    self.update_config(default_backend=backend.name)
    return 0

  def cmd_prj_create(self) -> int:
    raise NotImplementedError()

  def cmd_stack_dependencies(self) -> int:
    stack = self.get_required_selected_stack()
    deps = stack.get_stack_build_order()
    result = [ x.full_stack_name for x in deps ]
    self.pretty_print(result)
    return 0

  def cmd_stack_all_up(self) -> int:
    stack = self.get_required_selected_stack()
    deps = stack.get_stack_build_order()
    for build_stack in deps + [ stack ]:
      stack_name = build_stack.stack_name
      project = build_stack.project
      print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
      print(f"     Building xpulumi project {project.name}, stack {stack_name}", file=sys.stderr)
      print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
      project.init_stack(stack_name)
      rc = project.call_project_pulumi(['up'], stack_name=stack_name)
      if rc != 0:
        return rc
    return 0

  def cmd_stack_select(self) -> int:
    args = self._args
    stack_name: str = args.default_stack
    self.update_config(default_stack=stack_name)
    return 0

  def run(self) -> int:
    """Run the xpulumi command-line tool with provided arguments

    Args:
        argv (Optional[Sequence[str]], optional):
            A list of commandline arguments (NOT including the program as argv[0]!),
            or None to use sys.argv[1:]. Defaults to None.

    Returns:
        int: The exit code that would be returned if this were run as a standalone command.
    """
    parser = argparse.ArgumentParser(description="Manage pulumi-based projects.")

    # ======================= Main command

    self._parser = parser
    parser.add_argument('--traceback', "--tb", action='store_true', default=False,
                        help='Display detailed exception information')
    parser.add_argument('-M', '--monochrome', action='store_true', default=False,
                        help='Output to stdout/stderr in monochrome. Default is to colorize if stream is a compatible terminal')
    parser.add_argument('-c', '--compact', action='store_true', default=False,
                        help='Compact instead of pretty-printed output')
    parser.add_argument('-r', '--raw', action='store_true', default=False,
                        help='''Output raw strings and binary content directly, not json-encoded.
                                Values embedded in structured results are not affected.''')
    parser.add_argument('-o', '--output', dest="output_file", default=None,
                        help='Write output value to the specified file instead of stdout')
    parser.add_argument('--text-encoding', default='utf-8',
                        help='The encoding used for text. Default  is utf-8')
    parser.add_argument('-C', '--cwd', default='.',
                        help="Change the effective directory used to search for configuration")
    parser.add_argument('--config',
                        help="Specify the location of the config file")
    parser.add_argument('-b', '--backend', default=None,
                        help='Specify the local name of the backend to operate on. Default is the configured default backend')
    parser.set_defaults(func=self.cmd_bare)

    subparsers = parser.add_subparsers(
                        title='Commands',
                        description='Valid commands',
                        help='Additional help available with "xpulumi <command-name> -h"')


    # ======================= version

    parser_version = subparsers.add_parser('version',
                            description='''Display version information. JSON-quoted string. If a raw string is desired, use -r.''')
    parser_version.set_defaults(func=self.cmd_version)

    # ======================= project_root_dir

    parser_project_root_dir = subparsers.add_parser('project-root-dir',
                            description='''Display The project root directory. JSON-quoted string. If a raw string is desired, use -r.''')
    parser_project_root_dir.set_defaults(func=self.cmd_project_root_dir)

    # ======================= init-env

    parser_init_env = subparsers.add_parser('init-env',
                            description='''Initialize a new overall GitHub project environment.''')
    parser_init_env.add_argument('--phase-two', action='store_true', default=False,
                        help='Indicates phase-2 of init-env, running in the target environment. Internal use only.')
    parser_init_env.add_argument('--subaccount', default=None,
                        help='Specify a subaccount name to prevent collision '
                             'with duplicate stacks running in the same AWS account. Default is None.')
    parser_init_env.add_argument('-p', '--xpulumi-package', default=None,
                        help='Specify the package spec (as used by pip install) '
                             'to be used for xpulumi package updates. Default is latest stable.')
    parser_init_env.set_defaults(func=self.cmd_init_env)

    # ======================= run

    parser_run = subparsers.add_parser('run',
                            description='''Run a command, optionally in group or with sudo.''')
    parser_run.add_argument('-g', '--group', dest="run_with_group", default=None,
                        help='Run with membership in the specified OS group, using sudo if current process has not picked up membership')
    parser_run.add_argument('--sudo', dest="use_sudo", action='store_true', default=False,
                        help='''Run with sudo.''')
    parser_run.add_argument('--sudo-reason', default=None,
                        help='Provide a reason for why sudo is needed, if it turns out to be needed')
    parser_run.add_argument('cmd_and_args', nargs=argparse.REMAINDER,
                        help='Command and arguments as would be provided to sudo.')
    parser_run.set_defaults(func=self.cmd_run)

    # ======================= update-pulumi

    parser_update_pulumi = subparsers.add_parser('update-pulumi', description="Update the Pulumi CLI to the latest version.")
    parser_update_pulumi.set_defaults(func=self.cmd_update_pulumi)

    # ======================= test

    parser_test = subparsers.add_parser('test', description="Run a simple test. For debugging only.  Will be removed.")
    parser_test.set_defaults(func=self.cmd_test)

    # ======================= backend

    parser_backend = subparsers.add_parser('backend',
                            description='''Subcommands related to management of pulumi backends.''')
    backend_subparsers = parser_backend.add_subparsers(
                        title='Subcommands',
                        description='Valid backend subcommands',
                        help='Additional help available with "xpulumi backend <subcommand-name> -h"')

    # ======================= backend create

    parser_be_create = backend_subparsers.add_parser('create',
                            description='''Create a backend.''')
    parser_be_create.add_argument('--new-s3-bucket-project', default=None,
                        help='If an S3 backend, create a new project with the given name and a "global" stack on the current backend that creates and manages the bucket.')
    parser_be_create.add_argument('new_backend',
                        help='The new backend name')
    parser_be_create.add_argument('new_backend_uri', nargs='?', default=None,
                        help='The backend URI, either file: or s3:. If simply "file", creates a backend in the backend dir. Default is "file"')
    parser_be_create.set_defaults(func=self.cmd_be_create)

    # ======================= backend select

    parser_be_select = backend_subparsers.add_parser('select',
                            description='''Select a default backend.''')
    parser_be_select.add_argument('default_backend',
                        help='The new default backend name')
    parser_be_select.set_defaults(func=self.cmd_be_select)

    # ======================= project

    parser_project = subparsers.add_parser('project',
                            description='''Subcommands related to management of pulumi projects.''')
    parser_project.add_argument('-p', '--project', default=None,
                        help='Specify the project to operate on. Default is the current project directory')
    project_subparsers = parser_project.add_subparsers(
                        title='Subcommands',
                        description='Valid project subcommands',
                        help='Additional help available with "xpulumi project <subcommand-name> -h"')

    # ======================= project create

    parser_prj_create = project_subparsers.add_parser('create',
                            description='''Create a project.''')
    parser_prj_create.add_argument('new_project',
                        help='The new project name')
    parser_prj_create.add_argument('-b', '--backend', default=None,
                        help='The name of xpulumi backend that hosts project stacks; by default the default xpulumi backend will be used.')
    parser_prj_create.add_argument('-P', '--pulumi-project-name', default=None,
                        help='The name of the pulumi project; by default the same as the xpulumi project name.')
    parser_prj_create.add_argument('-g', '--organization', default=None,
                        help='The name of the backend organization associated with the project; by default, the default for the backend is used.')
    parser_prj_create.add_argument('-r', '--ref', dest="new_project_is_ref", action='store_true', default=False,
                        help='''The new project is a reference to an externally managed project; no Pulumi project stubs will be created.''')
    parser_prj_create.set_defaults(func=self.cmd_prj_create)

    # ======================= stack

    parser_stack = subparsers.add_parser('stack',
                            description='''Subcommands related to management of pulumi stacks.''')
    parser_stack.add_argument('-p', '--project', default=None,
                        help='Specify the project to operate on. Default is the current project directory')
    parser_stack.add_argument('-s', '--stack', default=None,
                        help='Specify the stack to operate on. Default is the configured default stack')
    stack_subparsers = parser_stack.add_subparsers(
                        title='Subcommands',
                        description='Valid stack subcommands',
                        help='Additional help available with "xpulumi stack <subcommand-name> -h"')

    # ======================= stack select

    parser_stack_select = stack_subparsers.add_parser('select',
                            description='''Select a default stack.''')
    parser_stack_select.add_argument('default_stack',
                        help='The new default stack name')
    parser_stack_select.set_defaults(func=self.cmd_stack_select)

    # ======================= stack dependencies

    parser_stack_dependencies = stack_subparsers.add_parser('dependencies',
                            description='''List the stack dependencies of the selected stack.''')
    parser_stack_dependencies.set_defaults(func=self.cmd_stack_dependencies)

    # ======================= stack all-up

    parser_stack_all_up = stack_subparsers.add_parser('all-up',
                            description='''Perform "pulumi up" on this stack and all prerequisite stacks, in order.''')
    parser_stack_all_up.set_defaults(func=self.cmd_stack_all_up)

    # =========================================================

    argcomplete.autocomplete(parser)
    try:
      args = parser.parse_args(self._argv)
    except ArgparseExitError as ex:
      return ex.exit_code
    traceback: bool = args.traceback
    try:
      self._args = args
      self._raw_stdout = sys.stdout
      self._raw_stderr = sys.stderr
      self._raw = args.raw
      self._compact = args.compact
      self._output_file = args.output_file
      self._encoding = args.text_encoding
      monochrome: bool = args.monochrome
      if not monochrome:
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
      self._cwd = os.path.abspath(os.path.expanduser(args.cwd))
      config_file: Optional[str] = args.config
      if not config_file is None:
        self._config_file = self.abspath(config_file)
      rc = args.func()
    except Exception as ex:
      if isinstance(ex, CmdExitError):
        rc = ex.exit_code
      else:
        rc = 1
      if rc != 0:
        if traceback:
          raise

        print(f"{self.ecolor(Fore.RED)}xpulumi: error: {ex}{self.ecolor(Style.RESET_ALL)}", file=sys.stderr)
    return rc

  @property
  def args(self) -> argparse.Namespace:
    return self._args

def run(argv: Optional[Sequence[str]]=None) -> int:
  try:
    rc = CommandLineInterface(argv).run()
  except CmdExitError as ex:
    rc = ex.exit_code
  return rc

class CommandHandler:
  cli: CommandLineInterface
  args: argparse.Namespace

  def __init__(self, cli: CommandLineInterface):
    self.cli = cli
    self.args = cli.args

  def __call__(self) -> int:
    raise NotImplementedError(f"{full_type(self)} has not implemented __call__")
