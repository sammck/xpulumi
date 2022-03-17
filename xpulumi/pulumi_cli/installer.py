#!/usr/bin/env python3

from typing import TextIO, Tuple, Optional, Dict, Iterator, Sequence
from pathlib import Path

import subprocess
import sys
import os
import argparse
import re
import urllib.request
from urllib.parse import urlparse
import http.client
import platform
import tempfile
import shutil
import shlex
from enum import Enum
from contextlib import contextmanager

verbose: bool = False

home_dir = os.path.expanduser("~")
default_pulumi_dir = os.path.join(home_dir, '.pulumi')
default_pulumi_bin_dir = os.path.join(default_pulumi_dir, 'bin')
default_pulumi_cmd = os.path.join(default_pulumi_bin_dir, 'pulumi')
pulumi_latest_version_url = "https://www.pulumi.com/latest-version"
pulumi_tarball_base_url="https://get.pulumi.com/releases/sdk/pulumi"

_cached_pulumi_latest_version: Optional[str] = None
def get_pulumi_latest_version() -> str:
  """
  Returns the latest version of Pulumi CLI available for download
  """
  global _cached_pulumi_latest_version
  if _cached_pulumi_latest_version is None:
    resp: http.client.HTTPResponse = urllib.request.urlopen(pulumi_latest_version_url)
    contents: bytes = resp.read()
    _cached_pulumi_latest_version = contents.decode('utf-8').strip()
  return _cached_pulumi_latest_version

def get_pulumi_tarball_url(version: Optional[str]=None):
  """
  Gets the full URL to the tarball for the specified version of Pulumi CLI,
  or the latest version.

  Args:
      version (Optional[str], optional): The desired version of Pulumi CLI,
      or None for the latest version. Defaults to None.
  """

  if version is None:
    version = get_pulumi_latest_version()

  platform_system = platform.system()    # Linux or Darwin
  if not platform_system in [ 'Linux', 'Darwin' ]:
    raise RuntimeError(f"OS platform \"{platform_system}\" is not supported")
  pulumi_os = platform_system.lower()
  platform_machine = platform.machine()  # aarch64 or arm64 for arm, x86_64 for intel/amd
  pulumi_arch: str
  if platform_machine  in [ 'aarch64', 'arm64' ]:
    pulumi_arch = 'arm64'
  elif platform_machine == 'x86_64':
    pulumi_arch = 'x64'
  else:
    raise RuntimeError(f"CPU architecture \"{platform_machine}\" is not supported")
  
  result = f"{pulumi_tarball_base_url}-v{version}-{pulumi_os}-{pulumi_arch}.tar.gz"
  return result

def download_file(url: str, dirname: str='.', filename: Optional[str]=None) -> str:
  """
  Downloads a file from http/https.

  Args:
      url:                                The URL that will provide the contents of the file
      dirname (str, optional):            The Directory to which filename is relative.  Defaults to '.'.
      filename (Optional[str], optional): The pathname in which to place the tarball, or None to use the last element of the url as a filename.
                                          Evaluated relative to dirname. Defaults to None.
  Returns:
      str: The path where the tarball was placed 
  """
  dirname = os.path.expanduser(dirname)
  if filename is None:
    url_path = urlparse(url).path
    filename = os.path.basename(url_path)
  filename = os.path.abspath(os.path.join(dirname, os.path.basename(filename)))

  pathname, resp = urllib.request.urlretrieve(url, filename=filename)
  return pathname


def download_pulumi_tarball(
      version: Optional[str]=None,
      dirname: str='.',
      filename: Optional[str]=None,
    ) -> Tuple[str, str]:
  """
  Downloads a tarball for a specific version of Pulumi CLI, or the latest version.

  Args:
      version (Optional[str], optional): The desired version, or None for the latest version. Defaults to None.
      dirname (str, optional): The directory in which to place the tarball. Defaults to '.'.
      filename (Optional[str], optional): The pathname in which to place the tarball, or None to use the last element of the url as a filename.
                                          Evaluated relative to dirname. Defaults to None.

  Returns:
      Tuple[str, str]: A tuple with:
                          [0] The path where the tarball was placed 
                          [1] The URL from which the tarball was fetched
  """
  url = get_pulumi_tarball_url(version=version)
  pathname = download_file(url, dirname=dirname, filename=filename)
  return pathname, url

def unix_mv(source: str, dest: str):
  """
  Equivalent to the linux "mv" commandline.  Atomic within same volume, and overwrites the destination.
  Works for directories.

  Args:
      source (str): Source file or directory.
      dest (str): Destination file or directory. Will be overwritten if it exists.

  Raises:
      RuntimeError: Any error from the mv command
  """
  source = os.path.expanduser(source)
  dest = os.path.expanduser(dest)
  with subprocess.Popen(['mv', source, dest], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (stdout_bytes, stderr_bytes) = proc.communicate()
    exit_code = proc.returncode
  if exit_code != 0:
    stderr_s = stderr_bytes.decode('utf-8').rstrip()
    raise RuntimeError(f"Unable to move \"{source}\" to \"{dest}\", exit code {exit_code}: {stderr_s}")

class TarFilter(Enum):
  BZIP2 = 'bzip2'
  XZ = 'xz'
  LZIP = 'lzip'
  LZMA = 'lzma'
  LZOP = 'lzop'
  GZIP = 'gzip'
  COMPRESS = 'compress'
  AUTO = 'auto-compress'    # Use file extension to choose
  NONE = 'no-auto-compress'


def extract_tarball(tarball_file: str, extract_dir: str='.', filter: TarFilter=TarFilter.AUTO):
  """
  Extracts a tarball, optionally filtering through bzip, etc.

  Args:
      tarball_file (str): The filename containing the tarball.
      extract_dir (str, optional): The directory in which to expand the tarball. Defaults to '.'.
      filter (TarFilter, optional):  The compression filter to use. Defaults to TarFilter.AUTO, which
                will choose based on file extension.

  Raises:
      RuntimeError: Any error from the 'tar' command.
  """
  extract_dir = os.path.expanduser(extract_dir)
  tarball_file = os.path.expanduser(tarball_file)

  if filter is None:
    filter = TarFilter.AUTO

  filter_s: str = filter.value

  if not filter_s.startswith('-'):
    filter_s = '--' + filter_s

  with subprocess.Popen(['tar', filter_s, '-xf', tarball_file, '-C', extract_dir], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (stdout_bytes, stderr_bytes) = proc.communicate()
    exit_code = proc.returncode
  if exit_code != 0:
    stderr_s = stderr_bytes.decode('utf-8').rstrip()
    raise RuntimeError(f"Unable to extract tarball \"{tarball_file}\" to \"{extract_dir}\", exit code {exit_code}: {stderr_s}")

def mkdir_p(dirname: str):
  dirname = os.path.expanduser(dirname)
  with subprocess.Popen(['mkdir', '-p', dirname], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (stdout_bytes, stderr_bytes) = proc.communicate()
    exit_code = proc.returncode
  if exit_code != 0:
    stderr_s = stderr_bytes.decode('utf-8').rstrip()
    raise RuntimeError(f"Unable mkdir -p \"{dirname}\", exit code {exit_code}: {stderr_s}")

def download_pulumi(dirname: str, version: Optional[str]=None, stderr: TextIO=sys.stderr):
  dirname = os.path.abspath(os.path.expanduser(dirname))
  with tempfile.TemporaryDirectory() as temp_dir:
    tb_path, tb_url = download_pulumi_tarball(version=version, dirname=temp_dir)
    bin_dir = os.path.join(dirname, 'bin')
    backup_bin_dir = bin_dir + '.bak'
    tmp_install_dir = os.path.join(dirname, 'install.tmp')

    if os.path.exists(tmp_install_dir):
      shutil.rmtree(tmp_install_dir)
    
    try:
      if not os.path.exists(tmp_install_dir):
        mkdir_p(tmp_install_dir)

      extract_tarball(tb_path, tmp_install_dir)

      tmp_bin_dir = os.path.join(tmp_install_dir, 'pulumi', 'bin')
      if not os.path.exists(tmp_bin_dir):
        tmp_bin_dir = os.path.join(tmp_install_dir, 'pulumi')
        if not os.path.exists(tmp_bin_dir):
          raise RuntimeError(f"Pulumi tarball at {tb_url} does not include pulumi subdirectory")
      
      if os.path.exists(backup_bin_dir):
        shutil.rmtree(backup_bin_dir)

      success: bool = False
      try:
        if os.path.exists(bin_dir):
          unix_mv(bin_dir, backup_bin_dir)
        unix_mv(tmp_bin_dir, bin_dir)
        success = True
        if os.path.exists(backup_bin_dir):
          shutil.rmtree(backup_bin_dir)
      finally:
        if not success:
          try:
            unix_mv(backup_bin_dir, bin_dir)
          except Exception:
            pass
    finally:
      if os.path.exists(tmp_install_dir):
        try:
          shutil.rmtree(tmp_install_dir)
        except Exception:
          pass

def get_shell_cmd_path(cmd: str) -> Optional[str]:
  try:
    cmd_path: Optional[str] = subprocess.check_output(f"command -v {shlex.quote(os.path.expanduser(cmd))}", shell=True).decode('utf-8').rstrip()
    if cmd_path == '':
      cmd_path = None
  except subprocess.CalledProcessError as e:
    rc: int = e.returncode
    if rc != 1 and rc != 127:
      raise
    cmd_path = None
  return cmd_path

def get_pulumi_in_path(cmd: str="pulumi") -> Optional[str]:
  cmd_path = get_shell_cmd_path(cmd)
  return cmd_path

def get_pulumi_dir_in_path(pulumi_cmd: str) -> Optional[str]:
  result: Optional[str] = None

  actual_pulumi_cmd = get_pulumi_in_path(pulumi_cmd)
  if not actual_pulumi_cmd is None:
    real_cmd = os.path.realpath(actual_pulumi_cmd)
    bin_dir = os.path.dirname(real_cmd)
    if os.path.basename(bin_dir) != 'bin':
      raise RuntimeError(f"Pulumi CLI \"{real_cmd}\" must reside in a \"bin\" subdirectory")
    result = os.path.dirname(bin_dir)
  return result

def get_installed_pulumi_dir(dirname: Optional[str]=None) -> Optional[str]:
  result: Optional[str] = None
  if dirname is None:
    result = get_pulumi_dir_in_path('pulumi')
    if result is None:
      if os.path.exists(default_pulumi_cmd):
        result = default_pulumi_dir
  else:
    dirname = os.path.abspath(os.path.expanduser(dirname))
    if os.path.exists(os.path.join(dirname, 'bin', 'pulumi')):
      result = dirname
  return result

def get_pulumi(dirname: Optional[str]=None) -> Optional[str]:
  result: Optional[str] = None
  dirname = get_installed_pulumi_dir(dirname)
  if not dirname is None:
    result = os.path.join(dirname, 'bin', 'pulumi')
  return result

def pulumi_is_installed(dirname: Optional[str]=None) -> bool:
  return not get_pulumi(dirname) is None

def parse_pulumi_version(version: str) -> Tuple[int, int, int]:
  m = re.match(r'^ *v?([0-9]+)(\.([0-9]+)(\.([0-9]+))?)? *$', version)
  if not m:
    raise RuntimeError(f"Improperly formated pulumi version string \"{version}\"")
  major = int(m[1])
  minor = 0 if m[3] is None else int(m[3])
  subminor = 0 if m[5] is None else int(m[5])
  return major, minor, subminor

def get_pulumi_cmd_version(pulumi_cmd: str='pulumi', stderr: TextIO=sys.stderr) -> str:
  actual_pulumi_cmd = get_pulumi_in_path(pulumi_cmd)
  if actual_pulumi_cmd is None:
    raise RuntimeError("Pulumi is not installed")
  with subprocess.Popen([actual_pulumi_cmd, 'version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (pulumi_out_bytes, pulumi_err_bytes) = proc.communicate()
    exit_code = proc.returncode
  pulumi_out = pulumi_out_bytes.decode('utf-8').rstrip()
  pulumi_err = pulumi_err_bytes.decode('utf-8').rstrip()
  if exit_code != 0:
    print(pulumi_err, file=stderr)
    raise RuntimeError(f"Unexpected nonzero exit code from \"pulumi version\": {exit_code}")
  m = re.match(r'^v([^ ]+)$', pulumi_out, re.MULTILINE)
  if not m:
    print(pulumi_err, file=stderr)
    raise RuntimeError(f"Unexpected output from \"pulumi version\": \"\"\"\"{pulumi_out}\"\"")
  pulumi_version = m[1]
  return pulumi_version

def install_pulumi(
      dirname:Optional[str] = None,
      min_version: Optional[str] = None,
      upgrade_version: Optional[str] = None,
      force: bool = False,
      stderr: TextIO = sys.stderr,
    ) -> Tuple[str, bool]:
  """Installs or upgrades the standard Pulumi CLI

  Args:
      dirname (Optional[str], optional):
                       The directory where Pulumi should be installed. If None, "$HOME/.pulumi"
                      will be used. Defaults to None.
      min_version (Optional[str], optional):
                       The minimum installed version before an upgrade will be forced. If 'latest',
                       then the latest version will be installed. If None,any installed version is
                       accepted. Defaults to None.
      upgrade_version (Optional[str], optional): The version to install if an installation is performed.
                       If None or 'latest', then the latest version will be installed.. Defaults to None.
      force (bool, optional): 
                       If True, the installation will be refreshed even if the current version satisfies other
                       constraints. Defaults to False.

  Raises:
      RuntimeError: The requested upgrade_version is less than min_version
      RuntimeError: The requested upgrade version is greater than the latest available version

  Returns:
      Tuple[str, bool]: A Tuple with:
                         [0]: The absolute installation directory name; i.e.., PULUMI_HOME
                         [1]: True iff an update/install was done
  """
  if dirname is None:
    dirname = default_pulumi_dir
  min_version_t = None if min_version is None else (0, 0, 0) if min_version=='latest' else parse_pulumi_version(min_version)
  if upgrade_version == 'latest':
    upgrade_version = None
  upgrade_version_t = None if upgrade_version is None else parse_pulumi_version(upgrade_version)

  if min_version != 'latest' and not min_version_t is None and not upgrade_version_t is None:
    if upgrade_version_t < min_version_t:
      raise RuntimeError("Requested Pulumi upgrade version {upgrade_version} is less than than minimum required version {min_version}")

  dirname = os.path.abspath(os.path.expanduser(dirname))
  pulumi_cmd = os.path.join(dirname, 'bin', 'pulumi')
  old_version: Optional[str] = None
  old_version_t: Optional[Tuple[int, int, int]] = None
  if pulumi_is_installed(dirname):
    old_version = get_pulumi_cmd_version(pulumi_cmd)
    if force:
      print(f"Forcing upgrade/reinstall of Pulumi version {old_version} in {dirname}", file=sys.stderr)
    else:
      if min_version is None:
        print(f"Pulumi version {old_version} is already installed in {dirname}; no need to reinstall", file=stderr)
        return dirname, False
      else:
        old_version_t = parse_pulumi_version(old_version)
        if min_version == 'latest':
          min_version = get_pulumi_latest_version()
          min_version_t = parse_pulumi_version(min_version)
          if old_version_t >= min_version_t:
            print(f"Pulumi version {old_version} is already installed in {dirname} and is the latest version; no need to upgrade", file=stderr)
            return dirname, False
          else:
            print(f"Installed Pulumi version {old_version} in {dirname} is not the latest version {min_version}; upgrading", file=stderr)
        else:
          if min_version_t is None or old_version_t >= min_version_t:
            print(f"Pulumi version {old_version} is already installed in {dirname} and meets minimum version {min_version}; no need to upgrade", file=stderr)
            return dirname, False
          else:
            print(f"Installed Pulumi version {old_version} in {dirname} does not meet minimum version {min_version}; upgrading", file=stderr)
  else:
    print(f"Pulumi not installed in {dirname}; installing", file=stderr)

  if not min_version is None and min_version == 'latest':
    min_version = get_pulumi_latest_version()
    min_version_t = parse_pulumi_version(min_version)

  if upgrade_version is None:
    upgrade_version = get_pulumi_latest_version()
    upgrade_version_t = parse_pulumi_version(upgrade_version)
    if not min_version_t is None:
      if upgrade_version_t < min_version_t:
        raise RuntimeError(f"Minimum required Pulumi version {min_version} is greater than latest released version {upgrade_version}")
    if not force and not old_version_t is None and upgrade_version_t <= old_version_t:
      print(f"The latest Pulumi version {old_version} is already installed in {dirname}; no need to upgrade", file=stderr)
      return dirname, False

  download_pulumi(dirname, upgrade_version, stderr=stderr)
  print(f"Pulumi cli version {upgrade_version} successfully installed in {dirname}.", file=stderr)
  return dirname, True


def get_short_pulumi_cmd(cmd: str='pulumi') -> str:
  pulumi_cmd = get_pulumi_in_path(cmd)
  if pulumi_cmd is None:
    raise RuntimeError(f"Pulumi is not installed at {cmd}")
  path_pulumi_cmd = get_pulumi_in_path()
  if not path_pulumi_cmd is None and path_pulumi_cmd == pulumi_cmd:
    return 'pulumi'
  short_pulumi_cmd = os.path.relpath(pulumi_cmd, os.path.expanduser('~'))
  if not short_pulumi_cmd.startswith('.'):
    return f"~/{short_pulumi_cmd}"
  return pulumi_cmd

def get_pulumi_username(cmd: str='pulumi', stderr: TextIO=sys.stderr) -> Optional[str]:
  pulumi_cmd = get_pulumi_in_path(cmd)
  if pulumi_cmd is None:
    raise RuntimeError(f"Pulumi is not installed at {cmd}")
  with subprocess.Popen([pulumi_cmd, 'whoami'], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (pulumi_out_bytes, pulumi_err_bytes) = proc.communicate()
    exit_code = proc.returncode
  pulumi_out = pulumi_out_bytes.decode('utf-8').rstrip()
  pulumi_err = pulumi_err_bytes.decode('utf-8').rstrip()
  username: Optional[str] = None
  if pulumi_err != '':
    """
    error: PULUMI_ACCESS_TOKEN must be set for login during non-interactive CLI sessions
    """
    if not pulumi_err.startswith("error: PULUMI_ACCESS_TOKEN must be set "):
      print(pulumi_err, file=stderr)
      raise RuntimeError(f"Unexpected stderr output from \"pulumi whoami\", exit_code={exit_code}")
  else:
    if exit_code != 0:
      raise RuntimeError("Unexpected nonzero exit code from \"pulumi whoami\": {exit_code}")
    if pulumi_out == "":
      raise RuntimeError(f"Unexpected empty username output from \"pulumi whoami\"")

    username = pulumi_out
  return username

def main(argv: Optional[Sequence[str]]=None):
  import argparse

  global verbose

  parser = argparse.ArgumentParser(description='Install or upgrade Python Poetry package manager.')
  parser.add_argument('--verbose', '-v', action='store_true', default=False,
                      help='Provide verbose output.')
  parser.add_argument('--force', '-f', action='store_true', default=False,
                      help='Force installation even if not required.')
  parser.add_argument('--upgrade', '-u', action='store_true', default=False,
                      help='Upgrade to latest version. Shorthand for --min-version=latest. Ignored if --min-version is provided.')
  parser.add_argument('--dir', '-d', dest='dirname', default=None,
                      help=f"Install in the specified directory. Default={default_pulumi_dir}")
  parser.add_argument('--min-version', default=None,
                      help='Upgrade to at least the specified version. May be "latest". By default, no upgrade is performed if installed.')
  parser.add_argument('--install-version', default=None,
                      help='The version to install if installation is required. May be "latest". By default, the latest version is installed.')

  args = parser.parse_args(argv)

  verbose = args.verbose
  force: bool = args.force
  upgrade: bool = args.upgrade
  dirname: Optional[str] = args.dirname
  min_version: Optional[str] = args.min_version
  install_version: Optional[str] = args.install_version

  if min_version is None:
    if upgrade:
      min_version = 'latest'

  install_pulumi(
        dirname,
        min_version=min_version,
        upgrade_version=install_version,
        force=force
    )

if __name__ == "__main__":
  main()
