#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Functions to wait for S3 objects"""

from typing import Optional, Tuple, List

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

def call_aws_sam(subcmd: List[str])

def sudo_Popen(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
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
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> subprocess.Popen:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.Popen(  # pylint: disable=consider-using-with
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
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

def sudo_call(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )


  result = subprocess.call(
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
    )
  return result

def sudo_check_call(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
      cwd: Optional[StrOrBytesPath] = None,
      env: Optional[_ENV] = None,
      universal_newlines: Optional[bool] = None,
      startupinfo: Any = None,
      creationflags: int = 0,
      restore_signals: bool = True,
      start_new_session: bool = False,
      pass_fds: Any = (),
      *,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.check_call(
      args,
      bufsize=bufsize,
      executable=cast(StrOrBytesPath, executable),
      stdin=stdin,
      stdout=stdout,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
      universal_newlines=cast(bool, universal_newlines),
      startupinfo=startupinfo,
      creationflags=creationflags,
      restore_signals=restore_signals,
      start_new_session=start_new_session,
      pass_fds=pass_fds,
    )
  return result

def sudo_check_output(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
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
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> Union[str, bytes]:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  result = subprocess.check_output(   # type: ignore [misc]
      args,
      bufsize=bufsize,
      executable=executable,
      stdin=stdin,
      stderr=stderr,
      preexec_fn=preexec_fn,
      close_fds=close_fds,
      cwd=cwd,
      env=env,
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
def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = ...,
      executable: Optional[StrOrBytesPath] = ...,
      stdin: Optional[_FILE] = ...,
      stderr: Optional[_FILE] = ...,
      preexec_fn: Optional[Callable[[], Any]] = ...,
      close_fds: bool = ...,
      shell: bool = ...,
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
      use_sudo: bool = ...,
      run_with_group: Optional[str] = ...,
      sudo_reason: Optional[str] = ...,
      text: Literal[True],
    ) -> str:
  ...

@overload
def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = ...,
      executable: Optional[StrOrBytesPath] = ...,
      stdin: Optional[_FILE] = ...,
      stderr: Optional[_FILE] = ...,
      preexec_fn: Optional[Callable[[], Any]] = ...,
      close_fds: bool = ...,
      shell: bool = ...,
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
      use_sudo: bool = ...,
      run_with_group: Optional[str] = ...,
      sudo_reason: Optional[str] = ...,
      text: Literal[False, None] = None,
    ) -> bytes:
  ...

def sudo_check_output_stderr_exception(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
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
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
      text: Optional[bool] = None,
    ) -> Union[str, bytes]:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  with subprocess.Popen(             # type: ignore [misc]
        args,
        bufsize=bufsize,
        executable=executable,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=env,
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
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr=stderr_s, output=stdout_bytes)
  return stdout_bytes

def sudo_check_call_stderr_exception(
      args: _CMD,
      bufsize: int = -1,
      executable: Optional[StrOrBytesPath] = None,
      stdin: Optional[_FILE] = None,
      stdout: Optional[_FILE] = None,
      stderr: Optional[_FILE] = None,
      preexec_fn: Optional[Callable[[], Any]] = None,
      close_fds: bool = True,
      shell: bool = False,
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
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> int:
  args = _sudo_fix_args(
      args,
      stderr=stderr,
      shell=shell,
      use_sudo=use_sudo,
      run_with_group=run_with_group,
      sudo_reason=sudo_reason,
    )

  with subprocess.Popen(             # type: ignore [misc]
        args,
        bufsize=bufsize,
        executable=executable,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        preexec_fn=preexec_fn,
        close_fds=close_fds,
        cwd=cwd,
        env=env,
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
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr = stderr_s)
  return exit_code
