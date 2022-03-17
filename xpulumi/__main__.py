#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Command-line interface for xpulumi package"""


import base64
from typing import Optional, Sequence, List, Union, Dict, TextIO

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

from xpulumi.config import XPulumiConfig
from xpulumi.exceptions import XPulumiError

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
  )
from xpulumi.pulumi_cli import install_pulumi

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

class CommandHandler:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

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

  def cmd_init_env(self) -> int:
    args = self._args

    project_dir = get_git_root_dir(self._cwd)
    if project_dir is None:
      raise XPulumiError("Could not locate Git project root directory; please run inside git working directory or use -C")
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
    parser.set_defaults(func=self.cmd_bare)

    subparsers = parser.add_subparsers(
                        title='Commands',
                        description='Valid commands',
                        help='Additional help available with "<command-name> -h"')

                
    # ======================= version

    parser_version = subparsers.add_parser('version', 
                            description='''Display version information. JSON-quoted string. If a raw string is desired, user -r.''')
    parser_version.set_defaults(func=self.cmd_version)

    # ======================= version

    parser_init_env = subparsers.add_parser('init-env', 
                            description='''Initialize a new overall GitHub project environment.''')
    parser_init_env.set_defaults(func=self.cmd_init_env)

    # ======================= test

    parser_test = subparsers.add_parser('test', description="Run a simple test. For debugging only.  Will be removed.")
    parser_test.set_defaults(func=self.cmd_test)


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
