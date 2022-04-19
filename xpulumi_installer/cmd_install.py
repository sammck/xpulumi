#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
xpulumi init-env command handler
"""
from typing import (
    Optional, Dict, Callable, cast
  )

import os
from colorama import Fore, Back, Style
import subprocess

from .exceptions import XPulumiInstallerError
from .internal_types import Jsonable

from project_init_tools import (
    get_git_root_dir,
    deactivate_virtualenv,
    searchpath_prepend_if_missing,
    PackageList,
  )

from .cli import (CommandHandler)

class CmdInstall(CommandHandler):
  _no_venv_environ: Optional[Dict[str, str]] = None
  _venv_environ: Optional[Dict[str, str]] = None
  _project_root_dir: Optional[str] = None
  _xpulumi_package: Optional[str] = None

  def get_no_venv_eviron(self) -> Dict[str, str]:
    if self._no_venv_environ is None:
      no_venv_environ = dict(os.environ)
      deactivate_virtualenv(no_venv_environ)
      no_venv_environ['PATH'] = searchpath_prepend_if_missing(no_venv_environ['PATH'], os.path.join(os.path.expanduser('~'), '.local', 'bin'))
      self._no_venv_environ = no_venv_environ
    return self._no_venv_environ

  def get_venv_eviron(self) -> Dict[str, str]:
    if self._venv_environ is None:
      no_venv_environ = self.get_no_venv_eviron()
      env = dict(no_venv_environ)
      venv_dir = os.path.join(self.get_project_root_dir(), '.venv')
      venv_bin_dir = os.path.join(venv_dir, 'bin')
      env['VIRTUAL_ENV'] = venv_dir
      env['PATH'] = f"{venv_bin_dir}:{env['PATH']}"
      self._venv_environ = env
    return self._venv_environ

  def get_xpulumi_package(self) -> str:
    if self._xpulumi_package is None:
      self._xpulumi_package = cast(Optional[str], self.args.package)
      if self._xpulumi_package is None:
        self._xpulumi_package = 'https://github.com/sammck/xpulumi.git'
    return self._xpulumi_package

  def __call__(self) -> int:
    project_root_dir = self.get_project_root_dir()
    xpulumi_package = self.get_xpulumi_package()

    pl = PackageList()
    pl.add_packages_if_missing([
        'build-essential',
        'meson',
        'ninja-build',
        'python3',
        'python3-venv',
        'sqlcipher',
        'python3-grpcio',
        'python3-dev',
        'python3-pip',
        'libsqlcipher0',
        'libsqlcipher-dev,'
      ])
    pl.add_package_if_cmd_missing('sha256sum', 'coreutils')
    pl.add_package_if_cmd_missing('curl')
    pl.add_package_if_cmd_missing('git')
    pl.add_package_if_cmd_missing('jq')
    pl.install_all()

    subprocess.check_call(['python3', '-m', 'venv', './.venv'], cwd=project_root_dir, env=self.get_no_venv_eviron())
    subprocess.check_call(['pip3', 'install', '--upgrade', 'pip'], cwd=project_root_dir, env=self.get_venv_eviron())
    subprocess.check_call(['pip3', 'install', '--upgrade', 'wheel'], cwd=project_root_dir, env=self.get_venv_eviron())
    subprocess.check_call(['pip3', 'install', 'grpcio==1.43'], cwd=project_root_dir, env=self.get_venv_eviron())
    subprocess.check_call(['pip3', 'install', '--upgrade', xpulumi_package], cwd=project_root_dir, env=self.get_venv_eviron())
    return subprocess.call(
        [os.path.join(os.path.expanduser('~'), '.venv', 'bin', 'xpulumi'), 'init-env', '--phase-two'],
        cwd=project_root_dir, env=self.get_venv_eviron()
      )

  def get_project_root_dir(self) -> str:
    if self._project_root_dir is None:
      self._project_root_dir = get_git_root_dir(starting_dir=self.cwd)
      if self._project_root_dir is None:
        raise XPulumiInstallerError(f"The working directory '{self.cwd}' is not within a git project.")
    return self._project_root_dir

  @property
  def cwd(self) -> str:
    return self.cli.cwd
