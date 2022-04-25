#!/usr/bin/env python3

from copy import deepcopy
from importlib.abc import ResourceReader
from typing import Optional, List, Dict, Union, cast

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

from .vpc import VpcEnv

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_invoke_options,
    get_aws_region_data,
    default_tags,
    get_availability_zones,
    get_aws_resource_options,
    long_stack,
    with_default_tags,
    long_xstack,
  )

port_descriptions: Dict[int, str] = {
    22: "SSH",
    80: "HTTP",
    443: "HTTPS",
    8080: "Alt HTTP",
    8443: "Alt HTTPS",
  }

class FrontEndSecurityGroup:
  resource_prefix: str = ''
  vpc: VpcEnv
  sg: ec2.SecurityGroup

  @property
  def sg_id(self) -> Output[str]:
    return self.sg.id

  @property
  def aws_region(self) -> str:
    return self.vpc.aws_region

  def __init__(
        self,
        vpc: VpcEnv,
        open_ports: Optional[List[Union[int, JsonableDict]]]=None,
        resource_prefix: Optional[str] = None,
        sg_id: Optional[Input[str]] = None,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    if sg_id is None:
      if open_ports is None:
        open_ports = [ 22, 80, 443 ]
      ingress: List[JsonableDict] = []
      for x in open_ports:
        if isinstance(x, int):
          x = dict(from_port=x, to_port=x)
        entry = deepcopy(x)
        if not 'cidr_blocks' in entry:
          entry['cidr_blocks'] = [ '0.0.0.0/0' ]
        from_port = cast(int, entry['from_port'])
        assert isinstance(from_port, int)
        if 'to_port' in entry:
          to_port = cast(int, entry['to_port'])
          assert isinstance(to_port, int)
        else:
          to_port = from_port
          entry['to_port'] = to_port
        if 'protocol' in entry:
          protocol = cast(str, entry['protocol'])
          assert isinstance(protocol, str)
        else:
          protocol = 'tcp'
          entry['protocol'] = protocol
        if protocol == 'tcp' and from_port == to_port and not 'description' in entry:
          desc = port_descriptions.get(from_port)
          if not desc is None:
            entry['description'] = desc
        ingress.append(entry)

      sg = ec2.SecurityGroup(
        f'{resource_prefix}front-end-sg',
        description=f'{long_stack} front-end security group. Public SSH, HTTP, and HTTPS',
        egress=[
          dict(
            cidr_blocks=[ '0.0.0.0/0' ],
            description="IPV4 ANY",
            protocol='tcp',
            from_port=1,
            to_port=65534
          ),
        ],
        ingress=ingress,
        # name=None,
        name_prefix=long_stack + '-',
        # revoke_rules_on_delete=None,
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-front-end-sg"),
        vpc_id=vpc.vpc_id,
        opts=get_aws_resource_options(self.aws_region),
      )
    else:
      sg = ec2.SecurityGroup.get(
          f'{resource_prefix}front-end-sg',
          id=sg_id,
          opts=get_aws_resource_options(self.aws_region)
        )
    self.sg = sg

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}front_end_sg_id', self.sg.id)
