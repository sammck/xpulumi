# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""xpulumi CLI"""

import base64
from typing import (Optional, Sequence, TextIO, Type, cast )

import os
import sys
import argparse
import json
import colorama # type: ignore[import]
from colorama import Fore, Back, Style
import subprocess

from project_init_tools import (
    full_type,
    find_command_in_path,
  )

from .exceptions import XPulumiInstallerError
from .internal_types import Jsonable
from .version import __version__ as pkg_version

def is_colorizable(stream: TextIO) -> bool:
  is_a_tty = hasattr(stream, 'isattry') and stream.isatty()
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

  _raw_stdout: TextIO = sys.stdout
  _raw_stderr: TextIO = sys.stderr
  _raw: bool = False
  _compact: bool = False
  _output_file: Optional[str] = None
  _encoding: str = 'utf-8'
  _config_file: Optional[str] = None

  _colorize_stdout: bool = False
  _colorize_stderr: bool = False
  _jq_prog: Optional[str] = None
  _checked_jq_prog: bool = False

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

  def get_jq_prog(self) -> Optional[str]:
    if not self._checked_jq_prog:
      self._jq_prog = find_command_in_path('jq', cwd=self.cwd)
      self._checked_jq_prog = True
    return self._jq_prog

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
    jq_prog = self.get_jq_prog()

    def emit_to(f: TextIO):
      final_colorize = not jq_prog is None and colorize and ((f is sys.stdout and self._colorize_stdout) or (f is sys.stderr and self._colorize_stderr))

      if not final_colorize:
        if compact:
          json.dump(value, f, separators=(',', ':'), sort_keys=True)
        else:
          json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')
      else:
        jq_input = json.dumps(value, separators=(',', ':'), sort_keys=True)
        assert not jq_prog is None
        cmd = [ jq_prog ]
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
    raise XPulumiInstallerError('A command is required')

  def cmd_install(self) -> int:
    from .cmd_install import CmdInstall as cmd_class # pylint: disable=cyclic-import
    return self.run_cmd_class(cmd_class)

  def cmd_version(self) -> int:
    self.pretty_print(pkg_version)
    return 0

  def run_cmd_class(self, cmd_class: Type['CommandHandler']):
    exit_code = cmd_class(self)()
    return exit_code

  def run(self) -> int:
    """Run the xpulumi-installer command-line tool with provided arguments

    Args:
        argv (Optional[Sequence[str]], optional):
            A list of commandline arguments (NOT including the program as argv[0]!),
            or None to use sys.argv[1:]. Defaults to None.

    Returns:
        int: The exit code that would be returned if this were run as a standalone command.
    """
    parser = argparse.ArgumentParser(description="Install xpulumi-based projects.")

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
    parser.set_defaults(func=self.cmd_bare)

    subparsers = parser.add_subparsers(
                        title='Commands',
                        description='Valid commands',
                        help='Additional help available with "xpulumi-installer <command-name> -h"')

    # ======================= version

    parser_version = subparsers.add_parser('version',
                            description='''Display version information. JSON-quoted string. If a raw string is desired, use -r.''')
    parser_version.set_defaults(func=self.cmd_version)

    # ======================= install

    parser_install = subparsers.add_parser('install',
                            description='''Install a new xpulumi project in the current git directory''')
    parser_install.add_argument('-p', '--package', default='.',
                        help="Specify the pip package for the desired version of xpulumi")
    parser_install.set_defaults(func=self.cmd_install)

    # =========================================================

    #argcomplete.autocomplete(parser)
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
            sys.stdout = cast(TextIO, colorama.AnsiToWin32(sys.stdout))
          if self._colorize_stderr:
            sys.stderr = cast(TextIO, colorama.AnsiToWin32(sys.stderr))

        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
          self._colorize_stdout = True
        if hasattr(sys.stderr, "isatty") and sys.stderr.isatty():
          self._colorize_stderr = True
      self._cwd = os.path.abspath(os.path.expanduser(args.cwd))
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
