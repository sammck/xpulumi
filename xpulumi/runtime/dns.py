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
  )
from .. import XPulumiError

def prepend_subzone(zone: Optional[Union[str, 'DnsZone']]=None, subzone: Optional[str]=None) -> str:
  if isinstance(zone, DnsZone):
    zone = zone.zone_name
  if zone is None:
    zone = ''
  while zone.startswith('.'):
    zone = zone[1:]
  if subzone is None:
    subzone = ''
  while subzone.endswith('.'):
    subzone = subzone[:-1]
  new_zone = subzone + '.' + zone
  while new_zone.startswith('.'):
    new_zone = new_zone[1:]
  while new_zone.endswith('.'):
    new_zone = new_zone[:-1]
  if new_zone == '':
    raise XPulumiError("Empty DNS zone name")
  return new_zone

class DnsZone:
  resource_prefix: str = ''
  parent_zone: Optional['DnsZone'] = None
  zone: route53.Zone
  zone_name: str

  @property
  def zone_id(self) -> Output[str]:
    return self.zone.id

  def __init__(
        self,
        subzone_name: str,
        resource_prefix: Optional[str] = None,
        parent_zone: Optional['DnsZone'] = None,
        create: bool = True,
        zone_id: Input[Optional[str]] = None,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    self.parent_zone = parent_zone
    zone_name = prepend_subzone(parent_zone, subzone_name)
    self.zone_name = zone_name
    if create:
      zone = route53.Zone(
          f'{resource_prefix}dns-zone',
          # opts=,
          comment='Public zone for pulumi stack %s' % long_stack,
          delegation_set_id=None,
          force_destroy=True,
          name=zone_name,
          tags=default_tags,
          # vpcs=None,
          opts=aws_resource_options
        )
      if not parent_zone is None:
        # Create an NS record in the parent zone that points to our zone's name servers.
        public_parent_zone_ns_record = route53.Record(
          f'{resource_prefix}dns-zone-parent-ns-record',
          # aliases=None, 
          # allow_overwrite=None, 
          # failover_routing_policies=None,
          # geolocation_routing_policies=None, 
          # health_check_id=None, 
          # latency_routing_policies=None, 
          # multivalue_answer_routing_policy=None, 
          name=zone_name, 
          records=zone.name_servers,
          # set_identifier=None, 
          ttl=TTL_MINUTE * 10,
          type='NS',
          # weighted_routing_policies=None,
          zone_id=parent_zone.zone_id,
          opts=aws_resource_options,
        )

    else:
      if zone_id is None:
        zone_info = route53.get_zone(name=zone_name, private_zone=False, opts=aws_invoke_options)
        zone_id = zone_info.id
      zone = route53.Zone.get(
          f'{resource_prefix}dns-zone',
          id=zone_id,
          opts=aws_resource_options,
        )
    self.zone = zone

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}dns_zone', self.zone_name)
    pulumi.export(f'{export_prefix}dns_zone_id', self.zone.id)
