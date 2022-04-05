#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Functions to wait for S3 objects"""

from typing import Optional, Awaitable

from pulumi import Input, Output

import boto3.session
import botocore.client
import botocore.errorfactory
import time
import json
from xpulumi.exceptions import XPulumiError

from xpulumi.internal_types import Jsonable

from ..s3_object_waiter import (
    sync_wait_s3_object,
    sync_wait_and_get_s3_object,
    async_wait_s3_object,
    async_wait_and_get_s3_object,
    DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS,
    _normalize_bucket_key,
  )


def wait_s3_object(
      uri: Input[Optional[str]]=None,
      bucket: Input[Optional[str]]=None,
      key: Input[Optional[str]]=None,
      region_name: Input[Optional[str]]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> Output[bool]:
  ow: Output[Awaitable[None]] = Output.all(
      uri,
      bucket,
      key,
      region_name,
      max_wait_seconds,
      poll_interval
    ).apply(
        lambda args: async_wait_s3_object(
            uri=args[0],
            bucket=args[1],
            key=args[2],
            region_name=args[3],
            max_wait_seconds=args[4],
            poll_interval=args[5]
          )
      )
  result: Output[bool] = Output.all(ow).apply(lambda args: True)
  return result

def wait_and_get_s3_object(
      uri: Input[Optional[str]]=None,
      bucket: Input[Optional[str]]=None,
      key: Input[Optional[str]]=None,
      region_name: Input[Optional[str]]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> Output[bytes]:
  ow: Output[Awaitable[bytes]] = Output.all(
      uri,
      bucket,
      key,
      region_name,
      max_wait_seconds,
      poll_interval
    ).apply(
        lambda args: async_wait_and_get_s3_object(
            uri=args[0],
            bucket=args[1],
            key=args[2],
            region_name=args[3],
            max_wait_seconds=args[4],
            poll_interval=args[5]
          )
      )
  result: Output[bytes] = Output.all(ow).apply(lambda args: args[0])
  return result

def wait_and_get_s3_object_str(
      uri: Input[Optional[str]]=None,
      bucket: Input[Optional[str]]=None,
      key: Input[Optional[str]]=None,
      region_name: Input[Optional[str]]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> Output[str]:
  bin = wait_and_get_s3_object(
      uri=uri,
      bucket=bucket,
      key=key,
      region_name=region_name,
      max_wait_seconds=max_wait_seconds,
      poll_interval=poll_interval
    )
  result = bin.apply(lambda x: x.decode('utf-8'))
  return result

def _load_s3_json(bin: bytes, uri: Optional[str], bucket: Optional[str], key: Optional[str]) -> Jsonable:
  try:
    s = bin.decode('utf-8')
    result: Jsonable = json.loads(s)
  except Exception as e:
    bucket, key = _normalize_bucket_key(uri=uri, bucket=bucket, key=key)
    raise XPulumiError(f"S3 object s3://{bucket}/{key} contains invalid JSON") from e
  return result

def wait_and_get_s3_json_object(
      uri: Input[Optional[str]]=None,
      bucket: Input[Optional[str]]=None,
      key: Input[Optional[str]]=None,
      region_name: Input[Optional[str]]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> Output[Jsonable]:
  bin = wait_and_get_s3_object(
      uri=uri,
      bucket=bucket,
      key=key,
      region_name=region_name,
      max_wait_seconds=max_wait_seconds,
      poll_interval=poll_interval
    )
  result: Output[Jsonable] = Output.all(bin, uri, bucket, key).apply(lambda args: _load_s3_json(*args))
  return result
