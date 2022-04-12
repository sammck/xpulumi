#!/usr/bin/env python3

from copy import deepcopy
from importlib.abc import ResourceReader
from typing import Optional, List, Dict, Union

import subprocess
import os
import json
import ipaddress

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
  Input,
)

from pulumi_aws import (
  ec2,
  route53,
  acm,
  cognito,
  ecs,
  ecr,
  elasticloadbalancingv2 as elbv2,
  iam,
  cloudwatch,
  rds,
  kms,
  secretsmanager
)
from secret_kv import JsonableDict

from .util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
)

from ..util import XPulumiError

from .common import (
    default_tags,
    aws_resource_options,
    long_subaccount_stack,
  )

class CloudWatch:
  resource_prefix: str = ''
  log_group: cloudwatch.LogGroup

  def __init__(
        self,
        resource_prefix: Optional[str] = None,
        create: Optional[bool] = None,
        log_group_id: Optional[Input[str]] = None,
        log_group_name: Optional[str] = None
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    if create is None:
      create = log_group_id is None
    if create:
      if log_group_name is None:
        log_group_name = f"{resource_prefix}{long_subaccount_stack}-log-group"
      # create a CloudWatch log group for all our logging needs
      log_group = cloudwatch.LogGroup(
          'cloudwatch_log_group',
          # kms_key_id=None,
          name=log_group_name,
          # name_prefix=None,
          retention_in_days=30,
          tags=default_tags,
          opts=aws_resource_options,
        )
    else:
      if log_group_id is None:
        raise XPulumiError("log_group_id is required if create==False")
      log_group = cloudwatch.LogGroup.get(
          'cloudwatch_log_group',
          id=log_group_id,
          opts=aws_resource_options,
        )
    self.log_group = log_group

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}cloudwatch_log_group', self.log_group.name)
