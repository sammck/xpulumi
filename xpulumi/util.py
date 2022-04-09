# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Miscellaneous utility functions"""

from typing import Type, Any, Optional, Union, List, Tuple
from .internal_types import Jsonable

import json
import hashlib
import os
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
import pathlib
import subprocess
import threading
import tempfile

from .exceptions import XPulumiError

def split_s3_uri(uri: str) -> Tuple[str, str]:
  parts = urlparse(uri)
  if parts.scheme != 's3':
    raise XPulumiError(f"Invalid 's3:' URL: {uri}")
  bucket = parts.netloc
  key = parts.path
  while key.startswith('/'):
    key = key[1:]
  return bucket, key
