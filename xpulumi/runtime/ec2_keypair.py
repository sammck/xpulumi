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
from xpulumi.base_context import XPulumiContextBase

from xpulumi.context import XPulumiContext

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
  ctx: XPulumiContextBase
  _public_key: Optional[str] = None
  _public_key_file: Optional[str] = None
  _keypair: Optional[ec2.KeyPair] = None
  keypair_id: Optional[Input[str]] = None
  _committed: bool = False

  def __init__(
        self,
        resource_prefix: Optional[str] = None,
        use_config: bool=True,
        cfg_prefix: Optional[str]=None,
        public_key: Optional[str]=None,
        public_key_file: Optional[str]=None,
        keypair_id: Optional[Input[str]]=None,
        commit: bool=True,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    self.ctx = get_xpulumi_context()

    if use_config and public_key is None and public_key_file is None:
      public_key = pconfig.get(f'{cfg_prefix}ssh_public_key')
      public_key_file = pconfig.get(f'{cfg_prefix}ssh_public_key_file')
    if use_config and keypair_id is None:
      keypair_id = pconfig.get(f'{cfg_prefix}ssh_keypair_id')

    self.keypair_id = keypair_id
    if keypair_id is None:
      self.set_public_key(public_key=public_key, filename=public_key_file)

    if commit:
      self.commit()

  @property
  def keypair(self) -> ec2.KeyPair:
    if self._keypair is None:
      raise XPulumiError("Ec2KeyPair has not been committed")
    return self._keypair

  @property
  def public_key(self) -> Optional[str]:
    return self._public_key

  @public_key.setter
  def public_key(self, public_key: Optional[str]) -> None:
    if not public_key is None:
      public_key = public_key.rstrip()
    self._public_key_file = None
    self._public_key = public_key

  def set_public_key(self, public_key: Optional[str]=None, filename: Optional[str]=None) -> None:
    pathname = None if filename is None else self.ctx.abspath(os.path.expanduser(filename))
    if public_key is None and not pathname is None:
      with open(pathname) as f:
        public_key = f.read().rstrip()
    self._public_key_file = pathname
    self._public_key = public_key

  @property
  def public_key_file(self) -> Optional[str]:
    return self._public_key_file

  @public_key_file.setter
  def public_key_file(self, filename: Optional[str]) -> None:
    self.set_public_key(filename=filename)

  def commit(self) -> None:
    if not self._committed:
      resource_prefix = self.resource_prefix
      public_key = self.public_key
      keypair_id = self.keypair_id
      if keypair_id is None:
        if public_key is None:
          raise XPulumiError("Unable to determine SSH public key--not passed and ~/.ssh/id_rsa.pub not present")
        self._keypair = ec2.KeyPair(
            f'{resource_prefix}ssh-keypair',
            key_name_prefix=f'{long_stack}-',
            public_key=public_key,
            tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-keypair"),
            opts=aws_resource_options,
          )
        self.keypair_id = self.keypair.id
      else:
        self._keypair = ec2.KeyPair.get(
            f'{resource_prefix}ssh-keypair',
            id=keypair_id,
            opts=aws_resource_options,
          )
      self._committed = True

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    assert self._committed
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}ssh_keypair_id', self.keypair.id)
