#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Temporary test function"""

from .internal_types import Jsonable

from secret_kv import open_kv_store

from .base_context import XPulumiContextBase
from .backend import XPulumiBackend
from .project import XPulumiProject
from .exceptions import XPulumiError

def run_test() -> Jsonable:
  project = XPulumiProject('test-project')
  outputs = project.get_stack_outputs('dev')
  return outputs
