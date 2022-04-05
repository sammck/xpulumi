#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Functions to wait for S3 objects"""

from typing import Optional, Tuple

import boto3.session
import botocore.client
import botocore.errorfactory
import time
import asyncio
import concurrent.futures

from xpulumi.exceptions import XPulumiError

from .util import run_once, split_s3_uri


@run_once
def get_executor() -> concurrent.futures.ThreadPoolExecutor:
  executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
  return executor

DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS: float = 10.0*60
DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS: int = 5.0

def _normalize_bucket_key(
      uri: Optional[str]=None,
      bucket: Optional[str]=None,
      key: Optional[str]=None,
    ) -> Tuple[str, str]:
  if not uri is None:
    bucket, key = split_s3_uri(uri)
  if bucket is None or bucket == '':
    raise XPulumiError("An S3 bucket name or S3 object URI is required")
  if key is None or key == '':
    raise XPulumiError("An S3 key name or S3 object URI is required")
  return bucket, key

def sync_wait_s3_object(
      uri: Optional[str]=None,
      bucket: Optional[str]=None,
      key: Optional[str]=None,
      sess: Optional[boto3.session.Session]=None,
      region_name: Optional[str]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> None:
  """Wait for an S3 object to exist

  Args:
      uri (str, optional): The bucket name and key as an S3 URI
      bucket (str, optional): The name of the S3 bucket
      key (str, optional): The S3 key of the S3 object
      sess (Optional[boto3.session.Session], optional): Optional precreated boto3 session. Defaults to None.
      region_name (Optional[str], optional): If sess is None, the region to use for a new session. Defaults to None.
      max_wait_seconds (float, optional): The maximum number of seconds to wait before raising
                                           TimeoutError. if negative, will wait forever.
                                           Defaults to DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS.
      poll_interval (float, optional): The number of seconds between S3 queries.
                                           Defaults to DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS.
  Raises:
      TimeoutError: The S# object did not appear before max_wait_seconds elapsed
  """
  bucket, key = _normalize_bucket_key(uri, bucket, key)
  start_time = time.monotonic()
  if sess is None:
    sess = boto3.session.Session(region_name=region_name)
  bcs3 = sess.client('s3')
  while True:
    try:
      bcs3.head_object(Bucket=bucket, Key=key)
      return
    except botocore.errorfactory.ClientError:
      pass
    if max_wait_seconds < 0:
      wait_seconds = poll_interval
    else:
      elapsed = time.monotonic() - start_time
      if elapsed > max_wait_seconds:
        raise TimeoutError(f"Timed out waiting for s3://{bucket}/{key} to exist")
      wait_seconds = min(poll_interval, max_wait_seconds - elapsed)
    if wait_seconds > 0:
      time.sleep(wait_seconds)

async def async_wait_s3_object(
      uri: Optional[str]=None,
      bucket: Optional[str]=None,
      key: Optional[str]=None,
      sess: Optional[boto3.session.Session]=None,
      region_name: Optional[str]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> None:
  bucket, key = _normalize_bucket_key(uri, bucket, key)
  start_time = time.monotonic()
  if sess is None:
    sess = boto3.session.Session(region_name=region_name)
  bcs3 = sess.client('s3')
  loop = asyncio.get_event_loop()
  executor = get_executor()
  while True:
    try:
      task = loop.run_in_executor(executor, lambda: bcs3.head_object(Bucket=bucket, Key=key))
      await task
      return
    except botocore.errorfactory.ClientError:
      pass
    if max_wait_seconds < 0:
      wait_seconds = poll_interval
    else:
      elapsed = time.monotonic() - start_time
      if elapsed > max_wait_seconds:
        raise TimeoutError(f"Timed out waiting for s3://{bucket}/{key} to exist")
      wait_seconds = min(poll_interval, max_wait_seconds - elapsed)
    if wait_seconds > 0:
      await asyncio.sleep(wait_seconds)

def sync_wait_and_get_s3_object(
      uri: Optional[str]=None,
      bucket: Optional[str]=None,
      key: Optional[str]=None,
      sess: Optional[boto3.session.Session]=None,
      region_name: Optional[str]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> bytes:
  """Wait for an S3 object to exist, and return its content

  Args:
      bucket (str): The name of the S3 bucket
      key (str): The S3 key of the S3 object
      sess (Optional[boto3.session.Session], optional): Optional precreated boto3 session. Defaults to None.
      region_name (Optional[str], optional): If sess is None, the region to use for a new session. Defaults to None.
      max_wait_seconds (float, optional): The maximum number of seconds to wait before raising
                                           TimeoutError. if negative, will wait forever.
                                           Defaults to DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS.
      poll_interval (float, optional): The number of seconds between S3 queries.
                                           Defaults to DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS.

  Returns:
      bytes:  The content of the S3 object

  Raises:
      TimeoutError: The S# object did not appear before max_wait_seconds elapsed
  """
  bucket, key = _normalize_bucket_key(uri, bucket, key)
  if sess is None:
    sess = boto3.session.Session(region_name=region_name)
  bcs3 = sess.client('s3')
  sync_wait_s3_object(
      bucket=bucket,
      key=key,
      sess=sess,
      region_name=region_name,
      max_wait_seconds=max_wait_seconds,
      poll_interval=poll_interval
      )
  resp = bcs3.get_object(Bucket=bucket, Key=key)
  result = resp['Body'].read()
  return result

async def async_wait_and_get_s3_object(
      uri: Optional[str]=None,
      bucket: Optional[str]=None,
      key: Optional[str]=None,
      sess: Optional[boto3.session.Session]=None,
      region_name: Optional[str]=None,
      max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS, # -1 for infinite wait
      poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    ) -> bytes:
  """Wait for an S3 object to exist, and return its content

  Args:
      bucket (str): The name of the S3 bucket
      key (str): The S3 key of the S3 object
      sess (Optional[boto3.session.Session], optional): Optional precreated boto3 session. Defaults to None.
      region_name (Optional[str], optional): If sess is None, the region to use for a new session. Defaults to None.
      max_wait_seconds (float, optional): The maximum number of seconds to wait before raising
                                           TimeoutError. if negative, will wait forever.
                                           Defaults to DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS.
      poll_interval (float, optional): The number of seconds between S3 queries.
                                           Defaults to DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS.

  Returns:
      bytes:  The content of the S3 object

  Raises:
      TimeoutError: The S# object did not appear before max_wait_seconds elapsed
  """
  bucket, key = _normalize_bucket_key(uri, bucket, key)
  if sess is None:
    sess = boto3.session.Session(region_name=region_name)
  bcs3 = sess.client('s3')
  await async_wait_s3_object(
      bucket=bucket,
      key=key,
      sess=sess,
      region_name=region_name,
      max_wait_seconds=max_wait_seconds,
      poll_interval=poll_interval
      )
  loop = asyncio.get_event_loop()
  executor = get_executor()
  resp = await loop.run_in_executor(executor, lambda: bcs3.get_object(Bucket=bucket, Key=key))
  result = await loop.run_in_executor(executor, lambda: resp['Body'].read())
  return result

