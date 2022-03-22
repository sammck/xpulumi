#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Command-line interface for xpulumi package"""


import base64
from typing import Optional, Sequence, List, Union, Dict, TextIO, Mapping, MutableMapping, cast, Any, Iterator, Iterable, Tuple

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
import ruamel.yaml
from io import StringIO

from xpulumi.config import XPulumiConfig
from xpulumi.context import XPulumiContext
from xpulumi.exceptions import XPulumiError
from xpulumi.base_context import XPulumiContextBase
from xpulumi.backend import XPulumiBackend
from xpulumi.installer.util import file_contents

# NOTE: this module runs with -m; do not use relative imports
from xpulumi.internal_types import JsonableTypes
from xpulumi.constants import XPULUMI_CONFIG_DIRNAME, XPULUMI_CONFIG_FILENAME_BASE

from xpulumi import (
    __version__ as pkg_version,
    Jsonable,
    JsonableDict,
    JsonableList,
  )

from xpulumi.util import (
    full_name_of_type,
    full_type,
    get_git_root_dir,
    append_lines_to_file_if_missing,
    file_url_to_pathname,
    pathname_to_file_url
  )

def is_colorizable(stream: TextIO) -> bool:
  is_a_tty = hasattr(stream, 'isattry') and stream.isatty()
  return is_a_tty


class CmdExitError(RuntimeError):
  exit_code: int

  def __init__(self, exit_code: int, msg: Optional[str]=None):
    if msg is None:
      msg = f"Command exited with return code {exit_code}"
    super(msg)
    self.exit_code = exit_code

class ArgparseExitError(CmdExitError):
  pass

class NoExitArgumentParser(argparse.ArgumentParser):
  def exit(self, status=0, message=None):
    if message:
        self._print_message(message, sys.stderr)
    raise ArgparseExitError(status, message)

class RoundTripConfig(MutableMapping[str, Any]):
  _config_file: str
  _text: str
  _data: MutableMapping
  _yaml: Optional[ruamel.yaml.YAML] = None

  def __init__(self, config_file: str):
    self._config_file = config_file
    text = file_contents(config_file)
    self._text = text
    if config_file.endswith('.yaml'):
      self._yaml = ruamel.yaml.YAML()
      self._data = self._yaml.load(text)
    else:
      self._data = json.loads(text)

  @property
  def data(self) -> MutableMapping:
    return self._data

  def save(self):
    if self._yaml is None:
      text = json.dumps(cast(JsonableDict, self.data), indent=2, sort_keys=True)
    else:
      with StringIO() as output:
        self._yaml.dump(self.data, output)
        text = output.getvalue()
    if not text.endswith('\n'):
      text += '\n'
    if text != self._text:
      with open(self._config_file, 'w') as f:
        f.write(text)

  def __setitem__(self, key: str, value: Any):
    self.data[key] = value

  def __getitem__(self, key: str) -> Any:
    return self.data[key]

  def __delitem__(self, key:str) -> None:
    del self.data[key]

  def __iter__(self) -> Iterator[Any]:
    return iter(self.data)

  def __len__(self) -> int:
    return len(self.data)

  def __contains__(self, key: str) -> bool:
    return key in self.data

  def keys(self) -> Iterable[str]:
    return self.data.keys()

  def values(self) -> Iterable[Any]:
    return self.data.values()

  def items(self) -> Iterable[Tuple[str, Any]]:
    return self.data.items()

  def update(self, *args, **kwargs) -> None:
    if len(args) > 0:
      assert len(args) == 1
      assert len(kwargs) == 0
      for k, v in kwargs.items():
        self.data[k] = v
    else:
      for k, v in kwargs.items():
        self.data[k] = v

class CommandHandler:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _cfg: Optional[XPulumiConfig] = None
  _ctx: Optional[XPulumiContextBase] = None

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self._argv = argv

  def ocolor(self, codes: str) -> str:
    return codes if self._colorize_stdout else ""

  def ecolor(self, codes: str) -> str:
    return codes if self._colorize_stderr else ""


  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self._cwd, os.path.expanduser(path)))

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
          proc.communicate(input=json.dumps(value, separators=(',', ':'), sort_keys=True).encode('utf-8'))
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

  def cmd_run(self) -> int:
    from xpulumi.installer.util import sudo_call
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

  def cmd_init_env(self) -> int:
    from xpulumi.installer.docker import install_docker
    from xpulumi.installer.aws_cli import install_aws_cli
    from xpulumi.installer.gh import install_gh
    from xpulumi.installer.pulumi import install_pulumi
    from xpulumi.installer.poetry import install_poetry
    from xpulumi.installer.util import sudo_call
    from xpulumi.installer.os_packages import PackageList

    args = self._args

    pl = PackageList()
    pl.add_packages_if_missing(['build-essential', 'meson', 'ninja-build', 'python3.8', 'python3.8-venv', 'sqlcipher'])
    pl.add_package_if_cmd_missing('sha256sum', 'coreutils')
    pl.add_package_if_cmd_missing('curl')
    pl.add_package_if_cmd_missing('git')
    pl.install_all()

    install_docker()
    install_aws_cli()
    install_gh()

    project_dir = get_git_root_dir(self._cwd)
    if project_dir is None:
      raise XPulumiError("Could not locate Git project root directory; please run inside git working directory or use -C")

    install_poetry()

    append_lines_to_file_if_missing(os.path.join(project_dir, ".gitignore"), ['.xpulumi/', '.secret-kv/'], create_file=True)
    xpulumi_dir = os.path.join(project_dir, XPULUMI_CONFIG_DIRNAME)
    if not os.path.exists(xpulumi_dir):
      os.mkdir(xpulumi_dir)
    xpulumi_config_file_yaml = os.path.join(xpulumi_dir, XPULUMI_CONFIG_FILENAME_BASE + ".yaml")
    xpulumi_config_file_json = os.path.join(xpulumi_dir, XPULUMI_CONFIG_FILENAME_BASE + ".json")
    if os.path.exists(xpulumi_config_file_yaml):
      config_file = xpulumi_config_file_yaml
    elif os.path.exists(xpulumi_config_file_json):
      config_file = xpulumi_config_file_json
    else:
      config_file = xpulumi_config_file_yaml
      new_config_data: JsonableDict = dict()
      with open(config_file, 'w') as f:
        yaml.dump(new_config_data, f)
    cfg = XPulumiConfig(config_file)
    xpulumi_dir = os.path.join(cfg.xpulumi_dir, '.pulumi')
    install_pulumi(xpulumi_dir, min_version='latest')
    project_root_dir = cfg.project_root_dir
    secret_kv_dir = os.path.join(project_root_dir, '.secret-kv')
    if not os.path.exists(secret_kv_dir):
      create_kv_store(project_root_dir)

    return 0

  def get_config(self) -> XPulumiConfig:
    if self._cfg is None:
      self._cfg = XPulumiConfig(starting_dir=self._cwd)
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

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_xpulumi_data_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), 'xpulumi.d')

  def get_project_dir(self, project_name: str) -> str:
    return os.path.join(self.get_xpulumi_data_dir(), 'project', project_name)

  def get_backend_dir(self, backend: str) -> str:
    return os.path.join(self.get_xpulumi_data_dir(), 'backend', backend)

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
    with open(os.path.join(backend_pathname, '.gitignore'), 'w') as f:
      print("!.pulumi/", file=f)
    backend_data: JsonableDict = dict(
        name=new_backend,
        uri=rel_backend_uri,
        options=dict(
          includes_organization=False,
          includes_project=False,
        )
      )
    with open(os.path.join(backend_dir, "backend.json"), 'w') as f:
      print(json.dumps(backend_data, indent=2, sort_keys=True), file=f)

  def create_s3_backend(self, new_backend: str, new_backend_uri: str, new_s3_bucket_project: Optional[str]=None, new_s3_bucket_backend: Optional[str]=None):
    raise NotImplementedError()


  def cmd_be_create(self) -> int:
    args = self._args
    new_s3_bucket_project: Optional[str] = args.new_s3_bucket_project,
    new_s3_bucket_backend: optional[str] = args.backend
    new_backend: str = args.new_backend
    new_backend_uri: Optional[str] = args.new_backend_uri
    if new_backend_uri is None or new_backend_uri == "file" or new_backend_uri == "file:" or new_backend_uri == "file://":
      new_backend_uri = "file://./state"
    parts = urlparse(new_backend_uri)
    if parts.scheme == 'file':
      self.create_file_backend(new_backend, new_backend_uri)
    elif parts.scheme == 's3':
      self.create_s3_backend(new_backend, new_backend_uri, new_s3_bucket_project=new_s3_bucket_project, new_s3_bucket_backend=new_s3_bucket_backend)
    else:
      raise XPulumiError(f"Cannot create a new backend with scheme {parts.scheme}")
    return 0

  def cmd_be_select(self) -> int:
    args = self._args
    backend_name: str = args.default_backend
    backend = XPulumiBackend(backend_name)
    self.update_config(default_backend=backend_name)
    return 0

  def cmd_prj_create(self) -> int:
    raise NotImplementedError()


  def run(self) -> int:
    """Run the xpulumi command-line tool with provided arguments

    Args:
        argv (Optional[Sequence[str]], optional):
            A list of commandline arguments (NOT including the program as argv[0]!),
            or None to use sys.argv[1:]. Defaults to None.

    Returns:
        int: The exit code that would be returned if this were run as a standalone command.
    """
    import argparse

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
                            description='''Display version information. JSON-quoted string. If a raw string is desired, user -r.''')
    parser_version.set_defaults(func=self.cmd_version)

    # ======================= init-env

    parser_init_env = subparsers.add_parser('init-env', 
                            description='''Initialize a new overall GitHub project environment.''')
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
    parser_prj_create.add_argument('-p', '--pulumi-project-name', default=None,
                        help='The name of the pulumi project; by default the same as the xpulumi project name.')
    parser_prj_create.add_argument('-g', '--organization', default=None,
                        help='The name of the backend organization associated with the project; by default, the default for the backend is used.')
    parser_prj_create.add_argument('-r', '--ref', dest="new_project_is_ref", action='store_true', default=False,
                        help='''The new project is a reference to an externally managed project; no Pulumi project stubs will be created.''')
    parser_prj_create.set_defaults(func=self.cmd_prj_create)

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
            sys.stdout = colorama.AnsiToWin32(sys.stdout)
          if self._colorize_stderr:
            sys.stderr = colorama.AnsiToWin32(sys.stderr)

        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
          self._colorize_stdout = True
        if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
          self._colorize_stderr = True
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

def run(argv: Optional[Sequence[str]]=None) -> int:
  try:
    rc = CommandHandler(argv).run()
  except CmdExitError as ex:
    rc = ex.exit_code
  return rc

# allow running with "python3 -m", or as a standalone script
if __name__ == "__main__":
  sys.exit(run())
