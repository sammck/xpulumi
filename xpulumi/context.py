# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract context for working with Pulumi.

Allows the application to provide certain requirements such as passphrases, defaults, etc.
on demand.

"""

from typing import Optional, cast, Dict
from .internal_types import Jsonable, JsonableDict

import os
from abc import ABC, abstractmethod
#from pulumi import automation as pauto
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
from copy import deepcopy
import boto3.session
from boto3.session import Session as BotoAwsSession
from botocore.session import Session as BotocoreSession

from project_init_tools import file_url_to_pathname
from .exceptions import XPulumiError

class XPulumiContext(ABC):
  @abstractmethod
  def get_aws_session(self, aws_account: Optional[str]=None, aws_region: Optional[str]=None) -> BotoAwsSession: ...

  @abstractmethod
  def get_environ(self) -> Dict[str, str]: ...

  @abstractmethod
  def get_pulumi_access_token(self, backend_url: Optional[str]=None) -> Optional[str]: ...

  @abstractmethod
  def get_pulumi_secret_passphrase(
        self,
        backend_url: str,
        organization: Optional[str]=None,
        project: Optional[str]=None,
        stack: Optional[str]=None,
        passphrase_id: Optional[str] = None,
        salt_state: Optional[str] =None,
    ) -> str: ...

  @abstractmethod
  def get_pulumi_home(self) -> str: ...

  @abstractmethod
  def get_pulumi_cli(self) -> str: ...

  @abstractmethod
  def get_pulumi_install_dir(self) -> str: ...

  @abstractmethod
  def get_cwd(self) -> str: ...

  @abstractmethod
  def get_default_cloud_subaccount(self) -> Optional[str]: ...
