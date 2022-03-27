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

from .vpc import VpcEnv

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_region_data,
    pconfig,
    default_tags,
    get_availability_zones,
    aws_resource_options,
    long_stack
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

  def __init__(
        self,
        vpc: VpcEnv,
        open_ports: Optional[List[Union[int, JsonableDict]]]=None,
        resource_prefix: Optional[str] = None,
        create: bool = True,
        sg_id: Input[Optional[str]] = None,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    if create:
      if open_ports is None:
        open_ports = [ 22, 80, 443 ]
      ingress: List[JsonableDict] = []
      for x in open_ports:
        if isinstance(x, int):
          x = dict(from_port=x, to_port=x)
        entry = deepcopy(x)
        if not 'cidr_blocks' in entry:
          entry['cidr_blocks'] = [ '0.0.0.0/0' ]
        from_port: int = entry['from_port']
        if 'to_port' in entry:
          to_port: int = entry['to_port']
        else:
          to_port = from_port
          entry['to_port'] = to_port
        if 'protocol' in entry:
          protocol: str = entry['protocol']
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
        description='%s front-end security group. Public SSH, HTTP, and HTTPS' % long_stack,
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
        tags=default_tags,
        vpc_id=vpc.vpc_id,
        opts=aws_resource_options,
      )
    else:
      sg = ec2.SecurityGroup.get(f'{resource_prefix}front-end-sg', id=id)
    self.sg = sg

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}front_end_sg_id', self.sg.id)
