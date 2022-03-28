#!/usr/bin/env python3

from typing import Optional, List, Union

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

from .util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
  get_xpulumi_context,
)

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_region_data,
    pconfig,
    default_tags,
    get_availability_zones,
    long_stack,
    aws_resource_options,
    aws_invoke_options,
    with_default_tags,
    long_xstack,
  )
from .. import XPulumiError


# NOTE: The AWS keypair fingerprint can be calculated from the SSH public key with:
#
#  ssh-keygen -f ~/.ssh/id_rsa.pub -m 'PEM' -e | \
#     openssl rsa  -RSAPublicKey_in | \
#     openssl rsa --inform PEM -pubin -pubout -outform DER | \
#     openssl md5 -c
#

class Ec2KeyPair:
  resource_prefix: str = ''
  public_key: Optional[str] = None
  keypair: ec2.KeyPair

  @property
  def keypair_id(self) -> Output[str]:
    return self.keypair.id

  def __init__(
        self,
        resource_prefix: Optional[str] = None,
        use_config: bool=True,
        cfg_prefix: Optional[str]=None,
        public_key: Optional[str]=None,
        public_key_file: Optional[str]=None,
        keypair_id: Optional[str]=None,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    ctx = get_xpulumi_context()
    if use_config and public_key is None and public_key_file is None:
      public_key = pconfig.get(f'{cfg_prefix}ssh_public_key')
      public_key_file = pconfig.get(f'{cfg_prefix}ssh_public_key_file')
    if use_config and keypair_id is None:
      keypair_id = pconfig.get(f'{cfg_prefix}ssh_keypair_id')
    if keypair_id is None:
      if public_key is None and public_key_file is None and os.path.exists(os.path.expanduser("~/.ssh/id_rsa.pub")):
        public_key_file = "~/.ssh/id_rsa.pub"
      if not public_key is None:
        public_key = public_key.rstrip()
      if not public_key_file is None:
        public_key_file = ctx.abspath(os.path.expanduser(public_key_file))
        with open(public_key_file) as f:
          file_public_key = f.read().rstrip()
        if public_key is None:
          public_key = file_public_key
        elif public_key != file_public_key:
          raise XPulumiError(f"Conflicting publish SSH keys, passed by value and in file {public_key_file}")
      if public_key is None:
        raise XPulumiError("Unable to determine SSH public key--not passed and ~/.ssh/id_rsa.pub not present")
      self.public_key = public_key
      self.keypair = ec2.KeyPair(
          f'{resource_prefix}ssh-keypair',
          key_name_prefix=f'{long_stack}-',
          public_key=public_key,
          tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-keypair"),
          opts=aws_resource_options,
        )
    else:
      if not public_key is None or not public_key_file is None:
        raise XPulumiError("If keypair_id is provided, public_key and public_key_file must be None")
      self.keypair = ec2.KeyPair.get(
          f'{resource_prefix}ssh-keypair',
          id=keypair_id,
          opts=aws_resource_options,
        )

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}ssh_keypair_id', self.keypair.id)
