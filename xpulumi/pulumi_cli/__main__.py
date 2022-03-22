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
from secret_kv import create_kv_store
from xpulumi.backend import XPulumiBackend
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
    super(msg)
    self.exit_code = exit_code

class ArgparseExitError(CmdExitError):
  pass

class NoExitArgumentParser(argparse.ArgumentParser):
  def exit(self, status=0, message=None):
    if message:
        self._print_message(message, sys.stderr)
    raise ArgparseExitError(status, message)

class PulumiCommandWrapper:
  main_header_notes = f"# AUTO-GENERATED--DO NOT EDIT! Main Pulumi config file for project {self.settings.project}"
  stack_header_notes = f"# AUTO-GENERATED--DO NOT EDIT! Stack {self.cfg.pulumi_project}-{self.cfg.pulumi_stack} Pulumi config file for project {self.settings.project}"
  _argv: Optional[Sequence[str]]
  _parser: argparse.ArgumentParser
  _args: argparse.Namespace
  _cwd: str

  _cfg: Optional[XPulumiConfig] = None
  _ctx: Optional[XPulumiContextBase] = None

  def __init__(self, argv: Optional[Sequence[str]]=None):
    self._argv = argv

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

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_xpulumi_data_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), 'xpulumi.d')

  def get_project_name(self, project_name: Optional[str]=None) -> str:
    if project_name is None:
      project_name = self.get_context().get
  def get_project_dir(self, project_name: Optional[str]=None) -> str:
    return os.path.join(self.get_xpulumi_data_dir(), 'project', project_name)

  def get_backend_dir(self, backend: str) -> str:
    return os.path.join(self.get_xpulumi_data_dir(), 'backend', backend)


  def global_sess(self) -> boto3.session.Session:
    if self._global_sess is None:
      self._global_sess = get_global_aws_session(self.settings)
    return self._global_sess

  def env_sess(self) -> boto3.session.Session:
    if self._env_sess is None:
      self._env_sess = get_aws_session(self.settings)
    return self._env_sess

  def global_s3(self) -> S3Client:
    if self._global_s3 is None:
      sess = self.global_sess()
      self._global_s3: S3Client = sess.client('s3')
    return self._global_s3

  def global_s3_resource(self) -> S3Resource:
    if self._global_s3_resource is None:
      sess = self.global_sess()
      self._global_s3_resource: S3Resource = sess.resource('s3')
    return self._global_s3_resource

  def global_s3_bucket_exists(self, bucket_name: str):
    try:
      self.global_s3().head_bucket(Bucket=bucket_name)
    except ClientError:
      # The bucket does not exist or you have no access.
      return False
    return True

  def global_s3_create_bucket(self, bucket_name: str, region: Optional[str]=None):
    s3 = self.global_s3()
    if region is None or region == '':
      region = s3.meta.region_name
    cfg = { 'LocationConstraint': region }
    resp = s3.create_bucket(
        Bucket=bucket_name,
        ACL='private',
        CreateBucketConfiguration=cfg
      )
    s3_resource = self.global_s3_resource()
    bucket_obj = s3_resource.Bucket(bucket_name)
    bucket_obj.wait_until_exists()

  def global_s3_create_bucket_if_missing(self, bucket_name: str, region: Optional[str]=None) -> bool:
    if not self.global_s3_bucket_exists(bucket_name):
      self.global_s3_create_bucket(bucket_name, region=region)
      return True
    return False

  def read_text_file(self, pathname: str) -> str:
    with open(pathname) as f:
      result = f.read()
    return result

  def write_text_file(self, pathname: str, data: str, end_with_newline: bool=True):
    with open(pathname, 'w') as f:
      f.write(data)
      if end_with_newline and not data.endswith('\n'):
        f.write('\n')

  def read_json_file(self, pathname: str) -> JSONType:
    json_text = self.read_text_file(pathname)
    result: JSONType = json.loads(json_text)
    return result

  def write_json_file(self, pathname: str, data: JSONType):
    json_text = json.dumps(data, sort_keys=True, indent=2)
    self.write_text_file(pathname, json_text)

  def read_yaml_file(self, pathname: str) -> JSONType:
    yaml_text = self.read_text_file(pathname)
    result: JSONType = yaml.load(yaml_text, Loader=YamlLoader)
    return result

  def write_yaml_file(self, pathname: str, data: JSONType, header_notes: Optional[str]=None):
    yaml_text = yaml.dump(data, sort_keys=True, indent=2, Dumper=YamlDumper)
    if not header_notes is None:
      header_notes = header_notes.rstrip()
      if header_notes != '':
        yaml_text = header_notes + '\n' + yaml_text
    self.write_text_file(pathname, yaml_text)

  def is_equal_json_data(self, data1: JSONType, data2: JSONType) -> bool:
    json_text_1 = json.dumps(data1, sort_keys=True)
    json_text_2 = json.dumps(data2, sort_keys=True)
    result = (json_text_1 == json_text_2)

  def sync_pulumi_credentials(self):
    in_sync: bool = True
    try:
      creds = self.read_json_file(self.cfg.pulumi_credentials_file)
    except FileNotFoundError:   # TODO: json.decoder.JSONDecodeError
      creds: JSONDictType = {}
      in_sync = False

    current_backend = creds.get("current", None)
    if not isinstance(current_backend, str) or current_backend != self.cfg.backend:
      creds["current"] = self.cfg.backend
      current_backend = self.cfg.backend
      in_sync = False

    accounts = creds.get("accounts", None)
    if not isinstance(accounts, dict):
      accounts = {}
      creds["accounts"] = accounts
      in_sync = False

    backend_account = accounts.get(self.cfg.backend, None)
    if not isinstance(backend_account, dict):
      backend_account = {}
      accounts[self.cfg.backend] = backend_account
      in_sync = False

    backend_account_last_validated_at = backend_account.get("lastValidataedAt", None)
    if not isinstance(backend_account_last_validated_at, str):
      backend_account_last_validated_at = "0001-01-01T00:00:00Z"
      backend_account["lastValidatedAt"] = backend_account_last_validated_at
      in_sync = False

    access_tokens = creds.get("accessTokens", None)
    if not isinstance(access_tokens, dict):
      access_tokens = {}
      creds["accessTokens"] = access_tokens
      in_sync = False

    access_token = access_tokens.get(self.cfg.backend, None)
    if not isinstance(access_token, str) or access_token != "":
      access_token = ""
      access_tokens[self.cfg.backend] = access_token
      in_sync = False

    if not in_sync:
      self.write_json_file(self.cfg.pulumi_credentials_file, creds)

    self.creds: JSONDictType = creds

  def sync_main_cfg(self):
    in_sync: bool = True
    try:
      cfg = self.read_yaml_file(self.cfg.pulumi_main_cfg_file)
    except (FileNotFoundError, yaml.parser.ParserError):
      cfg: JSONDictType = {}
      in_sync = False

    if in_sync:
      in_sync = self.is_equal_json_data(cfg, self.cfg.main_data)

    if not in_sync:
      self.write_yaml_file(self.cfg.pulumi_main_cfg_file, self.cfg.main_data, header_notes=self.main_header_notes)

  def sync_stack_cfg(self):
    if not self.cfg.pulumi_stack_cfg_file is None:
      in_sync: bool = True
      try:
        cfg = self.read_yaml_file(self.cfg.pulumi_stack_cfg_file)
      except (FileNotFoundError, yaml.parser.ParserError):
        cfg: JSONDictType = {}
        in_sync = False

      if in_sync:
        in_sync = self.is_equal_json_data(cfg, self.cfg.stack_data)

      if not in_sync:
        self.write_yaml_file(self.cfg.pulumi_stack_cfg_file, self.cfg.stack_data, header_notes=self.stack_header_notes)

  def sync_main_script(self):
    correct_link_destination = os.path.relpath(self.cfg.pulumi_main_script_source_file, os.path.dirname(self.cfg.pulumi_main_script_target_file))
    try:

      link_destination = os.readlink(self.cfg.pulumi_main_script_target_file)
      if link_destination == correct_link_destination:
        return
      os.remove(self.cfg.pulumi_main_script_target_file)
    except FileNotFoundError:
      pass
    except OSError:
      os.remove(self.cfg.pulumi_main_script_target_file)

    os.symlink(correct_link_destination, self.cfg.pulumi_main_script_target_file)

  def sync_workspace(self):
    in_sync: bool = True
    try:
      ws = self.read_json_file(self.cfg.pulumi_workspace_file)
    except FileNotFoundError:   # TODO: json.decoder.JSONDecodeError
      ws: JSONDictType = {}
      in_sync = False

    current_stack = ws.get("stack", None)
    if not isinstance(current_stack, str) or current_stack != self.cfg.pulumi_stack:
      ws["stack"] = self.cfg.pulumi_stack
      current_stack = self.cfg.pulumi_stack
      in_sync = False

    if not in_sync:
      self.write_json_file(self.cfg.pulumi_workspace_file, ws)

    self.workspace_data: JSONDictType = ws

  def sync_config(self):
    # print(f"main cfg={json.dumps(self.cfg.main_data)}")
    # print(f"stack cfg={json.dumps(self.cfg.stack_data)}")
    if not self.is_in_sync:
      if not os.path.exists(self.cfg.pulumi_data_dir):
        Path( self.cfg.pulumi_data_dir ).mkdir( parents=True, exist_ok=True )    
      if not os.path.exists(self.cfg.pulumi_workspaces_dir):
        Path( self.cfg.pulumi_workspaces_dir ).mkdir( parents=True, exist_ok=True )    
      self.sync_pulumi_credentials()
      self.sync_main_cfg()
      self.sync_stack_cfg()
      self.sync_main_script()
      self.sync_workspace()
      self.is_in_sync = True

  def get_or_create_pulumi_backend(self) -> str:
    if not self.backend_created:
      bucket = self.cfg.backend_bucket
      created = self.global_s3_create_bucket_if_missing(bucket)
      if created:
        dp(f"Created S3 bucket {bucket} for pulumi backend")
      self.backend_created = True
    return self.cfg.backend

  def _fix_raw_popen_args(self, arglist: List[str], kwargs: Dict[str, Any]) -> List[str]:
    arglist = [ self.cfg.pulumi_prog ] + arglist
    osenviron = dict(self.cfg.osenviron)
    env = kwargs.pop('env', None)
    if not env is None:
      osenviron.update(env)
    kwargs['env'] = osenviron
    kwargs['cwd'] = self.cfg.pulumi_data_dir
    return arglist


  def raw_pulumi_Popen(self, arglist: List[str], **kwargs) -> subprocess.Popen:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.Popen(arglist, **kwargs)

  def raw_pulumi_check_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    return subprocess.check_call(arglist, **kwargs)

  def raw_pulumi_call(self, arglist: List[str], **kwargs) -> int:
    arglist = self._fix_raw_popen_args(arglist, kwargs)
    #print(f"subprocess.call(args={arglist}, kwargs={json.dumps(kwargs, sort_keys=True, indent=2)})", file=sys.stderr)
    return subprocess.call(arglist, **kwargs)

  def parse_args(self, arglist: Optional[List[str]]=None) -> argparse.Namespace:
    pctx = self

    class MyParser(argparse.ArgumentParser):
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
    print(f"Environment variables: {json.dumps(self.cfg.osenviron, sort_keys=True, indent=2)}")
    return self.raw_pulumi_call(global_options.global_option_arglist + [ 'about' ] + args)

  def pulumi_call(self, arglist: Optional[List[str]]=None, **kwargs) -> int:

    args = self.parse_args(arglist)

    backend = self.get_or_create_pulumi_backend()
    if self.verbose_level > 1:
      print(f"Pulumi backend established at {backend}", file=sys.stderr)
    self.sync_config()
    if self.verbose_level > 1:
      print(f"Pulumi config synchronized", file=sys.stderr)

    if len(args.subcommand) > 0:
      cmd = args.subcommand[0]
      remargs = args.subcommand[1:]
      if cmd == 'about':
        return self.cmd_about(remargs, args)

    arglist = args.global_option_arglist + args.subcommand


    exit_code = self.raw_pulumi_call(arglist)
    return exit_code

if __name__ == '__main__':
  pw = PulumiCommandWrapper()
  exit_code = pw.pulumi_call()
  if exit_code != 0:
    sys.exit(exit_code)
