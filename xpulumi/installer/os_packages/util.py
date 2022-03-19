#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Utilities to assist with installation of OS packages"""

from typing import TYPE_CHECKING, Optional, List, Union, TextIO, cast, Callable, Any, Set, Tuple

import os
import sys
from packaging import version
import tempfile
import platform
import grp
import hashlib
import filecmp
import urllib3
import shutil
import shlex
import subprocess
from functools import lru_cache

import threading
from collections import defaultdict
from functools import lru_cache, _make_key

from xpulumi.exceptions import XPulumiError


if TYPE_CHECKING:
  from subprocess import _CMD, _FILE, _ENV
  from _typeshed import StrOrBytesPath
else:
  _CMD = Any
  _FILE = Any
  _ENV = Any
  StrOrBytesPath = Any

class _RunOnceState:
  has_run: bool = False
  result: Any = None
  lock: threading.Lock

  def __init__(self):
    self.lock = threading.Lock()

def run_once(func):
  state = _RunOnceState()

  def _run_once(*args, **kwargs) -> Any:
    if not state.has_run:
      with state.lock:
        if not state.has_run:
          state.result = func(*args, **kwargs)
          state.has_run = True
    return state.result
  return _run_once

@run_once
def get_tmp_dir() -> str:
  """Returns a temporary directory that is private to this user

  Returns:
      str: A temporary directory that is private to this user
  """
  parent_dir: Optional[str] = os.environ.get("XDG_RUNTIME_DIR")
  if parent_dir is None:
    parent_dir = tempfile.gettempdir()
    tmp_dir = os.path.join(parent_dir, f"user-{os.getuid()}")
  else:
    tmp_dir = os.path.join(parent_dir, 'tmp')
  if not os.path.exists(tmp_dir):
    os.mkdir(tmp_dir, mode=0o700)
  return tmp_dir

def check_version_ge(version1: str, version2: str) -> bool:
  """returns True iff version1 is greater than or equal to version2

  Args:
      version1 (str): A standard version string
      version2 (str): A standard version string

  Returns:
      bool: True iff version1 is greater than or equal to version2
  """
  return version.parse(version1) >= version.parse(version2)

def searchpath_split(searchpath: str) -> List[str]:
  result = [ x for x in searchpath.split(os.pathsep) if x != '' ]
  return result

def searchpath_join(dirnames: List[str]) -> str:
  return os.pathsep.join(dirnames)

def searchpath_normalize(searchpath: str) -> str:
  """Removes leading, trailing, and duplicate searchpath seperators from
  a search path string.

  Args:
      searchpath (str): A search path string similar to $PATH

  Returns:
      str: The search path string with extraneous seperators removed
  """
  return searchpath_join(searchpath_split(searchpath))

def searchpath_parts_contains_dir(parts: List[str], dirname: str) -> bool:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  return dirname in parts

def searchpath_contains_dir(searchpath: str, dirname: str) -> bool:
  return searchpath_parts_contains_dir(searchpath_split(searchpath), dirname)

def searchpath_parts_remove_dir(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [ x for x in parts if x != dirname ]
  return result

def searchpath_remove_dir(searchpath: str, dirname: str) -> str:
  return searchpath_join(searchpath_parts_remove_dir(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = [dirname] + searchpath_parts_remove_dir(parts, dirname)
  return result

def searchpath_prepend(searchpath: str, dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend(searchpath_split(searchpath), dirname))

def searchpath_parts_prepend_if_missing(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = [dirname] + parts
  return result

def searchpath_prepend_if_missing(searchpath: str, dirname: str) -> str:
  return searchpath_join(searchpath_parts_prepend_if_missing(searchpath_split(searchpath), dirname))

def searchpath_parts_force_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  result = searchpath_parts_remove_dir(parts, dirname) + [dirname]
  return result

def searchpath_force_append(searchpath: str, dirname: str) -> str:
  return searchpath_join(searchpath_parts_force_append(searchpath_split(searchpath), dirname))

def searchpath_parts_append(parts: List[str], dirname: str) -> List[str]:
  dirname = os.path.abspath(os.path.normpath(os.path.expanduser(dirname)))
  if dirname in parts:
    result = parts[:]
  else:
    result = parts + [dirname]
  return result

def searchpath_append(searchpath: str, dirname: str) -> str:
  return searchpath_join(searchpath_parts_append(searchpath_split(searchpath), dirname))

def get_current_architecture() -> str:
  return platform.machine()

def get_gid_of_group(group: str) -> int:
  gi = grp.getgrnam(group)
  return gi.gr_gid

def get_file_hash_hex(filename: str) -> str:
  h = hashlib.sha256()
  with open(filename, 'rb') as f:
    while True:
      data = f.read(1024*128)
      if len(data) == 0:
        break
      h.update(data)
  return h.hexdigest()

def files_are_identical(filename1: str, filename2: str, quick: bool=False) -> bool:
  return filecmp.cmp(filename1, filename2, shallow=quick)

_os_package_metadata_stale: bool = True
def invalidate_os_package_list() -> None:
  global _os_package_metadata_stale
  _os_package_metadata_stale = True

def download_url_file(
      url: str,
      filename: str,
      pool_manager: Optional[urllib3.PoolManager]=None,
      filter_cmd: Optional[Union[str, List[str]]]=None
    ) -> None:
  if pool_manager is None:
    pool_manager = urllib3.PoolManager()
  
  if not filter_cmd is None and not isinstance(filter_cmd, list):
    filter_cmd = cast(List[str], [ filter_cmd ])
  if filter_cmd is None or len(filter_cmd) == 0 or (len(filter_cmd) == 1 and filter_cmd[0] == 'cat'):
    with open(filename, 'wb') as f:
      resp = pool_manager.request('GET', url, preload_content=False)
      shutil.copyfileobj(resp, f)
  else:
    with tempfile.NamedTemporaryFile(dir=get_tmp_dir()) as f3:
      resp = pool_manager.request('GET', url, preload_content=False)
      shutil.copyfileobj(resp, f3)
      f3.flush()
      # TODO: following won't work on windows; see https://code.djangoproject.com/wiki/NamedTemporaryFile
      with open(f3.name, 'rb') as f1:
        with open(filename, 'wb') as f2:
          subprocess.check_call(filter_cmd, stdin=f1, stdout=f2)

def running_as_root() -> bool:
  return os.geteuid() == 0

@run_once
def sudo_warn(
      args: _CMD,
      stderr: Optional[_FILE] = None,
      sudo_reason: Optional[str] = None,
    ):
  errout = stderr if isinstance(stderr, TextIO) else sys.stderr
  if sudo_reason is None:
    sudo_reason = f"command: {args!r}"
  print(f"Sudo required: {sudo_reason}", file=errout)

def _sudo_fix_args(
      args: _CMD,
      stderr: Optional[_FILE] = None,
      shell: bool = False,
      use_sudo: bool = True,
      run_with_group: Optional[str] = None,
      sudo_reason: Optional[str] = None,
    ) -> _CMD:
  if shell:
    if isinstance(args, list):
      raise RuntimeError(f"Arglist not allowed with shell=True: {args}")
    args = cast(_CMD, [ 'bash', '-c', args ])

  if not isinstance(args, list):
    args = cast(_CMD, [ args ])

  need_group = not run_with_group is None and should_run_with_group(run_with_group)
  is_root = running_as_root()

  if need_group or (use_sudo and not is_root):
    sudo_warn(args, stderr=stderr, sudo_reason=sudo_reason)

    new_args = [ 'sudo' ]
    if need_group:
      new_args.extend( [ '-E', '-u', get_current_os_user()  ] )
    new_args.extend(cast(List[str], args))
    args = new_args
  return args

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

  result = subprocess.Popen(
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

class CalledProcessErrorWithStderrMessage(subprocess.CalledProcessError):
    def __str__(self):
      return super().__str__() + f": [{self.stderr}]" 

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
    raise CalledProcessErrorWithStderrMessage(exit_code, args, stderr = stderr_s)
  return stdout_bytes

def unix_mv(source: str, dest: str, use_sudo: bool=False, sudo_reason: Optional[str]=None) -> None:
  """
  Equivalent to the linux "mv" commandline.  Atomic within same volume, and overwrites the destination.
  Works for directories.

  Args:
      source (str): Source file or directory.
      dest (str): Destination file or directory. Will be overwritten if it exists.
      sudo (bool): If True, the move will be done as sudo
      sudo_reason (str, optional): Reason why sudo is needed

  Raises:
      RuntimeError: Any error from the mv command
  """
  source = os.path.expanduser(source)
  dest = os.path.expanduser(dest)
  sudo_check_output_stderr_exception(['mv', source, dest], use_sudo=use_sudo, sudo_reason=sudo_reason)

def chown_root(filename: str, sudo_reason: Optional[str]=None):
  sudo_check_output_stderr_exception(['chown', 'root.root', filename], sudo_reason=sudo_reason)

def update_gpg_keyring(
      url: str,
      dest_file: str,
      filter_cmd: Optional[Union[str, List[str]]]=None,
      stderr: Optional[TextIO]=None,
    ) -> None:
  if stderr is None:
    stderr = sys.stderr
  tmp_file_gpg = os.path.join(get_tmp_dir(), "tmp_gpg_keyring.gpg")
  download_url_file(url, tmp_file_gpg, filter_cmd=filter_cmd)
  if os.path.exists(dest_file) and files_are_identical(dest_file, tmp_file_gpg):
    return
  print(f"Updating GPG keyring at {dest_file} (sudo required)", file=stderr)
  os.chmod(tmp_file_gpg, 0o644)
  chown_root(tmp_file_gpg, sudo_reason=f"Installing GPG keyring to {dest_file}")
  unix_mv(tmp_file_gpg, dest_file, use_sudo=True, sudo_reason=f"Installing GPG keyring to {dest_file}")

def install_gpg_keyring_if_missing(
      url: str,
      dest_file: str,
      filter_cmd: Optional[Union[str, List[str]]]=None,
      stderr: Optional[TextIO]=None,
    ) -> None:
  if not os.path.exists(dest_file):
    update_gpg_keyring(url, dest_file, filter_cmd=filter_cmd, stderr=stderr)

@run_once
def get_linux_distro_name() -> str:
  result = subprocess.check_output(['lsb_release', '-cs'])
  linux_distro = result.decode('utf-8').rstrip()
  return linux_distro

@run_once
def get_dpkg_arch() -> str:
  result = subprocess.check_output(['dpkg', '--print-architecture'])
  dpkg_arch = result.decode('utf-8').rstrip()
  return dpkg_arch

def file_contents(filename: str) -> str:
  with open(filename) as f:
    result = f.read()
  return result

def update_os_package_list(force: bool=False, stderr: Optional[TextIO]=None) -> None:
  global _os_package_metadata_stale
  if force:
    _os_package_metadata_stale = True

  if _os_package_metadata_stale:
    sudo_check_call(['apt-get', 'update'], sudo_reason="Updating available apt-get package metadata", stderr=stderr)
    _os_package_metadata_stale = False

def update_apt_sources_list(dest_file: str, signed_by: str, url: str, *args, stderr: Optional[TextIO]=None) -> None:
  arch = get_dpkg_arch()
  tmp_file = os.path.join(get_tmp_dir(), "tmp_apt_source.list")
  with open(tmp_file, "w") as f:
    print(f"deb [arch={arch} signed-by={signed_by}] {url} {' '.join(args)}", file=f)
  if os.path.exists(dest_file):
    if files_are_identical(tmp_file, dest_file):
      return
    sudo_reason= f"Updating apt-get sources list for {dest_file}; old=<{file_contents(dest_file).rstrip()}>"
  else:
    sudo_reason= f"Creating apt-get sources list for {dest_file}"
  sudo_reason += f", new=<{file_contents(tmp_file).rstrip()}>"
  os.chmod(tmp_file, 0o644)
  chown_root(tmp_file, sudo_reason=sudo_reason)
  invalidate_os_package_list()
  unix_mv(tmp_file, dest_file, use_sudo=True, sudo_reason=sudo_reason)
  update_os_package_list(stderr=stderr)

def install_apt_sources_list_if_missing(dest_file: str, signed_by: str, url: str, *args, stderr: Optional[TextIO]=None) -> None:
  if not os.path.exists(dest_file):
    update_apt_sources_list(dest_file, signed_by, url, *args, stderr=stderr)

def get_os_package_version(package_name: str) -> str:
  stdout_bytes = cast(bytes, sudo_check_output_stderr_exception(['dpkg-query', '-W', '-f=${Version}\\n']))
  return stdout_bytes.decode('utf-8').rstrip()

def os_package_is_installed(package_name: str) -> bool:
  result: bool = False
  try:
    get_os_package_version(package_name)
    result = True
  except subprocess.CalledProcessError:
    pass
  return result

def uninstall_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'remove'] + filtered, stderr=stderr, sudo_reason=f"Removing packages {filtered}")

def install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")


def update_and_install_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  filtered = [ x for x in package_names if not os_package_is_installed(x) ]

  if len(filtered) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'install', '-y'] + filtered, stderr=stderr, sudo_reason=f"Installing packages {filtered}")

def upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")


def update_and_upgrade_os_packages(package_names: Union[str, List[str]], stderr: Optional[TextIO] = None) -> None:
  if not isinstance(package_names, list):
    package_names = [ package_names ]

  if len(package_names) > 0:
    update_os_package_list()
    sudo_check_call(['apt-get', 'upgrade', '-y'] + package_names, stderr=stderr, sudo_reason=f"Upgrading packages {package_names}")

def find_command_in_path(cmd: str) -> Optional[str]:
  result: Optional[str] = None
  try:
    result = subprocess.check_output(f"command -v {shlex.quote(cmd)}", shell=True).decode('utf-8').rstrip('\n\r')
  except subprocess.CalledProcessError:
    pass
  return result

def command_exists(cmd: str) -> bool:
  return not find_command_in_path(cmd) is None

class PackageList:
  _package_names: List[str]
  _package_name_set: Set[str]

  def __init__(self, package_names: Optional[List[str]]=None):
    self._package_names = []
    self._package_name_set = set()
    self.add_packages(package_names)

  def add_packages(self, package_names: Optional[Union[str, List[str]]]) -> None:
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set:
          self._package_names.append(package_name)
          self._package_name_set.add(package_name)

  def add_packages_if_missing(self, package_names: Optional[Union[str, List[str]]]) -> None:
    if not package_names is None:
      if not isinstance(package_names, list):
        package_names = [ package_names ]
      for package_name in package_names:
        if not package_name in self._package_name_set and not os_package_is_installed(package_name):
          self.add_packages(package_name)

  def add_package_if_cmd_missing(self, cmd: str, package_name: Optional[str]=None) -> None:
    if package_name is None:
      package_name = cmd
    if not package_name in self._package_name_set and not command_exists(cmd):
      self.add_packages(package_name)

  def add_package_if_outdated(self, package_name: str, min_version: str) -> None:
    if not package_name in self._package_name_set:
      package_version: Optional[str] = None
      try:
        package_version = get_os_package_version(package_name)
      except subprocess.CalledProcessError:
        pass
      if package_version is None or not check_version_ge(package_version, min_version):
        self.add_packages(package_name)

  def install_all(self, stderr:Optional[TextIO]=None):
    if len(self._package_names) > 0:
      install_os_packages(self._package_names, stderr=stderr)

  def upgrade_all(self, stderr:Optional[TextIO]=None):
    if len(self._package_names) > 0:
      upgrade_os_packages(self._package_names, stderr=stderr)

def create_os_group(group_name: str, stderr: Optional[TextIO]=None) -> int:
  gid: Optional[int] = None
  try:
    groupinfo = grp.getgrnam(group_name)
    gid = groupinfo.gr_gid
  except KeyError:
    pass

  if gid is None:
    sudo_check_output_stderr_exception(['groupadd', group_name], stderr=stderr, sudo_reason=f"Adding OS group '{group_name}'")
    groupinfo = grp.getgrnam(group_name)
    gid = groupinfo.gr_gid
  return gid

def get_current_os_user() -> str:
  return os.getlogin()

def get_all_os_groups() -> List[str]:
  return sorted(x.gr_name for x in grp.getgrall())

def os_group_exists(group_name: str) -> bool:
  gid: Optional[int] = None
  try:
    groupinfo = grp.getgrnam(group_name)
    gid = groupinfo.gr_gid
  except KeyError:
    pass
  return not gid is None

def get_os_groups_of_user(user: Optional[str]=None) -> List[str]:
  if user is None:
    user = get_current_os_user()
  result: List[str] = []
  for group in grp.getgrall():
    if user in group.gr_mem:
      result.append(group.gr_name)
  return sorted(result)

def get_os_groups_of_current_process() -> List[str]:
  gids = os.getgroups()
  result: List[str] = []
  for group in grp.getgrall():
    if group.gr_gid in gids:
      result.append(group.gr_name)
  return sorted(result)

def os_group_includes_user(group_name: str, user: Optional[str]=None) -> bool:
  groups = get_os_groups_of_user(user=user)
  return group_name in groups

def os_group_includes_current_process(group_name: str) -> bool:
  groups = get_os_groups_of_current_process()
  return group_name in groups

def os_groupadd_user(group_name: str, user: Optional[str]=None, stderr: Optional[TextIO]=None):
  if user is None:
    user = get_current_os_user()
  if not os_group_includes_user(user):
    sudo_check_output_stderr_exception(['usermod', '-a', '-G', group_name, user], stderr=stderr, sudo_reason=f"Adding user '{user}' to OS group '{group_name}'")

def should_run_with_group(group_name: str, require: bool=True) -> bool:
  if require:
    if not os_group_includes_user(group_name):
      if os_group_exists(group_name):
        raise XPulumiError(f"User \"{get_current_os_user()}\" is not a member of OS group \"{group_name}\"")
      else:
        raise XPulumiError(f"OS group \"{group_name}\" does not exist")
    result = not os_group_includes_current_process(group_name)
  else:
    result = not os_group_includes_current_process(group_name) and os_group_includes_user(group_name)
  return result
