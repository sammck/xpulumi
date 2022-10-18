#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Temporary test function"""

from .internal_types import Jsonable

from secret_kv import open_kv_store

from .config import XPulumiConfig
from .base_context import XPulumiContextBase
from .exceptions import XPulumiError

def run_test() -> Jsonable:
  cfg = XPulumiConfig()
  ctx = cfg.create_context()
  result = dict(default_aws_profile=ctx.default_aws_profile)
  return result
