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
from .exceptions import XPulumiError

def run_test() -> Jsonable:
  ctx = XPulumiContextBase()
  skv = open_kv_store(config_path=ctx.get_cwd())
  try:
    v = skv.get_value("pulumi/passphrase")
    if v is None:
      raise XPulumiError("Please run \"secret-kv set pulumi/passphrase <passphrase-for-test-project>\"")
    passphrase = v.as_simple_jsonable()
  finally:
    skv.close()
  assert isinstance(passphrase, str)
  ctx.set_pulumi_secret_passphrase(passphrase)
  be = XPulumiBackend(ctx, 's3://492598163938-us-west-2-cloud-dev/cloud-dev/pulumi/prj', options={ 'includes_organization': True })
  outputs = be.get_stack_outputs('test-project', 'dev', bypass_pulumi=False)
  return outputs
