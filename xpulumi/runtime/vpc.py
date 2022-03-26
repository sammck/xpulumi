#!/usr/bin/env python3

from importlib.abc import ResourceReader
from typing import Optional, List

import subprocess
import os
import json
import ipaddress

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
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
  )

class VpcEnv:
  DEFAULT_CIDR: str = '10.77.0.0/16'
  DEFAULT_N_AZS: int = 3
  DEFAULT_N_POTENTIAL_SUBNETS: int = 16

  n_azs: int
  vpc_cidr: str
  #n_potential_subnets: int
  aws_region: str
  resource_prefix: str = ""

  azs: List[str]
  #vpc_ip_network: ipaddress.IPv4Network
  #max_n_subnet_id_bits: int
  #n_subnet_id_bits: int
  #vpc_potential_subnet_ip_networks: List[ipaddress.IPv4Network]
  #public_subnet_ip_networks: List[ipaddress.IPv4Network]
  #private_subnet_ip_networks: List[ipaddress.IPv4Network]
  public_subnet_cidrs: List[str]
  private_subnet_cidrs: List[str]
  vpc: ec2.Vpc
  public_subnets: List[ec2.Subnet]
  public_subnet_ids: List[Output[str]]
  private_subnets: List[ec2.Subnet]
  private_subnet_ids: List[Output[str]]
  subnets: List[ec2.Subnet]
  subnet_ids: List[Output[str]]
  internet_gateway: ec2.InternetGateway
  route_table: ec2.DefaultRouteTable
  route_table_associations: List[ec2.RouteTableAssociation]
  route_table_association_ids: List[Output[str]]

  @classmethod
  def load(
        cls,
        resource_prefix: Optional[str] = None,
        cfg_prefix: Optional[str]=None,
      ) -> 'VpcEnv':
    vpc = VpcEnv(resource_prefix=resource_prefix)
    vpc._load(cfg_prefix=cfg_prefix)
    return vpc

  @classmethod
  def create(
        cls,
        resource_prefix: Optional[str] = None,
        use_config: bool=True,
        cfg_prefix: Optional[str]=None,
        n_azs: Optional[int]=None,
        vpc_cidr: Optional[str]=None,
        n_potential_subnets: Optional[int]=None,
        aws_region: Optional[str]=None,
      ) -> 'VpcEnv':
    vpc = VpcEnv(resource_prefix=resource_prefix)
    vpc._create(
        use_config=use_config,
        cfg_prefix=cfg_prefix,
        n_azs=n_azs,
        vpc_cidr=vpc_cidr,
        n_potential_subnets=n_potential_subnets,
        aws_region=aws_region,
      )
    return vpc

  @classmethod
  def stack_import(
        cls,
        resource_prefix: Optional[str] = None,
        stack_name: Optional[str]=None,
        project_name: Optional[str]=None,
        import_prefix: Optional[str]=None
      ) -> 'VpcEnv':
    vpc = VpcEnv(resource_prefix=resource_prefix)
    vpc._stack_import(
        stack_name=stack_name,
        project_name=project_name,
        import_prefix=import_prefix,
      )
    return vpc

  def __init__(self, resource_prefix: Optional[str] = None):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    pass

  def _load(
        self,
        cfg_prefix: Optional[str]=None,
      ) -> None:
    if cfg_prefix is None:
      cfg_prefix = ''
    vpc_import_stack_name: Optional[str] = pconfig.get(f'{cfg_prefix}vpc_import_stack')
    vpc_import_project_name: Optional[str] = pconfig.get(f'{cfg_prefix}vpc_import_project')
    if not vpc_import_stack_name is None or not vpc_import_project_name is None:
      vpc_import_prefix: Optional[str] = pconfig.get(f'{cfg_prefix}vpc_import_prefix')
      self._stack_import(stack_name=vpc_import_stack_name, project_name=vpc_import_project_name, import_prefix=vpc_import_prefix)
    else:
      self._create(use_config=True)

  def _create(
        self,
        use_config: bool=True,
        cfg_prefix: Optional[str]=None,
        n_azs: Optional[int]=None,
        vpc_cidr: Optional[str]=None,
        n_potential_subnets: Optional[int]=None,
        aws_region: Optional[str]=None,
      ) -> None:
    if cfg_prefix is None:
      cfg_prefix = ''
    resource_prefix = self.resource_prefix

    if use_config:
      if n_azs is None:
        n_azs = pconfig.get_int(f'{cfg_prefix}vpc_n_azs') # The number of AZs that we will provision our vpc in
      if vpc_cidr is None:
        vpc_cidr = pconfig.get(f'{cfg_prefix}vpc_cidr')
      if n_potential_subnets is None:
        n_potential_subnets = pconfig.get_int(f'{cfg_prefix}vpc_n_potential_subnets')
      if aws_region is None:
        aws_region = pconfig.get(f'{cfg_prefix}vpc_aws_region')

    rd = get_aws_region_data(aws_region)
    aws_region = rd.aws_region
    self.aws_region = aws_region
    ro = rd.resource_options

    n_azs = default_val(n_azs, self.DEFAULT_N_AZS) # The number of AZs that we will provision our vpc in
    vpc_cidr = default_val(vpc_cidr, self.DEFAULT_CIDR)
    n_potential_subnets = default_val(n_potential_subnets, self.DEFAULT_N_POTENTIAL_SUBNETS)

    self.n_azs = n_azs
    self.vpc_cidr = vpc_cidr

    azs = get_availability_zones(aws_region)[:n_azs]
    self.azs = azs
    vpc_ip_network = ipaddress.ip_network(vpc_cidr)
    #self.vpc_ip_network = vpc_ip_network
    max_n_subnet_id_bits = 32 - vpc_ip_network.prefixlen
    #self.max_n_subnet_id_bits = max_n_subnet_id_bits
    if n_potential_subnets < 8 or n_potential_subnets > (1 << 31) or (n_potential_subnets & (n_potential_subnets - 1)) != 0:
      raise RuntimeError("Config value n_potential_subnets must be a power of 2 >= 8: %d" % n_potential_subnets)
    #self.n_potential_subnets = n_potential_subnets
    n_subnet_id_bits = 0
    x = n_potential_subnets
    while x > 1:
      x //= 2
      n_subnet_id_bits += 1
    if n_subnet_id_bits > max_n_subnet_id_bits:
      raise RuntimeError("Config value n_potential_subnets is greater than maximum allowed (%d) by vpc CIDR %s: %d" % (1 << max_n_subnet_id_bits, vpc_cidr, n_potential_subnets))
    #self.n_subnet_id_bits = n_subnet_id_bits
    vpc_potential_subnet_ip_networks = list(vpc_ip_network.subnets(prefixlen_diff=n_subnet_id_bits))
    #self.vpc_potential_subnet_ip_networks = vpc_potential_subnet_ip_networks
    public_subnet_ip_networks = vpc_potential_subnet_ip_networks[:n_azs]
    #self.public_subnet_ip_networks = public_subnet_ip_networks
    private_subnet_ip_networks = vpc_potential_subnet_ip_networks[n_potential_subnets//2:n_potential_subnets//2+n_azs]
    #self.private_subnet_ip_networks = private_subnet_ip_networks

    public_subnet_cidrs = [str(x) for x in public_subnet_ip_networks]
    self.public_subnet_cidrs = public_subnet_cidrs
    private_subnet_cidrs = [str(x) for x in private_subnet_ip_networks]
    self.private_subnet_cidrs = private_subnet_cidrs

    # create a VPC that our whole stack and dependent services will run in
    vpc = ec2.Vpc(
      f'{resource_prefix}vpc',
      cidr_block=vpc_cidr,
      enable_dns_hostnames=True,
      enable_dns_support=True,
      tags=default_tags,
      opts=ro,
    )
    self.vpc = vpc

    # create public subnets in separate AZs
    public_subnets = []
    for i, cidr in enumerate(public_subnet_cidrs):
      public_subnets.append(
        ec2.Subnet(
          f'{resource_prefix}public-subnet-{i}',
          availability_zone=azs[i],
          vpc_id=vpc.id,
          cidr_block=cidr,
          map_public_ip_on_launch=True,
          tags=default_tags,
          opts=ro,
        )
      )
    self.public_subnets = public_subnets

    public_subnet_ids = [  x.id for x in public_subnets ]
    self.public_subnet_ids = public_subnet_ids

    # create private subnets in separate AZs.
    # TODO: currently these are the same as public subnets. We can change
    # that with a NAT gateway, no-assign public IP, and network ACLs.
    private_subnets = []
    for i, cidr in enumerate(private_subnet_cidrs):
      private_subnets.append(
        ec2.Subnet(
          f'{resource_prefix}private-subnet-{i}',
          availability_zone=azs[i],
          vpc_id=vpc.id,
          cidr_block=cidr,
          map_public_ip_on_launch=True,   # review: probably want to use NAT gateway for private subnets...?
          tags=default_tags,
          opts=ro,
        )
      )
    self.private_subnets = private_subnets
    private_subnet_ids = [ x.id for x in private_subnets ]
    self.private_subnet_ids = private_subnet_ids

    # convenient list of all subnets, public and private
    subnets = public_subnets + private_subnets
    self.subnets = subnets
    subnet_ids = [ x.id for x in subnets ]
    self.subnet_ids = subnet_ids

    # Create an internet gateway to route internet traffic to/from public IPs attached to the VPC
    internet_gateway = ec2.InternetGateway(
        f'{resource_prefix}vpc-gateway',
        tags=default_tags,
        vpc_id=vpc.id,
        opts=ro
      )
    self.internet_gateway = internet_gateway

    # Create a default route table for the VPC that routes everything inside the VPC CIDR locally,
    # and everything else to the internet through the internet gateway
    # TODO: provide direct VPC routing to AWS services
    route_table = ec2.DefaultRouteTable(
      f'{resource_prefix}route-table',
      default_route_table_id=vpc.default_route_table_id,
      routes=[
        dict(cidr_block="0.0.0.0/0", gateway_id=internet_gateway.id)
      ],
      tags=default_tags,
      opts=ro,
    )
    self.route_table = route_table

    # Attach all subnets to our default route table
    route_table_associations = []
    for i, subnet in enumerate(subnets):
      route_table_associations.append(
        ec2.RouteTableAssociation(
          f'{resource_prefix}default-route-table-association-{i}',
          route_table_id=route_table.id,
          subnet_id=subnet.id,
          opts=ro,
        )
      )
    self.route_table_associations = route_table_associations
    route_table_association_ids = [  x.id for x in route_table_associations ]
    self.route_table_association_ids = route_table_association_ids

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}vpc_aws_region', self.aws_region)
    pulumi.export(f'{export_prefix}vpc_id', self.vpc.id)
    pulumi.export(f'{export_prefix}vpc_cidr', self.vpc_cidr)
    pulumi.export(f'{export_prefix}vpc_azs', self.azs)
    pulumi.export(f'{export_prefix}public_subnet_cidrs', self.public_subnet_cidrs)
    pulumi.export(f'{export_prefix}private_subnet_cidrs', self.private_subnet_cidrs)
    pulumi.export(f'{export_prefix}public_subnet_ids', self.public_subnet_ids)
    pulumi.export(f'{export_prefix}private_subnet_ids', self.private_subnet_ids)
    pulumi.export(f'{export_prefix}internet_gateway_id', self.internet_gateway.id)
    pulumi.export(f'{export_prefix}route_table_id', self.route_table.id)
    pulumi.export(f'{export_prefix}route_table_association_ids', self.route_table_association_ids)

  def _stack_import(
        self,
        stack_name: Optional[str]=None,
        project_name: Optional[str]=None,
        import_prefix: Optional[str]=None
      ) -> None:
    if  import_prefix is None:
       import_prefix = ''
    resource_prefix = self.resource_prefix

    outputs = SyncStackOutputs(stack_name=stack_name, project_name=project_name)
    aws_region: str = outputs[f'{ import_prefix}vpc_aws_region']
    vpc_id: str = outputs[f'{ import_prefix}vpc_id']
    self.public_subnet_ids = outputs[f'{ import_prefix}public_subnet_ids']
    self.private_subnet_ids = outputs[f'{ import_prefix}private_subnet_ids']
    internet_gateway_id: str = outputs[f'{ import_prefix}internet_gateway_id']
    route_table_id: str = outputs[f'{ import_prefix}route_table_id']
    self.route_table_association_ids = outputs[f'{ import_prefix}route_table_association_ids']
    self.vpc_cidr = outputs[f'{ import_prefix}vpc_cidr']
    self.azs = outputs[f'{ import_prefix}vpc_azs']
    self.public_subnet_cidrs = outputs[f'{ import_prefix}public_subnet_cidrs']
    self.private_subnet_cidrs = outputs[f'{ import_prefix}private_subnet_cidrs']
    self.public_subnet_ids = outputs[f'{ import_prefix}public_subnet_ids']
    self.private_subnet_ids = outputs[f'{ import_prefix}private_subnet_ids']
    self.route_table_association_ids = outputs[f'{ import_prefix}route_table_association_ids']

    rd = get_aws_region_data(aws_region)
    aws_region = rd.aws_region
    self.aws_region = aws_region
    ro = rd.resource_options

    self.subnet_ids = self.public_subnet_ids + self.private_subnet_ids

    self.vpc = ec2.Vpc.get(
        f'{resource_prefix}vpc',
        id=vpc_id,
        opts=ro,
      )

    self.internet_gateway = ec2.InternetGateway.get(
        f'{resource_prefix}vpc-gateway',
        id=internet_gateway_id,
        opts=ro,
      )

    self.route_table = ec2.DefaultRouteTable.get(
        f'{resource_prefix}route-table',
        id=route_table_id,
        vpc_id=vpc_id,
        opts=ro,
      )

    self.public_subnets = []
    for i, id in enumerate(self.public_subnet_ids):
      subnet = ec2.Subnet.get(
          f'{resource_prefix}public-subnet-{i}',
          id=id,
          opts=ro,
        )
      self.public_subnets.append(subnet)

    self.private_subnets = []
    for i, id in enumerate(self.private_subnet_ids):
      subnet = ec2.Subnet.get(
          f'{resource_prefix}private-subnet-{i}',
          id=id,
          opts=ro,
        )
      self.private_subnets.append(subnet)

    self.subnets = self.public_subnets + self.private_subnets

    self.route_table_associations = []
    for i, id in enumerate(self.route_table_association_ids):
      rta = ec2.RouteTableAssociation.get(
          f'{resource_prefix}default-route-table-association-{i}',
          id=id,
          route_table_id=route_table_id,
          opts=ro,
        )
      self.route_table_associations.append(rta)
