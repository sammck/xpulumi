#!/usr/bin/env python3

import argparse
import base64
import json
import os
import subprocess
import sys
from base64 import b64decode, b64encode
from io import StringIO, TextIOWrapper
from pathlib import Path
from typing import (Any, Dict, Iterable, Iterator, List, Mapping,
                    MutableMapping, Optional, Sequence, TextIO, Tuple, Union,
                    cast)
from urllib.parse import ParseResult, urlparse

import argcomplete  # type: ignore[import]
import boto3
import boto3.session
import colorama  # type: ignore[import]
import ruamel.yaml
import yaml
import yaml.parser
from boto3_type_annotations.s3 import Client as S3Client
from boto3_type_annotations.s3 import ServiceResource as S3Resource
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
    from yaml import Loader as YamlLoader, Dumper as YamlDumper

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

class PulumiCommandWrapper:
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _cfg: Optional[XPulumiConfig] = None
  _ctx: Optional[XPulumiContextBase] = None
  _project_name: Optional[str] = None
  _backend_name: Optional[str] = None
  _stack_name: Optional[str] = None
  _project: Optional[XPulumiProject] = None
  _backend: Optional[XPulumiBackend] = None
  _have_backend_name: bool = False
  _have_project_name: bool = False
  _have_stack_name: bool = False

  _env: Optional[Dict[str, str]] = None

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self._argv = argv
    self._cwd = os.getcwd()

  def abspath(self, path: str) -> str:
    return os.path.abspath(os.path.join(self._cwd, os.path.expanduser(path)))

  def get_config(self) -> XPulumiConfig:
    if self._cfg is None:
      self._cfg = XPulumiConfig(starting_dir=self._cwd)
    return self._cfg

  def get_config_file(self) -> str:
    return self.get_config().config_file  

  def get_context(self) -> XPulumiContextBase:
    if self._ctx is None:
      self._ctx = self.get_config().create_context()
    return self._ctx

  def get_optional_stack_name(self) -> Optional[str]:
    return self._stack_name

  def set_stack_name(self, stack_name: str) -> Optional[str]:
    self._stack_name = stack_name
    self._have_stack_name = True

  def get_stack_name(self) -> str:
    if self._stack_name is None:
      raise XPulumiError("A stack name is required")
    return self._stack_name

  def get_optional_project_name(self) -> Optional[str]:
    if not self._have_project_name:
      self._project_name = self.get_context().get_optional_project_name()
      self._have_project_name = True
    return self._project_name

  def get_project_name(self) -> str:
    if self._project_name is None:
      self._project_name = self.get_context().get_project_name()
    return self._project_name

  def get_optional_project(self) -> Optional[XPulumiProject]:
    if self._project is None:
      project_name = self.get_optional_project_name()
      if not project_name is None:
        self._project = self.get_context().get_project(project_name)
    return self._project

  def get_project(self) -> XPulumiProject:
    if self._project is None:
      self._project = self.get_context().get_project(self.get_project_name())
    return self._project

  def get_optional_backend_name(self) -> Optional[str]:
    if not self._have_backend_name:
      self._backend_name = self.get_context().get_optional_backend_name()
      self._have_backend_name = True
    return self._backend_name

  def get_backend_name(self) -> str:
    if not self._backend_name is None:
      self._backend_name = self.get_context().get_backend_name()
      self._have_backend_name = True
    return self._backend_name

  def get_optional_backend(self) -> Optional[XPulumiBackend]:
    if self._backend is None:
      backend_name = self.get_optional_backend_name()
      if not backend_name is None:
        self._backend = self.get_context().get_backend(backend_name)
    return self._backend

  def get_backend(self) -> XPulumiBackend:
    if self._backend is None:
      self._backend = self.get_context().get_backend(self.get_backend_name())
    return self._backend

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_xpulumi_data_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), 'xpulumi.d')

  def get_project_dir(self, project_name: Optional[str]=None) -> str:
    if project_name is None:
      project_name = self.get_project_name()
    return self.get_context().get_project_infra_dir(project_name)

  def get_backend_dir(self, backend_name: Optional[str]=None) -> str:
    if backend_name is None:
      backend_name = self.get_backend_name()
    return self.get_context().get_backend_infra_dir(backend_name)

  def get_environ(self) -> Dict[str, str]:

    if self._env is None:
      ctx = self.get_context()
      env = dict(os.environ)
      self._env = env
      env['PULUMI_HOME'] = ctx.get_pulumi_home()
      project = self.get_optional_project()
      backend: Optional[XPulumiBackend] = None
      if project is None:
        if 'PULUMI_BACKEND_URL' in env:
          del env['PULUMI_BACKEND_URL']
        if 'PULUMI_ACCESS_TOKEN' in env:
          del env['PULUMI_ACCESS_TOKEN']
      else:
        env['PULUMI_BACKEND_URL'] = project.get_project_backend_url()
        backend = project.backend
        if backend.scheme == 'https' or backend.scheme == 'http':
          env['PULUMI_ACCESS_TOKEN'] = backend.require_access_token()
        else:
          if 'PULUMI_ACCESS_TOKEN' in env:
            del env['PULUMI_ACCESS_TOKEN']
        stack_name = self.get_optional_stack_name()
      if not 'PULUMI_CONFIG_PASSPHRASE' in env:
        passphrase: Optional[str] = None
        if not backend is None:
          try:
            passphrase = ctx.get_pulumi_secret_passphrase(backend_url=backend.url, organization=project.organization, project=project.name, stack=stack_name)
          except XPulumiError:
            pass
        if passphrase is None:
          try:
            passphrase = ctx.get_simple_kv_secret('pulumi/passphrase')
          except Exception:
            pass
        if not passphrase is None:
          env['PULUMI_CONFIG_PASSPHRASE'] = passphrase
    return self._env

  def _fix_raw_popen_args(self, arglist: List[str], kwargs: Dict[str, Any]) -> List[str]:
    arglist = [ self.get_context().get_pulumi_cli() ] + arglist
    env = self.get_environ()
    call_env = kwargs.pop('env', None)
    if not call_env is None:
      env.update(call_env)
    kwargs['env'] = env
    project = self.get_optional_project()
    if project is None:
      cwd = self._cwd
    else:
      cwd = project._project_dir
    kwargs['cwd'] = cwd
    return arglist

  def raw_pulumi_Popen(self, arglist: List[str], **kwargs) -> subprocess.Popen:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.Popen(arglist, **kwargs)

  def raw_pulumi_check_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.check_call(arglist, **kwargs)

  def raw_pulumi_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.call(arglist, **kwargs)

  def parse_args(self, arglist: Optional[List[str]]=None) -> argparse.Namespace:
    pctx = self

    class MyParser(NoExitArgumentParser):
      def print_help(self, file=None) -> int:
        pctx.raw_pulumi_check_call([ '--help' ])

      def print_usage(self, file=None) -> int:
        pctx.raw_pulumi_check_call([ '--help' ])

    parser = MyParser(description='Pulumi wrapper.')

    parser.add_argument('--color', default=None,
                        help='Colorize output. Choices are: always, never, raw, auto (default "auto")')
    parser.add_argument('--cwd', '-C', default=None,
                        help='Run pulumi as if it had been started in another directory')
    parser.add_argument('--disable-integrity-checking', action='store_true', default=False,
                        help='Disable integrity checking of checkpoint files')
    parser.add_argument('--emoji', '-e', action='store_true', default=False,
                        help='Enable emojis in the output')
    parser.add_argument('--logflow', action='store_true', default=False,
                        help='Flow log settings to child processes (like plugins)')
    parser.add_argument('--logtostderr', action='store_true', default=False,
                        help='Log to stderr instead of to files')
    parser.add_argument('--non-interactive', action='store_true', default=False,
                        help='Disable interactive mode for all commands')
    parser.add_argument('--profiling', default=None,
                        help='Emit CPU and memory profiles and an execution trace to \'[filename].[pid].{cpu,mem,trace}\', respectively')
    parser.add_argument('--tracing', default=None,
                        help='Emit tracing to the specified endpoint. Use the file: scheme to write tracing data to a local file')
    parser.add_argument('--verbose', '-v', type=int, default=None,
                        help='Enable verbose logging (e.g., v=3); anything >3 is very verbose')
    parser.add_argument('subcommand', nargs=argparse.REMAINDER, default=[])

    args = parser.parse_args(arglist)

    arglist: List[str] = []
    if not args.color is None:
      arglist.extend(['--color', args.color])
    if not args.cwd is None:
      raise RuntimeError("--cwd, -C are not supported by the pulumi wrapper")
      # arglist.extend(['--cwd', args.cwd])
    if args.emoji:
      arglist.extend(['--emoji'])
    if args.logflow:
      arglist.extend(['--logflow'])
    if args.logtostderr:
      arglist.extend(['--logtostderr'])
    if args.non_interactive:
      arglist.extend(['--non-interactive'])
    if not args.profiling is None:
      arglist.extend(['--profiling', args.profiling])
    if not args.tracing is None:
      arglist.extend(['--tracing', args.tracing])
    if not args.verbose is None:
      self.verbose_level = args.verbose
      arglist.extend(['--verbose', str(args.verbose)])
    args.global_option_arglist = arglist

    return args

  def cmd_about(self, args: List[str], global_options: argparse.Namespace) -> int:
    print(f"Environment variables: {json.dumps(self.get_environ(), sort_keys=True, indent=2)}")
    return self.raw_pulumi_call(global_options.global_option_arglist + [ 'about' ] + args)

  def pulumi_call(self, arglist: Optional[List[str]]=None, **kwargs) -> int:
    args = self.parse_args(arglist)
    self._args = args
    cwd: Optional[str] = args.cwd
    if cwd is None:
      cwd = os.getcwd()
    self._cwd = cwd

    if len(args.subcommand) > 0:
      cmd = args.subcommand[0]
      remargs = args.subcommand[1:]
      if cmd == 'about':
        return self.cmd_about(remargs, args)

    arglist = args.global_option_arglist + args.subcommand

    exit_code = self.raw_pulumi_call(arglist)
    return exit_code

def run(argv: Optional[Sequence[str]]=None) -> int:
  try:
    rc = PulumiCommandWrapper(argv).pulumi_call()
  except CmdExitError as ex:
    rc = ex.exit_code
  return rc

if __name__ == '__main__':
  sys.exit(run())
