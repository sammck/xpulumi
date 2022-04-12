#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
xpulumi init-env command handler
"""
import base64
from copy import deepcopy
from typing import (
    TYPE_CHECKING,
    Optional, Sequence, List, Union, Dict, TextIO, Mapping, MutableMapping,
    cast, Any, Iterator, Iterable, Tuple, ItemsView, ValuesView, KeysView,
    Callable
  )

import re
import os
import sys
import argparse
import argcomplete # type: ignore[import]
import json
from base64 import b64encode, b64decode
import boto3
import colorama # type: ignore[import]
from colorama import Fore, Back, Style
import subprocess
from io import TextIOWrapper
from requests import options
import yaml
from secret_kv import create_kv_store
from urllib.parse import urlparse, ParseResult
import ruamel.yaml # type: ignore[import]
from io import StringIO
import datetime
from tomlkit.toml_document import TOMLDocument
from tomlkit.items import Table, Item, Key, Trivia, item as toml_item
from tomlkit.container import Container, OutOfOrderTableProxy
from tomlkit.exceptions import TOMLKitError, ParseError

from .config import XPulumiConfig
from .context import XPulumiContext
from .exceptions import XPulumiError
from .base_context import XPulumiContextBase
from .backend import XPulumiBackend
from .internal_types import JsonableTypes, Jsonable, JsonableDict, JsonableList
from .constants import XPULUMI_CONFIG_DIRNAME, XPULUMI_CONFIG_FILENAME_BASE
from .version import __version__ as pkg_version
from .project import XPulumiProject

from project_init_tools import (
    full_name_of_type,
    full_type,
    get_git_root_dir,
    append_lines_to_file_if_missing,
    file_url_to_pathname,
    pathname_to_file_url,
    deactivate_virtualenv,
    get_git_config_value,
    get_git_user_friendly_name,
    get_git_user_email,
    dedent,
    get_aws_session,
    get_aws_account,
    get_aws_region,
    get_current_os_user,
    file_contents,
    PyprojectToml,
    ProjectInitConfig,
    RoundTripConfig,
    PackageList,
  )

from .cli import (CmdExitError, CommandLineInterface, CommandHandler)


aws_region_names = [
    "us-east-2",
    "us-east-1",
    "us-west-1",
    "us-west-2",
    "af-south-1",
    "ap-east-1",
    "ap-southeast-3",
    "ap-south-1",
    "ap-northeast-3",
    "ap-northeast-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-northeast-1",
    "ca-central-1",
    "eu-central-1",
    "eu-west-1",
    "eu-west-2",
    "eu-south-1",
    "eu-west-3",
    "eu-north-1",
    "me-south-1",
    "sa-east-1",
  ]

class XPulumiProjectInitConfig(ProjectInitConfig):
  pass

_email_re = re.compile(r'^\s*([\w!#$%&\'*+-/=?^_`{|}~.]+@[\w\-.]+\.[a-zA-Z]+)\s*$')
_name_and_email_re = re.compile(r'^\s*(([\w\'\s]*)\<\s*([\w!#$%&\'*+-/=?^_`{|}~.]+@[\w\-.]+\.[a-zA-Z]+)\s*\>)\s*$')
def split_name_and_email(name_and_email: str) -> Tuple[Optional[str], str]:
  m = _name_and_email_re.match(name_and_email)
  if m:
    name: Optional[str] = m.group(2).strip()
    email = m.group(3)
    if name == '':
      name = None
  else:
    m = _email_re.match(name_and_email)
    if m:
      name = None
      email = m.group(1)
    else:
      raise ValueError(f"Invalid name/email address: {name_and_email}")
  return name, email

def to_git_https(repo: str) -> str:
  if repo.startswith('git@'):
    # force to HTTPS for publishing
    https_repo = f"https://{repo[4:].replace(':', '/', 1)}"
  else:
    if not repo.startswith('https://'):
      raise XPulumiError(f"Cannot convert git URI to https:// protocol: {repo}")
    https_repo = repo
  return https_repo

class CmdInitEnv(CommandHandler):
  _config_file: Optional[str] = None
  _cfg: Optional[XPulumiProjectInitConfig] = None
  _pyproject_toml: Optional[PyprojectToml] = None
  _pyfile_header: Optional[str] = None
  _license_filename: Optional[str] = None
  _license_type: Optional[str] = None
  _license_year: Optional[int] = None
  _license_text: Optional[str] = None
  _legal_name: Optional[str] = None
  _friendly_name: Optional[str] = None
  _email_address: Optional[str] = None
  _project_name: Optional[str] = None
  _project_version: Optional[str] = None
  _project_description: Optional[str] = None
  _project_authors: Optional[List[str]] = None
  _project_keywords: Optional[List[str]] = None
  _project_readme_filename: Optional[str] = None
  _project_homepage: Optional[str] = None
  _project_repository: Optional[str] = None
  _package_import_name: Optional[str] = None
  _repo_user: Optional[str] = None
  _user_homepage: Optional[str] = None
  _project_readme_text: Optional[str] = None
  _gitignore_add_lines: Optional[List[str]] = None
  _aws_session: Optional[boto3.Session] = None
  _aws_account: Optional[str] = None
  _aws_region: Optional[str] = None
  _cloud_subaccount: Optional[str] = None
  _have_cloud_subaccount: bool = False
  _pylint_disable_list: Optional[List[str]] = None
  _round_trip_config: Optional[RoundTripConfig] = None
  _root_zone_name: Optional[str] = None

  def prompt_val(
        self,
        prompt: str,
        default: Optional[str] = None,
        converter: Optional[Callable[[str], Jsonable]] = None
      ) -> Jsonable:
    if not default is None:
      prompt += f" [{default}]"
    have_result = False
    while not have_result:
      have_result = True
      s = input(f"{self.cli.ecolor(Fore.GREEN)}\n{prompt}: {self.cli.ecolor(Style.RESET_ALL)}")
      if not default is None and s == '':
        s = default
      if converter is None:
        v: Jsonable = s
      else:
        try:
          v = converter(s)
        except Exception as e:
          print(f"Invalid input: {e}")
          have_result = False
    return v

  def get_or_prompt_config_val(
        self,
        key: str,
        prompt: Optional[str] = None,
        default: Optional[str] = None,
        converter: Optional[Callable[[str], Jsonable]] = None
      ) -> Jsonable:
    v: Optional[str] = None
    cfg = self.get_round_trip_config()
    if key in cfg:
      v = cast(Jsonable, cfg[key])
    else:
      if prompt is None:
        prompt = f"Enter configuration value \"{key}\""
      v = self.prompt_val(prompt, default=default, converter=converter)
      cfg[key] = v
      cfg.save()
    return v

  def get_cloud_subaccount(self) -> Optional[str]:
    if not self._have_cloud_subaccount:
      def validate(v: str) -> Jsonable:
        v = v.strip()
        if v == '':
          return None

        if not re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', v):
          raise ValueError("Subaccount name must start with a letter and be alphanumeric")

        return v

      self._cloud_subaccount = cast(Optional[str], self.get_or_prompt_config_val(
        'cloud_subaccount',
        prompt=dedent('''
            Enter an optional "cloud subaccount" name. If provided, this
            name will be used to uniqueify account-wide resources created in
            your AWS account that would otherwise collide with other identical
            xpulumi projects deployed in the same account; e.g., DNS names,
            S3 bucket names, etc. Normally you will leave this blank
          ''').rstrip(),
          converter=validate
      ))
      self._have_cloud_subaccount = True
    return self._cloud_subaccount

  def get_root_zone_name(self) -> str:
    import dns.resolver
    if self._root_zone_name is None:
      def validate_dns(v: str) -> Jsonable:
        if v == '':
          raise ValueError('DNS name cannot be an empty string')
        entries = dns.resolver.resolve(v, 'NS')
        if len(entries) == 0:
          raise ValueError(f"NS record for {v} has no entries")
        ns_servers = [ x.target.to_text() for x in entries ]
        for ns_server in ns_servers:
          if not '.awsdns-' in ns_server:
            raise ValueError('Domain name {v} has nameserver {ns_server} which is not hosted by Route53')
        print(f"Domain name {v} is valid and hosted by Route53...", file=sys.stderr)

        return v

      root_zone_name = self.get_or_prompt_config_val(
        'root_zone_name',
        prompt=dedent('''
            Enter the name of an existing DNS domain that is already hosted by
            AWS Route53 in your AWS account. This domain will become the root
            for new subzones created by your xpulumi project.
            (e.g., mydomain.com)
          ''').rstrip(),
          converter=validate_dns
      )
      self._root_zone_name = root_zone_name
    return self._root_zone_name

  def get_project_name(self) -> str:
    if self._project_name is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_name = cast(Optional[str], t_tool_poetry.get('name', None))
      if project_name is None:
        project_name = os.path.basename(self.get_project_root_dir())
      self._project_name = project_name
    return self._project_name

  def get_package_import_name(self) -> str:
    if self._package_import_name is None:
      package_import_name = self.get_project_name().replace('-', '_')
      self._package_import_name = package_import_name
    return self._package_import_name

  def get_project_version(self) -> str:
    if self._project_version is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_version = cast(Optional[str], t_tool_poetry.get('version', None))
      if project_version is None:
        project_version = '0.1.0'
      self._project_version = project_version
    return self._project_version

  def get_project_description(self) -> str:
    if self._project_description is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_description = cast(Optional[str], t_tool_poetry.get('description', None))
      if project_description is None:
        project_description = f'Python package {self.get_project_name()}'
      self._project_description = project_description
    return self._project_description

  def get_project_authors(self) -> List[str]:
    if self._project_authors is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_authors = cast(Optional[List[str]], t_tool_poetry.get('authors', None))
      if project_authors is None:
        project_authors = [ f"{self.get_friendly_name()} <{self.get_email_address()}>" ]
      self._project_authors = project_authors
    return self._project_authors

  def get_project_keywords(self) -> List[str]:
    if self._project_keywords is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_keywords = cast(Optional[List[str]], t_tool_poetry.get('keywords', None))
      if project_keywords is None:
        project_keywords = []
      self._project_keywords = project_keywords
    return self._project_keywords

  def get_project_readme_filename(self) -> str:
    if self._project_readme_filename is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_readme_filename = cast(Optional[str], t_tool_poetry.get('readme', None))
      if project_readme_filename is None:
        project_readme_filename = 'README.md'
      project_readme_filename = os.path.abspath(os.path.join(
          self.get_project_root_dir(), os.path.normpath(os.path.expanduser(project_readme_filename))))
      self._project_readme_filename = project_readme_filename
    return self._project_readme_filename

  def get_project_readme_rel_filename(self) -> str:
    pathname = self.get_project_readme_filename()
    relpath = os.path.relpath(pathname, self.get_project_root_dir())
    if relpath.startswith('./'):
      relpath = relpath[2:]
    return relpath

  def get_project_repository_https(self) -> str:
    if self._project_repository is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_repository = cast(Optional[str], t_tool_poetry.get('repository', None))
      if project_repository is None:
        git_remote_repo = get_git_config_value('remote.origin.url', cwd=self.get_project_root_dir())
        if not git_remote_repo is None:
          project_repository = to_git_https(git_remote_repo)
      if project_repository is None:
        raise XPulumiError("Cannot determine project repository URI")
      self._project_repository = project_repository
    return self._project_repository

  def get_project_homepage(self) -> str:
    if self._project_homepage is None:
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_homepage = cast(Optional[str], t_tool_poetry.get('homepage', None))
      if project_homepage is None:
        https_repo = self.get_project_repository_https()
        if https_repo.endswith('.git'):
          project_homepage = https_repo[:-4]
      if project_homepage is None:
        project_homepage = f"https://github.com/{get_current_os_user()}/{self.get_project_name()}"
      self._project_homepage = project_homepage
    return self._project_homepage

  def get_user_homepage(self) -> str:
    if self._user_homepage is None:
      https_repo = self.get_project_repository_https()
      if https_repo.endswith('.git'):
        https_repo = https_repo[:-4]
      self._user_homepage = https_repo.rsplit('/', 1)[0]
    return self._user_homepage

  def get_license_year(self) -> int:
    return datetime.date.today().year if self._license_year is None else self._license_year

  def get_email_address(self) -> str:
    if self._email_address is None:
      def validate(v: str) -> Jsonable:
        v = v.strip()
        parts = v.rsplit('@', 1)
        if len(parts) == 2:
          if '.' in parts[1]:
            return v
        raise ValueError(f"Invalid email address: {v}")

      email_address: Optional[str] = None
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_authors = cast(Optional[List[str]], t_tool_poetry.get('authors', None))
      if not project_authors is None and len(project_authors) == 1:
        project_author = str(project_authors[0])
        _, email_address = split_name_and_email(project_author)
      if email_address is None:
        try:
          email_address = get_git_user_email()
        except subprocess.CalledProcessError:
          pass

      self._email_address = cast(str, self.get_or_prompt_config_val(
        'email_address',
        prompt=dedent('''
            Enter your email address, to be used for Python package
            configuration
          ''').rstrip(),
          converter=validate,
          default=email_address
      ))

    return self._email_address

  def get_friendly_name(self) -> str:
    if self._friendly_name is None:
      def validate(v: str) -> Jsonable:
        v = v.strip()
        parts = [ x for x in v.split(' ') if x != '' ]
        if len(parts) < 2:
          raise ValueError(f"A first and last name are required: {v}")
        v = ' '.join(parts)
        return v

      friendly_name: Optional[str] = None
      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      project_authors = cast(Optional[List[str]], t_tool_poetry.get('authors', None))
      if not project_authors is None and len(project_authors) == 1:
        project_author = str(project_authors[0])
        friendly_name, _ = split_name_and_email(project_author)
      if friendly_name is None:
        try:
          friendly_name = get_git_user_friendly_name()
        except subprocess.CalledProcessError:
          pass

      self._friendly_name = cast(str, self.get_or_prompt_config_val(
        'friendly_name',
        prompt=dedent('''
            Enter your friendly full name (e.g., "John Doe"), to be used for Python package
            configuration
          ''').rstrip(),
          converter=validate,
          default=friendly_name
      ))

    return self._friendly_name

  def get_legal_name(self) -> str:
    if self._legal_name is None:
      def validate(v: str) -> Jsonable:
        v = v.strip()
        parts = [ x for x in v.split(' ') if x != '' ]
        if len(parts) < 2:
          raise ValueError(f"A first and last name are required: {v}")
        v = ' '.join(parts)
        return v


      self._legal_name = cast(str, self.get_or_prompt_config_val(
        'friendly_name',
        prompt=dedent('''
            Enter your friendly full name (e.g., "John Doe"), to be used for Python package
            configuration
          ''').rstrip(),
          converter=validate,
          default=self.get_friendly_name()
      ))

    return self._legal_name

  def get_license_type(self) -> str:
    if self._license_type is None:
      def validate(v: str) -> Jsonable:
        v = v.strip().upper()
        if v == '':
          v = 'MIT'
        if v != 'MIT':
          raise ValueError("Unrecognized license type (only MIT is currently supported): {v}")
        return v

      pyproject = self.get_pyproject_toml(create=True)
      t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
      license_type = cast(Optional[str], t_tool_poetry.get('license', None))
      if license_type is None:
        if not self._license_text is None or os.path.exists(self.get_license_filename()):
          license_text = self.get_license_text()
          license_first_line = license_text.split('\n', 1)[0].strip().lower()
          if license_first_line == 'mit license':
            license_type = 'MIT'
      if license_type is None:
        license_type = 'MIT'

      self._license_type = cast(str, self.get_or_prompt_config_val(
        'license_type',
        prompt=dedent('''
            Enter the type of license to use for this project (e.g., MIT)
          ''').rstrip(),
          converter=validate,
          default=license_type
      ))

    return self._license_type

  def get_aws_session(self):
    if self._aws_session is None:
      self._aws_session = get_aws_session()
    return self._aws_session

  def get_aws_account(self):
    if self._aws_account is None:
      self._aws_account = get_aws_account(self.get_aws_session())
    return self._aws_account

  def get_aws_region(self) -> str:
    if self._aws_region is None:
      def validate(v: str) -> Jsonable:
        v = v.strip().lower()
        if not v in aws_region_names:
          raise ValueError("Unrecognized AWS region name: {v}")
        return v

      aws_region: Optional[str] = None
      try:
        aws_region = cast(Optional[str], get_aws_region(self.get_aws_session()))
      except Exception:
        pass
      if aws_region is None:
        aws_region = 'us-west-2'

      self._aws_region = cast(str, self.get_or_prompt_config_val(
        'aws_region',
        prompt=dedent('''
            Enter the AWS region you would like to deploy to (e.g., "us-west-2").
            "us-west-2" is in Oregon and offers the lowest ping latencies to
            Seattle
          ''').rstrip(),
          converter=validate,
          default=aws_region,
      ))
    return self._aws_region

  def get_license_filename(self) -> str:
    if self._license_filename is None:
      self._license_filename = os.path.join(self.get_project_root_dir(), 'LICENSE')
    return self._license_filename

  def get_license_text(self) -> str:
    if self._license_text is None:
      filename = self.get_license_filename()
      if os.path.exists(filename):
        self._license_text = file_contents(filename)
      else:
        license_type = self.get_license_type()
        if license_type != 'MIT':
          raise XPulumiError(f"Don't know how to generate license text for license type \"{license_type}\"")
        year = self.get_license_year()
        legal_name = self.get_legal_name()
        license_text = dedent(f"""
            {license_type} License

            Copyright (c) {year} {legal_name}

            Permission is hereby granted, free of charge, to any person obtaining a copy
            of this software and associated documentation files (the "Software"), to deal
            in the Software without restriction, including without limitation the rights
            to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
            copies of the Software, and to permit persons to whom the Software is
            furnished to do so, subject to the following conditions:

            The above copyright notice and this permission notice shall be included in all
            copies or substantial portions of the Software.

            THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
            IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
            FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
            AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
            LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
            OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
            SOFTWARE.
          """)
        self._license_text = license_text
    return self._license_text

  def get_repo_user(self) -> str:
    if self._repo_user is None:
      repository = self.get_project_repository_https()
      trep_path = urlparse(repository).path
      if trep_path.startswith('/'):
        trep_path = trep_path[1:]
      self._repo_user = trep_path.split('/', 1)[0]
    return self._repo_user

  def get_project_readme_text(self) -> str:
    if self._project_readme_text is None:
      filename = self.get_project_readme_filename()
      if os.path.exists(filename):
        with open(filename, encoding='utf-8') as f:
          self._project_readme_text = f.read()
      else:
        license_type = self.get_license_type()
        if license_type == 'MIT':
          readme_project_license = (
              '[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)'
            )
          readme_project_license_short = (
              '[MIT License](https://opensource.org/licenses/MIT)'
            )
        else:
          readme_project_license = (
              f'[![License: {license_type}]](https://opensource.org/licenses)'
            )
          readme_project_license_short = (
              '[{license_type} License](https://opensource.org/licenses)'
            )
        project_name = self.get_project_name()
        repo_user = self.get_repo_user()
        project_description = self.get_project_description()
        project_repository = self.get_project_repository_https()
        project_homepage = self.get_project_homepage()
        friendly_name = self.get_friendly_name()
        user_homepage = self.get_user_homepage()
        self._project_readme_text = dedent(f"""
            {project_name}: A Python package
            =================================================

            {readme_project_license}
            [![Latest release](https://img.shields.io/github/v/release/{repo_user}/{project_name}.svg?style=flat-square&color=b44e88)](https://github.com/{repo_user}/{project_name}/releases)

            {project_description}

            Table of contents
            -----------------

            * [Introduction](#introduction)
            * [Installation](#installation)
            * [Usage](#usage)
              * [API](api)
            * [Known issues and limitations](#known-issues-and-limitations)
            * [Getting help](#getting-help)
            * [Contributing](#contributing)
            * [License](#license)
            * [Authors and history](#authors-and-history)


            Introduction
            ------------

            Python package `{project_name}` BLAH BLAH.

            Installation
            ------------

            ### Prerequisites

            **Python**: Python 3.8+ is required. See your OS documentation for instructions.

            ### From PyPi

            The current released version of `{project_name}` can be installed with

            ```bash
            pip3 install {project_name}
            ```

            ### From GitHub

            [Poetry](https://python-poetry.org/docs/master/#installing-with-the-official-installer) is required; it can be installed with:

            ```bash
            curl -sSL https://install.python-poetry.org | python3 -
            ```

            Clone the repository and install {project_name} into a private virtualenv with:

            ```bash
            cd <parent-folder>
            git clone {project_repository}
            cd {project_name}
            poetry install
            ```

            You can then launch a bash shell with the virtualenv activated using:

            ```bash
            poetry shell
            ```


            Usage
            =====

            API
            ---

            TBD

            Known issues and limitations
            ----------------------------


            Getting help
            ------------

            Please report any problems/issues [here]({project_homepage}/issues).

            Contributing
            ------------

            Pull requests welcome.

            License
            -------

            `{project_name}` is distributed under the terms of the {readme_project_license_short}.  The license applies to this file and other files in the [Git repository]({project_homepage}) hosting this file.

            Authors and history
            -------------------

            The author of {project_name} is [{friendly_name}]({user_homepage}).
          """)
    return self._project_readme_text

  def get_gitignore_add_lines(self) -> List[str]:
    if self._gitignore_add_lines is None:
      self._gitignore_add_lines=dedent('''
          __pycache__/
          *.py[cod]
          *$py.class
          *.so
          .Python
          build/
          develop-eggs/
          dist/
          downloads/
          eggs/
          .eggs/
          lib/
          lib64/
          parts/
          sdist/
          var/
          wheels/
          pip-wheel-metadata/
          share/python-wheels/
          *.egg-info/
          .installed.cfg
          *.egg
          MANIFEST
          pip-log.txt
          pip-delete-this-directory.txt
          htmlcov/
          .tox/
          .nox/
          .coverage
          .coverage.*
          .cache
          nosetests.xml
          coverage.xml
          *.cover
          *.py,cover
          .hypothesis/
          .pytest_cache/
          *.mo
          *.pot
          *.log
          local_settings.py
          db.sqlite3
          db.sqlite3-journal
          instance/
          .webassets-cache
          .scrapy
          docs/_build/
          target/
          .ipynb_checkpoints
          profile_default/
          ipython_config.py
          .python-version
          __pypackages__/
          celerybeat-schedule
          celerybeat.pid
          *.sage.py
          .env
          .venv
          env/
          venv/
          ENV/
          env.bak/
          venv.bak/
          .spyderproject
          .spyproject
          .ropeproject
          /site
          .mypy_cache/
          .dmypy.json
          dmypy.json
          .pyre/
          /trash/
          /.xppulumi/
          /.secret-kv/
          /.local/
        ''').rstrip().split('\n')
    return self._gitignore_add_lines

  def get_pylint_disable_list(self) -> List[str]:
    if self._pylint_disable_list is None:
      self._pylint_disable_list = [
          "wrong-import-order",
          "duplicate-code",
          "too-many-arguments",
          "missing-function-docstring",
          "import-outside-toplevel",
          "too-few-public-methods",
          "missing-class-docstring",
          "unused-import",
          "too-many-locals",
          "unused-argument",
          "invalid-name",
          "no-self-use",
          "global-statement",
          "broad-except",
          "too-many-branches",
          "too-many-statements",
          "exec-used",
          "ungrouped-imports",
          "subprocess-popen-preexec-fn",
          "multiple-statements",
          "too-many-public-methods",
          "missing-module-docstring",
          "too-many-instance-attributes",
          "too-many-nested-blocks",
          "unneeded-not",
          "unnecessary-lambda",
        ]
    return self._pylint_disable_list

  def get_pyfile_header(self) -> str:
    if self._pyfile_header is None:
      self._pyfile_header = dedent(f'''
              # Copyright (c) {self.get_license_year()} {self.get_legal_name()}
              #
              # See LICENSE file accompanying this package.
              #

        ''')
    return self._pyfile_header

  def get_package_dir(self) -> str:
    return os.path.join(self.get_project_root_dir(), self.get_package_import_name())

  def get_xp_dir(self) -> str:
    xp_dir = os.path.join(self.get_project_root_dir(), 'xp')
    return xp_dir

  def get_xp_project_parent_dir(self) -> str:
    xp_project_parent_dir = os.path.join(self.get_xp_dir(), 'project')
    return xp_project_parent_dir

  def get_xp_backend_parent_dir(self) -> str:
    xp_backend_parent_dir = os.path.join(self.get_xp_dir(), 'backend')
    return xp_backend_parent_dir

  def get_xp_project_dir(self, project_name: str) -> str:
    xp_project_dir = os.path.join(self.get_xp_project_parent_dir(), project_name)
    return xp_project_dir

  def get_xp_backend_dir(self, backend_name: str) -> str:
    xp_backend_dir = os.path.join(self.get_xp_backend_parent_dir(), backend_name)
    return xp_backend_dir

  def write_pyfile(self, filename: str, content: str, executable: bool = False) -> None:
    filename = os.path.join(self.get_package_dir(), filename)
    if not os.path.exists(filename):
      with open(filename, 'w', encoding='utf-8') as f:
        if executable:
          f.write("#!/usr/bin/env python3\n")
        f.write(self.get_pyfile_header()+dedent(content))
        if executable:
          subprocess.call(['chmod','+x', filename], stderr=subprocess.DEVNULL)

  def write_standard_xp_project_main(self, project_name: str, standard_stack_name: str) -> None:
    xp_project_dir = self.get_xp_project_dir(project_name)
    if not os.path.isdir(xp_project_dir):
      os.makedirs(xp_project_dir)
    filename = os.path.join(xp_project_dir, '__main__.py')
    self.write_pyfile(filename, dedent(f'''
        from xpulumi.standard_stacks.{standard_stack_name} import load_stack

        load_stack()
      '''))

  def create_yaml_file(self, filename: str, content: Jsonable) -> None:
    filename = os.path.join(self.get_project_root_dir(), filename)
    if not os.path.exists(filename):
      with open(filename, 'w', encoding='utf-8') as f:
        yaml.dump(content, f)

  def create_xp_backend(
        self,
        backend_name: str,
        config: Optional[JsonableDict] = None,
        uri: Optional[str] = None,
        options: Optional[JsonableDict] = None,   #pylint: disable=redefined-outer-name
        includes_organization: Optional[bool] = None,
        includes_project: Optional[bool] = None,
        default_organization: Optional[str] = None,
        backend_xstack: Optional[str] = None,
      ) -> None:
    xp_backend_dir = self.get_xp_backend_dir(backend_name)

    if config is None:
      config = {}
    else:
      config = deepcopy(config)

    coptions = cast(Optional[JsonableDict], config.get('options', None))
    if coptions is None:
      coptions = {}
      config['options'] = coptions

    if not options is None:
      coptions.update(deepcopy(options))

    options = coptions

    if not uri is None:
      config['uri'] = uri

    config['name'] = backend_name
    if not backend_xstack is None:
      coptions['backend_xstack'] = backend_xstack

    uri = cast(Optional[str], config.get('uri', None))

    if uri is None:
      raise XPulumiError("A URI is required for backend config")

    parts = urlparse(uri)
    is_blob_backend = parts.scheme in ('file', 's3')
    includes_organization = not is_blob_backend if includes_organization is None else includes_organization
    includes_project = not is_blob_backend if includes_project is None else includes_project
    default_organization = 'g' if default_organization is None else default_organization

    if not 'includes_organization' in options:
      options['includes_organization'] = includes_organization
    if not 'includes_project' in options:
      options['includes_project'] = includes_project
    if not options['includes_organization']:
      if not 'default_organization' in options:
        options['default_organization'] = default_organization
    if parts.scheme == 's3':
      if not 'aws_region' in options:
        options['aws_region'] = self.get_aws_region()
      if not 'aws_account' in options:
        options['aws_account'] = self.get_aws_account()

    if not os.path.isdir(xp_backend_dir):
      os.makedirs(xp_backend_dir)
    self.create_yaml_file(os.path.join(xp_backend_dir, "backend.yaml"), config)

  def create_local_xp_backend(
        self,
        backend_name: str,
        config: Optional[JsonableDict] = None,
        options: Optional[JsonableDict] = None,     #pylint: disable=redefined-outer-name
        includes_organization: Optional[bool] = None,
        includes_project: Optional[bool] = None,
        default_organization: Optional[str] = None,
      ) -> None:
    self.create_xp_backend(
        backend_name,
        config=config,
        options=options,
        includes_organization=includes_organization,
        includes_project=includes_project,
        default_organization=default_organization,
        uri="file://./state"
      )

    xp_backend_dir = self.get_xp_backend_dir(backend_name)
    state_dir = os.path.join(xp_backend_dir, 'state')
    if not os.path.isdir(state_dir):
      os.makedirs(state_dir)
    gitignore_file = os.path.join(state_dir, '.gitignore')
    if not os.path.exists(gitignore_file):
      with open(gitignore_file, 'w', encoding='utf-8') as f:
        f.write("!.pulumi/\n")

  def create_xp_project(
        self,
        project_name: str,
        standard_stack_name: Optional[str]=None,
        main_script_content: Optional[str]=None,
        xpulumi_config: Optional[JsonableDict]=None,
        pulumi_config: Optional[JsonableDict]=None,
        pulumi_stack_configs: Optional[Dict[str, JsonableDict]]=None,
        organization: Optional[str]='g',
        backend: str='s3',
        description: Optional[str] = None,
      ) -> None:
    if standard_stack_name is None and main_script_content is None:
      raise XPulumiError("Either standard_stack_name or main_script_content must be provided")

    xp_project_dir = self.get_xp_project_dir(project_name)

    if xpulumi_config is None:
      xpulumi_config = {}
    else:
      xpulumi_config = deepcopy(xpulumi_config)
    if not organization is None and not 'organization' in xpulumi_config:
      xpulumi_config['organization'] = organization
    if not 'backend' in xpulumi_config:
      xpulumi_config['backend'] = backend

    if pulumi_config is None:
      pulumi_config = {}
    else:
      pulumi_config = deepcopy(pulumi_config)
    if not 'name' in pulumi_config:
      pulumi_config['name'] = project_name
    pulumi_project_name = pulumi_config['name']
    if not 'runtime' in pulumi_config:
      pulumi_config['runtime'] = dict(
          name = "python",
          options = dict(
              virtualenv = "../../../.venv"
            )
        )
    if not 'description' in pulumi_config:
      if description is None:
        if standard_stack_name is None:
          description = f"XPulumi project {project_name}"
        else:
          description = f"Standard {standard_stack_name} xpulumi project {project_name}"
      pulumi_config['description'] = description

    if pulumi_stack_configs is None:
      pulumi_stack_configs = {}

    for stack_name, stack_config in list(pulumi_stack_configs.items()):
      if stack_config is None:
        stack_config = {}
      else:
        stack_config = deepcopy(stack_config)
      pulumi_stack_configs[stack_name] = stack_config
      for k, v in list(stack_config.items()):
        if not ':' in k:
          # For convenience, replace any bare config property_name
          # with <project_name>:<property_name>
          del stack_config[k]
          k = f"{pulumi_project_name}:{k}"
          stack_config[k] = v

        if v is None:
          del stack_config[k]

      if not 'aws:region' in stack_config:
        stack_config['aws:region'] = self.get_aws_region()

    self.write_standard_xp_project_main(project_name, standard_stack_name)
    self.create_yaml_file(os.path.join(xp_project_dir, "xpulumi-project.yaml"), xpulumi_config)
    self.create_yaml_file(os.path.join(xp_project_dir, "Pulumi.yaml"), pulumi_config)
    for stack_name, stack_config in pulumi_stack_configs.items():
      self.create_yaml_file(os.path.join(xp_project_dir, f"Pulumi.{stack_name}.yaml"), stack_config)

  def set_pyproject_default(self, table: Union[str, Table, OutOfOrderTableProxy, Container], key, value) -> None:
    if isinstance(table, str):
      pyproject = self.get_pyproject_toml(create=True)
      table = pyproject.get_table(table, auto_split=True, create=True)
    if not value is None and not key in table:
      table[key] = value

  def get_cloud_subaccount_prefix(self) -> str:
    cloud_subaccount = self.get_cloud_subaccount()
    return '' if cloud_subaccount is None else f"{cloud_subaccount}-"

  def __call__(self) -> int:
    from project_init_tools.installer.docker import install_docker
    from project_init_tools.installer.aws_cli import install_aws_cli
    from project_init_tools.installer.gh import install_gh
    from project_init_tools.installer.pulumi import install_pulumi
    from project_init_tools.installer.poetry import install_poetry

    self.get_or_create_config()
    project_root_dir = self.get_project_root_dir()
    project_init_dir = self.get_project_init_dir()
    if not os.path.exists(project_init_dir):
      os.makedirs(project_init_dir)
    local_dir = self.get_project_local_dir()
    if not os.path.exists(local_dir):
      os.makedirs(local_dir)

    # Install hard prerequites needed before we create
    # the virtualenv and install xpulumi into it

    pl = PackageList()
    pl.add_packages_if_missing(['build-essential', 'meson', 'ninja-build', 'python3.8', 'python3.8-venv', 'sqlcipher'])
    pl.add_package_if_cmd_missing('sha256sum', 'coreutils')
    pl.add_package_if_cmd_missing('curl')
    pl.add_package_if_cmd_missing('git')
    pl.install_all()

    install_poetry()

    license_filename = self.get_license_filename()
    license_text = self.get_license_text()

    pyproject = self.get_pyproject_toml(create=True)
    t_tool_poetry = pyproject.get_table('tool.poetry', auto_split=True, create=True)
    project_name  = self.get_project_name()
    project_version = self.get_project_version()
    project_description = self.get_project_description()
    project_authors = self.get_project_authors()
    license_type = self.get_license_type()
    project_keywords = self.get_project_keywords()
    project_readme_filename = self.get_project_readme_filename()
    project_readme_rel_filename = self.get_project_readme_rel_filename()
    project_homepage = self.get_project_homepage()
    project_repository_https = self.get_project_repository_https()
    package_import_name = self.get_package_import_name()
    repo_user = self.get_repo_user()
    friendly_name = self.get_friendly_name()
    legal_name = self.get_legal_name()
    year = self.get_license_year()
    license_text = self.get_license_text()
    project_readme_text = self.get_project_readme_text()
    gitignore_file = os.path.join(project_root_dir, ".gitignore")
    gitignore_add_lines = self.get_gitignore_add_lines()
    pylint_disable_list = self.get_pylint_disable_list()

    append_lines_to_file_if_missing(gitignore_file, gitignore_add_lines, create_file=True)

    self.set_pyproject_default(t_tool_poetry, 'name', project_name)
    self.set_pyproject_default(t_tool_poetry, 'version', project_version)
    self.set_pyproject_default(t_tool_poetry, 'description', project_description)
    self.set_pyproject_default(t_tool_poetry, 'authors', project_authors)
    self.set_pyproject_default(t_tool_poetry, 'license', license_type)
    self.set_pyproject_default(t_tool_poetry, 'keywords', project_keywords)
    self.set_pyproject_default(t_tool_poetry, 'readme', project_readme_rel_filename)
    self.set_pyproject_default(t_tool_poetry, 'homepage', project_homepage)
    self.set_pyproject_default(t_tool_poetry, 'repository', project_repository_https)

    t_tool_poetry_dependencies = pyproject.get_table('tool.poetry.dependencies', auto_split=True, create=True)
    self.set_pyproject_default(t_tool_poetry_dependencies, 'python', "^3.8")
    self.set_pyproject_default(t_tool_poetry_dependencies,
        'xpulumi', dict(git="https://github.com/sammck/xpulumi.git", branch='main'))

    t_tool_poetry_dev_dependencies = pyproject.get_table('tool.poetry.dev-dependencies', auto_split=True, create=True)
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'mypy', "^0.931")
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'dunamai', "^1.9.0")
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'python-semantic-release', "^7.25.2")
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'types-urllib3', "^1.26.11")
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'types-PyYAML', "^6.0.5")
    self.set_pyproject_default(t_tool_poetry_dev_dependencies, 'pylint', "^2.13.5")

    t_build_system = pyproject.get_table('build-system', auto_split=True, create=True)
    self.set_pyproject_default(t_build_system, 'requires', ["poetry-core>=1.0.0"])
    self.set_pyproject_default(t_build_system, 'build-backend', "poetry.core.masonry.api")

    # Create an empty table for convenience
    pyproject.get_table('tool.poetry.scripts', auto_split=True, create=True)

    t_tool_semantic_release = pyproject.get_table('tool.semantic_release', auto_split=True, create=True)
    self.set_pyproject_default(t_tool_semantic_release, 'version_variable', f'{package_import_name}/version.py:__version__')
    self.set_pyproject_default(t_tool_semantic_release, 'version_toml', 'pyproject.toml:tool.poetry.version')
    self.set_pyproject_default(t_tool_semantic_release, 'upload_to_pypi', False)
    self.set_pyproject_default(t_tool_semantic_release, 'upload_to_release', True)
    self.set_pyproject_default(t_tool_semantic_release, 'build_command', "pip install poetry && poetry build")
    t_tool_pylint_messages_control = pyproject.get_table(['tool', 'pylint', 'MESSAGES CONTROL'], create=True)
    self.set_pyproject_default(
        t_tool_pylint_messages_control,
        'disable',
        toml_item(pylint_disable_list).multiline(True),
      )
    t_tool_pylint_format = pyproject.get_table('tool.pylint.FORMAT', auto_split = True, create=True)
    self.set_pyproject_default(t_tool_pylint_format, 'indent-after-paren', 4)
    self.set_pyproject_default(t_tool_pylint_format, 'indent-string', '  ')
    self.set_pyproject_default(t_tool_pylint_format, 'max-line-length', 200)

    pyproject.save()

    package_dir = os.path.join(project_root_dir, package_import_name)
    if not os.path.exists(package_dir):
      os.mkdir(package_dir)

    self.write_pyfile("__init__.py", f'''
            """
            Package {package_import_name}: {project_description}
            """

            from .version import __version__
          ''')
    self.write_pyfile("version.py", f'''
            """
            Automatically updated version information for this package
            """

            # The following line is automatically updated with "semantic-release version"
            __version__ =  "{project_version}"

            __all__ = [ '__version__' ]
          ''')

    pytyped_file = os.path.join(package_dir, 'py.typed')
    if not os.path.exists(pytyped_file):
      with open(pytyped_file, 'w', encoding='utf-8') as f:
        pass

    if not license_text is None and not os.path.exists(license_filename):
      with open(license_filename, 'w', encoding='utf-8') as f:
        f.write(license_text)

    if not project_readme_text is None and not os.path.exists(project_readme_filename):
      with open(project_readme_filename, 'w', encoding='utf-8') as f:
        f.write(project_readme_text)

    no_venv_environ = dict(os.environ)
    deactivate_virtualenv(no_venv_environ)

    subprocess.check_call(['poetry', 'install'], cwd=project_root_dir, env=no_venv_environ)

    # ================================================================
    # Everything from here down can be done after xpulumi is installed

    # aws_session = self.get_aws_session()
    aws_account = self.get_aws_account()
    aws_region = self.get_aws_region()
    # allows us to create multiple parallel installations in the same AWS account
    cloud_subaccount_prefix = self.get_cloud_subaccount_prefix()

    local_backend_name = 'local'

    s3_backend_project_name = "s3_backend"
    s3_backend_stack_name = "global"

    s3_backend_name = 's3'
    s3_backend_bucket_name = f"{cloud_subaccount_prefix}{aws_account}-{aws_region}-xpulumi"
    s3_backend_subkey = f"{cloud_subaccount_prefix}xpulumi-be"
    s3_backend_uri = f"s3://{s3_backend_bucket_name}/{s3_backend_subkey}"

    dev_stack_name = 'dev'
    root_zone_name = 'mckelvie.org'
    subzone_name = f"{cloud_subaccount_prefix}dev"

    # ------

    self.create_local_xp_backend(local_backend_name)
    self.create_xp_project(
        s3_backend_project_name,
        standard_stack_name = 's3_backend_v1',
        backend = local_backend_name,
        description = "Simple locally-backed pulumi project that manages an S3 backend used by all other projects",
        pulumi_stack_configs = {
            s3_backend_stack_name: dict(
                backend_url = s3_backend_uri,
            )
          }
      )

    self.create_xp_backend(
        s3_backend_name,
        uri=s3_backend_uri,
        backend_xstack=f"{s3_backend_project_name}:{s3_backend_stack_name}"
      )

    self.create_xp_project(
        'awsenv',
        standard_stack_name = 's3_aws_env_v1',
        backend = s3_backend_name,
        description = "AWS project core resources (VPC etc)",
        pulumi_stack_configs = {
            dev_stack_name: dict(
                vpc_n_azs = 3,
                vpc_n_potential_subnets = 16,
                vpc_cidr = "10.78.0.0/16",
                root_zone_name = root_zone_name,
                subzone_name = subzone_name,
              )
          },
      )

    install_docker()
    install_aws_cli()
    install_gh()

    project_init_pulumi_dir = os.path.join(local_dir, '.pulumi')
    install_pulumi(project_init_pulumi_dir, min_version='latest')
    secret_kv_dir = os.path.join(project_root_dir, '.secret-kv')
    if not os.path.exists(secret_kv_dir):
      create_kv_store(project_root_dir)

    return 0

  def get_or_create_config(self) -> XPulumiProjectInitConfig:
    if self._cfg is None and self._config_file is None:
      project_root_dir = get_git_root_dir(self.cwd)
      if project_root_dir is None:
        raise XPulumiError("Could not locate Git project root directory; please run inside git working directory or use -C")
      project_init_dir = os.path.join(project_root_dir, 'project-init')
      if not os.path.exists(project_init_dir):
        os.mkdir(project_init_dir)
      config_file = os.path.join(project_init_dir, "config.yaml")
      self._config_file = config_file
      if not os.path.exists(config_file):
        new_config_data: JsonableDict = {}
        with open(config_file, 'w', encoding='utf-8') as f:
          yaml.dump(new_config_data, f)
    return self.get_config()

  def get_config(self) -> XPulumiProjectInitConfig:
    if self._cfg is None:
      self._cfg = XPulumiProjectInitConfig(starting_dir=self.cwd)
    return self._cfg

  def get_round_trip_config(self) -> RoundTripConfig:
    if self._round_trip_config is None:
      cfg = self.get_or_create_config()
      self._round_trip_config = RoundTripConfig(cfg.config_file)
    return self._round_trip_config

  def save_round_trip_config(self) -> None:
    if not self._round_trip_config is None:
      config_file = self._round_trip_config._config_file   # pylint: disable=protected-access
      changed = self._round_trip_config.save()
      if changed:
        self._cfg = XPulumiProjectInitConfig(config_file=config_file)

  def get_config_file(self) -> str:
    return self.get_config().config_file

  def update_config(self, *args, **kwargs):
    cfg_file = self.get_config_file()
    rt = RoundTripConfig(cfg_file)
    rt.update(*args, **kwargs)
    rt.save()

  def get_project_root_dir(self) -> str:
    return self.get_config().project_root_dir

  def get_project_init_dir(self) -> str:
    return self.get_config().project_init_dir

  def get_project_local_dir(self) -> str:
    return self.get_config().project_local_dir

  def get_project_local_bin_dir(self) -> str:
    return self.get_config().project_local_bin_dir

  def get_pyproject_toml(self, create: Optional[bool]=False) -> PyprojectToml:
    if create is None:
      create = False
    if self._pyproject_toml is None:
      self._pyproject_toml = PyprojectToml(project_dir=self.get_project_root_dir(), create=create)
    return self._pyproject_toml

  @property
  def cwd(self) -> str:
    return self.cli.cwd
