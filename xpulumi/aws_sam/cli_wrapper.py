#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper class or AWS SAM CLI"""

import os
import boto3.session
import botocore.client
import botocore.errorfactory
import time
import asyncio
import concurrent.futures
import subprocess
from project_init_tools.installer.aws_sam_cli import (
    install_aws_sam_cli,
    aws_sam_cli_is_installed,
    get_aws_sam_cli_version,
    get_aws_sam_cli_prog,
  )

from ..exceptions import XPulumiError

from typing import (
    TYPE_CHECKING,
    Optional,
    List,
    Union,
    TextIO,
    cast,
    Callable,
    Any,
    Set,
    Tuple,
    Generator,
    Mapping,
    overload,
    Literal,
    Dict,
    MutableMapping,
    Type
  )

from project_init_tools import CalledProcessErrorWithStderrMessage
from ..internal_types import Jsonable, JsonableDict

import json
import hashlib
import string
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
import pathlib
import threading
import tempfile
import secrets
import boto3
from boto3.session import Session as BotoAwsSession
from botocore.session import Session as BotocoreSession
import sys
from packaging import version
import platform
import grp
import filecmp
import urllib3
import shutil
import shlex
from collections import defaultdict
from functools import lru_cache, _make_key

import yaml

try:
  from yaml import CLoader as YamlLoader, CDumper as YamlDumper
except ImportError:
  from yaml import Loader as YamlLoader, Dumper as YamlDumper  #type: ignore[misc]

# mypy really struggles with this
if TYPE_CHECKING:
  from subprocess import _CMD, _FILE, _ENV
  from _typeshed import StrOrBytesPath
else:
  _CMD = Any
  _FILE = Any
  _ENV = Any
  StrOrBytesPath = Any


class AwsSamCli:
  aws_sam_cli_prog: str
  base_env: Dict[str, str]
  debug: bool

  def __init__(
        self,
        aws_sam_cli_prog: Optional[str]=None,
        base_env: Optional[Mapping[str, str]]=None,
        debug: bool=True,
      ):
    if aws_sam_cli_prog is None:
      aws_sam_cli_prog = get_aws_sam_cli_prog()
    self.aws_sam_cli_prog = os.path.abspath(aws_sam_cli_prog)
    if base_env is None:
      base_env = os.environ
    self.base_env = dict(base_env)
    self.debug = debug

  def _fix_args(
        self,
        args: List[str],
        env: Optional[_ENV]=None,
        debug: Optional[bool]=None,
      ) -> Tuple[List[str], Dict[str, str]]:
    has_debug = False
    args = list(args)
    i = 0
    while i < len(args):
      arg = args[i]
      if arg == '--debug':
        if debug is None or debug:
          has_debug = True
          break
        del args[i]
      else:
        i += 1
    if debug is None:
      debug = self.debug
    if debug and not has_debug:
      args.insert(0, '--debug')
    args.insert(0, self.aws_sam_cli_prog)
    result_env = dict(self.base_env)
    if not env is None:
      result_env.update(env)

    return args, result_env

  def Popen(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        stdout: Optional[_FILE] = None,
        stderr: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        *,
        text: Optional[bool] = None,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        debug: Optional[bool] = None,
      ) -> subprocess.Popen:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    result = subprocess.Popen(  # pylint: disable=consider-using-with
        fixed_args,
        bufsize=bufsize,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=fixed_env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
        text=cast(bool, text),
        encoding=encoding,
        errors=errors,
      )
    return result

  def call(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        stdout: Optional[_FILE] = None,
        stderr: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        debug: Optional[bool] = None,
      ) -> int:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    result = subprocess.call(
        fixed_args,
        bufsize=bufsize,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=fixed_env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
      )
    return result

  def check_call(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        stdout: Optional[_FILE] = None,
        stderr: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        debug: Optional[bool] = None,
      ) -> int:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    result = subprocess.check_call(
        fixed_args,
        bufsize=bufsize,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=fixed_env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
      )
    return result

  @overload
  def check_output(
        self,
        args: List[str],
        bufsize: int = ...,
        stdin: Optional[_FILE] = ...,
        stderr: Optional[_FILE] = ...,
        preexec_fn: Optional[Callable[[], Any]] = ...,
        close_fds: bool = ...,
        cwd: Optional[StrOrBytesPath] = ...,
        env: Optional[_ENV] = ...,
        universal_newlines: Optional[bool] = ...,
        startupinfo: Any = ...,
        creationflags: int = ...,
        restore_signals: bool = ...,
        start_new_session: bool = ...,
        pass_fds: Any = ...,
        *,
        encoding: Optional[str] = ...,
        errors: Optional[str] = ...,
        text: Literal[True],
        debug: Optional[bool] = None,
      ) -> str:
    ...

  @overload
  def check_output(
        self,
        args: List[str],
        bufsize: int = ...,
        stdin: Optional[_FILE] = ...,
        stderr: Optional[_FILE] = ...,
        preexec_fn: Optional[Callable[[], Any]] = ...,
        close_fds: bool = ...,
        cwd: Optional[StrOrBytesPath] = ...,
        env: Optional[_ENV] = ...,
        universal_newlines: Optional[bool] = ...,
        startupinfo: Any = ...,
        creationflags: int = ...,
        restore_signals: bool = ...,
        start_new_session: bool = ...,
        pass_fds: Any = ...,
        *,
        encoding: Optional[str] = ...,
        errors: Optional[str] = ...,
        text: Literal[False, None] = None,
        debug: Optional[bool] = None,
      ) -> bytes:
    ...

  def check_output(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        stderr: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        *,
        text: Optional[bool] = None,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        debug: Optional[bool] = None,
      ) -> Union[str, bytes]:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    result = subprocess.check_output(   # type: ignore [misc]
        fixed_args,
        bufsize=bufsize,
        stdin=stdin,
        stderr=stderr,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=fixed_env,
        universal_newlines=cast(bool, universal_newlines),
        startupinfo=startupinfo,
        creationflags=creationflags,
        restore_signals=restore_signals,
        start_new_session=start_new_session,
        pass_fds=pass_fds,
        text=text,
        encoding=cast(str, encoding),
        errors=errors,
      )
    return result

  @overload
  def check_output_stderr_exception(
        self,
        args: List[str],
        bufsize: int = ...,
        stdin: Optional[_FILE] = ...,
        preexec_fn: Optional[Callable[[], Any]] = ...,
        close_fds: bool = ...,
        cwd: Optional[StrOrBytesPath] = ...,
        env: Optional[_ENV] = ...,
        universal_newlines: Optional[bool] = ...,
        startupinfo: Any = ...,
        creationflags: int = ...,
        restore_signals: bool = ...,
        start_new_session: bool = ...,
        pass_fds: Any = ...,
        *,
        encoding: Optional[str] = ...,
        errors: Optional[str] = ...,
        text: Literal[True],
        debug: Optional[bool] = None,
      ) -> str:
    ...

  @overload
  def check_output_stderr_exception(
        self,
        args: List[str],
        bufsize: int = ...,
        stdin: Optional[_FILE] = ...,
        preexec_fn: Optional[Callable[[], Any]] = ...,
        close_fds: bool = ...,
        cwd: Optional[StrOrBytesPath] = ...,
        env: Optional[_ENV] = ...,
        universal_newlines: Optional[bool] = ...,
        startupinfo: Any = ...,
        creationflags: int = ...,
        restore_signals: bool = ...,
        start_new_session: bool = ...,
        pass_fds: Any = ...,
        *,
        encoding: Optional[str] = ...,
        errors: Optional[str] = ...,
        text: Literal[False, None] = None,
        debug: Optional[bool] = None,
      ) -> bytes:
    ...

  def check_output_stderr_exception(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        *,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        text: Optional[bool] = None,
        debug: Optional[bool] = None,
      ) -> Union[str, bytes]:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    with subprocess.Popen(             # type: ignore [misc]
          fixed_args,
          bufsize=bufsize,
          stdin=stdin,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          preexec_fn=preexec_fn,
          close_fds=close_fds,
          cwd=cwd,
          env=fixed_env,
          universal_newlines=cast(bool, universal_newlines),
          startupinfo=startupinfo,
          creationflags=creationflags,
          restore_signals=restore_signals,
          start_new_session=start_new_session,
          pass_fds=pass_fds,
          text=text,
          encoding=cast(str, encoding),
          errors=errors,
        ) as proc:
      (stdout_bytes, stderr_bytes) = cast(Tuple[Union[str, bytes], Union[str, bytes]], proc.communicate())
      exit_code = proc.returncode
    if exit_code != 0:
      if encoding is None:
        encoding = 'utf-8'
      stderr_s = stderr_bytes if isinstance(stderr_bytes, str) else stderr_bytes.decode(encoding)
      stderr_s = stderr_s.rstrip()
      raise CalledProcessErrorWithStderrMessage(exit_code, fixed_args, stderr=stderr_s, output=stdout_bytes)
    return stdout_bytes

  def check_call_stderr_exception(
        self,
        args: List[str],
        bufsize: int = -1,
        stdin: Optional[_FILE] = None,
        stdout: Optional[_FILE] = None,
        preexec_fn: Optional[Callable[[], Any]] = None,
        close_fds: bool = True,
        cwd: Optional[StrOrBytesPath] = None,
        env: Optional[_ENV] = None,
        universal_newlines: Optional[bool] = None,
        startupinfo: Any = None,
        creationflags: int = 0,
        restore_signals: bool = True,
        start_new_session: bool = False,
        pass_fds: Any = (),
        *,
        text: Optional[bool] = None,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        debug: Optional[bool] = None,
      ) -> int:
    fixed_args, fixed_env = self._fix_args(args, env, debug=debug)

    with subprocess.Popen(             # type: ignore [misc]
          fixed_args,
          bufsize=bufsize,
          stdin=stdin,
          stdout=stdout,
          stderr=subprocess.PIPE,
          preexec_fn=preexec_fn,
          close_fds=close_fds,
          cwd=cwd,
          env=fixed_env,
          universal_newlines=cast(bool, universal_newlines),
          startupinfo=startupinfo,
          creationflags=creationflags,
          restore_signals=restore_signals,
          start_new_session=start_new_session,
          pass_fds=pass_fds,
          text=text,
          encoding=cast(str, encoding),
          errors=errors,
        ) as proc:
      (_, stderr_bytes) = cast(Tuple[Union[str, bytes], Union[str, bytes]], proc.communicate())
      exit_code = proc.returncode
    if exit_code != 0:
      if encoding is None:
        encoding = 'utf-8'
      stderr_s = stderr_bytes if isinstance(stderr_bytes, str) else stderr_bytes.decode(encoding)
      stderr_s = stderr_s.rstrip()
      raise CalledProcessErrorWithStderrMessage(exit_code, fixed_args, stderr = stderr_s)
    return exit_code
