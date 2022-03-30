#!/usr/bin/env python3

from base64 import b64encode
from copy import deepcopy
from typing import Optional, List, Union, Set, Tuple

import subprocess
import os
import json
import ipaddress
import yaml
import io
from io import BytesIO

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
  ebs,
  ecs,
  ecr,
  elasticloadbalancingv2 as elbv2,
  iam,
  cloudwatch,
  rds,
  kms,
  secretsmanager,
  AwaitableGetAmiResult,
)

from xpulumi.internal_types import JsonableDict

from .util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
  get_ami_arch_from_instance_type,
  future_func,
  yamlify_promise,
)

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_region_data,
    pconfig,
    default_tags,
    get_availability_zones,
    long_stack,
    aws_provider,
    aws_resource_options,
    aws_invoke_options,
    with_default_tags,
    long_xstack,
  )
from .. import XPulumiError
from .vpc import VpcEnv
from .security_group import FrontEndSecurityGroup
from .ec2_keypair import Ec2KeyPair
from .dns import DnsZone
from .user_data import (
    render_user_data_base64,
    UserDataConvertible
  )

@future_func
def get_ami_name_filter(ami_arch: str, ami_distro: str, ami_os_version: str) -> str:
  return f"ubuntu/images/hvm-ssd/ubuntu-{ami_distro}-{ami_os_version}-{ami_arch}-server-*"


'''
# create a cloud-config document to attach as user-data to the new ec2 instance.
# we create a sync function to generate the document when all needed outputs have values, and wrap it as a future that can consume outputs. 
def gen_frontend_cloud_config_obj(
      zone_name: str,
      region: str,
      ecr_domain: str,
      bootstrap_repo_name: str,
      bootstrap_repo_tag: str
    ) -> JsonableDict:
  docker_config_obj = {
      "credHelpers": {
          "public.ecr.aws": "ecr-login",
          ecr_domain: "ecr-login"
        }
    }
  full_repo_and_tag = f"{ecr_domain}/{bootstrap_repo_name}:{bootstrap_repo_tag}"
  docker_config = json.dumps(docker_config_obj, indent=1, sort_keys=True)
  config_obj = dict(
      repo_update = True,
      repo_upgrade = "all",
      fqdn = f"fe.{zone_name}",
      apt = dict(
          sources = {
            "docker.list": dict(
                source = "deb [arch=amd64] https://download.docker.com/linux/ubuntu $RELEASE stable",
                keyid = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
              ),
            },
        ),

      packages = [
          "jq",
          "awscli",
          "collectd",
          "ca-certificates",
          "curl",
          "gnupg",
          "lsb-release",
          "docker-ce",
          "docker-ce-cli",
          "amazon-ecr-credential-helper",
        ],

      runcmd = [
          [ "bash", "-c", f"mkdir -p /root/.docker && chmod 700 /root/.docker && echo '{docker_config}' > /root/.docker/config.json && chmod 600 /root/.docker/config.json" ],
          [ "docker", "pull", full_repo_and_tag ],
          [ "docker", "run", "--rm", "-v", "/:/host-rootfs", "--privileged", "--net=host", full_repo_and_tag ],
          [ "bash", "-c", 'echo "it works!"' ],
        ],
    )
  return config_obj

def gen_future_frontend_cloud_config_obj(
    zone_name: Union[str, Output[str]],
    region: Union[str, Output[str]],
    ecr_domain: Union[str, Output[str]],
    bootstrap_repo_name: Union[str, Output[str]],
    bootstrap_repo_tag: Union[str, Output[str]],
  ) -> Output[dict]:
  # "pulumi.Output.all(*future_args).apply(lambda args: sync_func(*args))"" is a pattern
  # provided by pulumi. It waits until all promises in future_args have been satisfied,
  # then invokes sync_func with the realized values of all the future_args as *args. Finally
  # it wraps the synchronous function as a promise and returns the new promise as the result.
  # this allows you to write synchronous code in pulumi that depends on future values, and
  # turn it into asynchronous code
  future_obj = Output.all(
        zone_name, region, ecr_domain, bootstrap_repo_name, bootstrap_repo_tag
    ).apply(lambda args: gen_frontend_cloud_config_obj(*args))
  return future_obj

future_frontend_cloud_config_obj = gen_future_frontend_cloud_config_obj(
    zone_name=zone_name,
    region=region,
    ecr_domain=ecr_domain,
    bootstrap_repo_name=front_end_bootstrap_repo_name,
    bootstrap_repo_tag=front_end_bootstrap_repo_tag,
  )

frontend_cloud_config = yamlify_promise(
    future_frontend_cloud_config_obj,
    indent=1,
    default_flow_style=None,
    width=10000,
    prefix_text="#cloud-config\n",
  )

'''

class Ec2Instance:
  # define a policy that allows EC2 to assume our roles for the purposes of creating EC2 instances
  DEFAULT_ASSUME_ROLE_POLICY_OBJ: JsonableDict = {
      "Version": "2012-10-17",
      "Statement": [
          {
              "Action": "sts:AssumeRole",
              "Principal": {
                "Service": "ec2.amazonaws.com",
              },
              "Effect": "Allow",
              "Sid": "",
            },
        ],
    }
  DEFAULT_ROLE_POLICY_OBJ: JsonableDict = {
      "Version": "2012-10-17",
      "Statement": [
          # Nondestructive EC2 queries
          {
              "Action": ["ec2:Describe*"],
              "Effect": "Allow",
              "Resource": "*",
            },
          # Read-only access to ECR, to fetch docker images
          {
              "Action": [
                  "ecr:GetAuthorizationToken",
                  "ecr:DescribeRepositories",
                  "ecr:BatchGetImage",
                  "ecr:BatchCheckLayerAvailability",
                  "ecr:GetDownloadUrlForLayer",
                ],
              "Effect": "Allow",
              "Resource": "*",
            },
        ],
    }
  DEFAULT_INSTANCE_TYPE = "t3.medium"
  DEFAULT_SYS_VOLUME_SIZE_GB = 40
  DEFAULT_DATA_VOLUME_SIZE_GB = 40
  AMI_OWNER_CANONICAL: str = "099720109477"  # The publisher of Ubunti AMI's
  DEFAULT_AMI_DISTRO = "focal"
  DEFAULT_AMI_OS_VERSION = "20.04"

  resource_prefix: str = ''
  assume_role_policy_obj: Optional[JsonableDict] = None
  role: iam.Role
  instance_profile: iam.InstanceProfile
  instance_type: str
  sys_volume_size_gb: int
  data_volume_size_gb: int
  keypair: Ec2KeyPair
  instance_dependencies: List[Output]
  cloudwatch_agent_attached_policy: iam.RolePolicyAttachment
  ssm_attached_policy: iam.RolePolicyAttachment
  role_policy_obj: Optional[JsonableDict] = None
  role_policy: iam.Policy
  attached_policy: iam.RolePolicyAttachment
  ami_arch: str
  ami_owner: str
  ami_distro: str
  ami_os_version: str
  ami_name_filter: Output[str]
  ami: AwaitableGetAmiResult
  eip: Optional[ec2.Eip] = None
  use_elastic_ip: bool
  parent_dns_zone: Optional[DnsZone] = None
  dns_names: List[str]
  primary_dns_name: Optional[str] = None
  description: str
  instance_name: str
  dns_records: List[route53.Record]
  vpc: VpcEnv
  sg: FrontEndSecurityGroup
  ebs_data_volume: Optional[ebs.Volume] = None
  ec2_instance: ec2.Instance
  eip_association: Optional[ec2.EipAssociation] = None
  data_volume_attachment: Optional[ec2.VolumeAttachment] = None
  user_data_text: Input[Optional[str]] = None
  user_data: UserDataConvertible = None

  def __init__(
        self,
        vpc: VpcEnv,
        resource_prefix: Optional[str] = None,
        use_config: bool = True,
        cfg_prefix: Optional[str]=None,
        assume_role_policy_obj: Optional[JsonableDict]=None,
        description: Optional[str]=None,
        instance_type: Optional[str]=None,
        sys_volume_size_gb: Optional[int]=None,
        data_volume_size_gb: Optional[int]=None,
        public_key: Optional[str]=None,
        public_key_file: Optional[str]=None,
        role_policy_obj: Optional[JsonableDict]=None,
        ami_os_version: Optional[str] = None,
        use_elastic_ip: Optional[bool] = None,
        parent_dns_zone: Optional[Union[str, DnsZone]] = None,
        dns_names: Optional[Union[str, List[str]]] = None,
        dns_subnames: Optional[Union[str, List[str]]] = None,
        register_dns: Optional[bool] = None,
        instance_name: Optional[str] = None,
        open_ports: Optional[List[Union[int, JsonableDict]]]=None,
        user_data: UserDataConvertible = None,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix

    self.vpc = vpc

    ami_distro = self.DEFAULT_AMI_DISTRO
    ami_owner = self.AMI_OWNER_CANONICAL

    self.instance_dependencies = []

    if use_config:
      if instance_type is None:
        instance_type = pconfig.get(f'{cfg_prefix}ec2_instance_type')
      if sys_volume_size_gb is None:
        sys_volume_size_gb = pconfig.get_int(f'{cfg_prefix}ec2_sys_volume_size')
      if data_volume_size_gb is None:
        data_volume_size_gb = pconfig.get_int(f'{cfg_prefix}ec2_data_volume_size')
      if ami_os_version is None:
        ami_os_version = pconfig.get(f'{cfg_prefix}ec2_ami_os_version')
      if use_elastic_ip is None:
        use_elastic_ip = pconfig.get_bool(f'{cfg_prefix}ec2_use_elastic_ip')
      if parent_dns_zone is None:
        parent_dns_zone = pconfig.get(f'{cfg_prefix}ec2_parent_dns_zone')
      if dns_names is None and dns_subnames is None:
        dns_names = pconfig.get(f'{cfg_prefix}ec2_dns_names')
        dns_subnames = pconfig.get(f'{cfg_prefix}ec2_dns_subnames')
      if register_dns is None:
        register_dns = pconfig.get_bool(f'{cfg_prefix}ec2_register_dns')
      if instance_name is None:
        instance_name = pconfig.get(f'{cfg_prefix}ec2_instance_name')
      if open_ports is None:
        open_ports = pconfig.get(f'{cfg_prefix}ec2_open_ports')
        if isinstance(open_ports, str):
          open_ports = json.loads(open_ports)
      if user_data is None:
        user_data = pconfig.get(f'{cfg_prefix}ec2_user_data')

    if instance_type is None:
      instance_type = self.DEFAULT_INSTANCE_TYPE

    if sys_volume_size_gb is None:
      sys_volume_size_gb = self.DEFAULT_SYS_VOLUME_SIZE_GB

    if data_volume_size_gb is None:
      data_volume_size_gb = self.DEFAULT_DATA_VOLUME_SIZE_GB
      if data_volume_size_gb is None:
        data_volume_size_gb = 0

    if ami_os_version is None:
      ami_os_version = self.DEFAULT_AMI_OS_VERSION

    if use_elastic_ip is None:
      use_elastic_ip = True

    if isinstance(parent_dns_zone, str):
      parent_dns_zone = DnsZone(parent_dns_zone, resource_prefix=f'{resource_prefix}parent-', create=False)

    if register_dns is None:
      register_dns = not parent_dns_zone is None

    if register_dns and not use_elastic_ip:
      raise XPulumiError("use_elastic_ip is required if register_dns is specified")

    if dns_names is None:
      dns_names = []
    elif not isinstance(dns_names, list):
      dns_names = [ dns_names ]
    if dns_subnames is None:
      dns_subnames = []
    elif not isinstance(dns_subnames, list):
      dns_subnames = [ dns_subnames ]

    all_dns_names: Set[str] = set()
    ordered_dns_names: List[str] = []
    for fq_name in dns_names:
      if not fq_name in all_dns_names:
        all_dns_names.add(fq_name)
        ordered_dns_names.append(fq_name)
    if len(dns_subnames) > 0:
      if parent_dns_zone is None:
        raise XPulumiError("parent_dns_zone must be provided if dns_subnames are provided")
      for sn in dns_subnames:
        if sn == '' or sn == '.':
          fq_name = parent_dns_zone.zone_name
        else:
          fq_name = sn + '.' + parent_dns_zone.zone_name
        if not fq_name in all_dns_names:
          all_dns_names.add(fq_name)
          ordered_dns_names.append(fq_name)

    primary_dns_name: Optional[str] = None
    if not parent_dns_zone is None:
      for dn in ordered_dns_names:
        if dn == parent_dns_zone.zone_name:
          primary_dns_name = dn
        elif not dn.endswith('.' + parent_dns_zone.zone_name):
          raise XPulumiError(f"Requested DNS name {dn} is not a child of parent zone {parent_dns_zone.zone_name}")
    if primary_dns_name is None and len(ordered_dns_names) > 0:
      primary_dns_name = ordered_dns_names[0]

    if len(ordered_dns_names) > 0:
      # put the primary name first
      ordered_dns_names.remove(primary_dns_name)
      ordered_dns_names = [ primary_dns_name ] + ordered_dns_names
      
    if description is None:
      description = f"EC2 instance ({resource_prefix}) in Pulumi stack {long_stack}"

    if instance_name is None:
      instance_name = primary_dns_name
      if instance_name is None:
        instance_name = f"{resource_prefix}ec2-instance"

    self.instance_type = instance_type
    self.sys_volume_size_gb = sys_volume_size_gb
    self.data_volume_size_gb = data_volume_size_gb
    self.ami_os_version = ami_os_version
    self.ami_owner = ami_owner
    self.ami_distro = ami_distro
    self.use_elastic_ip = use_elastic_ip
    self.dns_names = ordered_dns_names
    self.primary_dns_name = primary_dns_name
    self.description = description
    self.instance_name = instance_name
    self.parent_dns_zone = parent_dns_zone
    self.user_data = user_data

    # define an assume role policy that allows EC2 to assume a role for our instance
    if assume_role_policy_obj is None:
      assume_role_policy_obj = self.DEFAULT_ASSUME_ROLE_POLICY_OBJ
    assume_role_policy_obj = deepcopy(assume_role_policy_obj)
    self.assume_role_policy_obj = assume_role_policy_obj

    # define a role policy that allows our instance to access needed AWS resources
    if role_policy_obj is None:
      role_policy_obj = self.DEFAULT_ROLE_POLICY_OBJ
    role_policy_obj = deepcopy(role_policy_obj)
    self.role_policy_obj = role_policy_obj

    # ---- start creating resources

    self.role_policy = iam.Policy(
        f"{resource_prefix}ec2-role-policy",
        path="/",
        description=f"Custom role policy for {description}",
        policy=json.dumps(self.role_policy_obj, sort_keys=True),
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-role"),
        opts=aws_resource_options,
      )

    # Create an IAM role for our EC2 instance to run in.
    self.role = iam.Role(
        f'{resource_prefix}ec2-instance-role',
        assume_role_policy=json.dumps(self.assume_role_policy_obj, sort_keys=True),
        description=f"Role for {description}",
        # force_detach_policies=None,
        max_session_duration=12*TTL_HOUR,
        name=f'{resource_prefix}{long_xstack.replace(":", "-")}-ec2-role',
        # name_prefix=None,
        path=f'/pstack={long_stack}/',
        # permissions_boundary=None,
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-role"),
        opts=aws_resource_options,
      )

    # Attach policy to the EC2 instance role to allow cloudwatch monitoring.
    self.cloudwatch_agent_attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy-cloudwatch-agent',
        role=self.role.name,
        policy_arn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.cloudwatch_agent_attached_policy)

    # Attach policy to the EC2 instance role to allow SSM management.
    self.ssm_attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy-ssm-managed',
        role=self.role.name,
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.ssm_attached_policy)

    # Attach our custom policy to the EC2 instance role.
    self.attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy',
        role=self.role.name,
        policy_arn=self.role_policy.arn,
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.attached_policy)

    # create an instance profile for our instance that allows it to assume the above role
    self.instance_profile = iam.InstanceProfile(
        f"{resource_prefix}ec2-instance-profile",
        role=self.role.name,
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-instance"),
        opts=aws_resource_options,
      )

    self.keypair = Ec2KeyPair(
      resource_prefix=resource_prefix,
      use_config=use_config,
      cfg_prefix=cfg_prefix,
      public_key=public_key,
      public_key_file=public_key_file,
    )

    self.ami_arch = get_ami_arch_from_instance_type(self.instance_type)
    self.ami_name_filter = get_ami_name_filter(self.ami_arch, self.ami_distro, self.ami_os_version)

    # Find the most recent AMI that matches
    self.ami = ec2.get_ami(
        most_recent=True,
        filters=[
            ec2.GetAmiFilterArgs(
                name="name",
                values=[ self.ami_name_filter ],
              ),
            ec2.GetAmiFilterArgs(
                name="virtualization-type",
                values=[ "hvm" ],
              ),
          ],
        owners=[self.ami_owner],
        opts=aws_invoke_options,
      )

    if use_elastic_ip:
      # Create an elastic IP address for the instance. This allows the IP address to remain stable even if the instance is
      # shut down and restarted, or even destroyed and recreated. Prevents DNS entries and caches from becoming invalid.
      self.eip = ec2.Eip(
          f'{resource_prefix}ec2-instance-eip',
          vpc=True,
          tags=with_default_tags(Name=instance_name),
          opts=aws_resource_options,
        )
      self.instance_dependencies.append(self.eip)

    dns_records: List[route53.Record] = []
    if register_dns and len(self.dns_names) > 0:
      cname_target: Optional[str] = None
      for i, dn in enumerate(self.dns_names):
        if cname_target is None or dn == self.parent_dns_zone.zone_name:
          dns_record = route53.Record(
              f'{resource_prefix}ec2-instance-dns-record-{dn}',
              # aliases=None,
              # allow_overwrite=None, 
              # failover_routing_policies=None, 
              # geolocation_routing_policies=None, 
              # health_check_id=None, 
              # latency_routing_policies=None, 
              # multivalue_answer_routing_policy=None, 
              name=dn,
              records=[ self.eip.public_ip ],
              # set_identifier=None, 
              ttl=TTL_MINUTE * 10,
              type='A',
              # weighted_routing_policies=None,
              zone_id=self.parent_dns_zone.zone_id,
              opts=aws_resource_options,
            )
          if cname_target is None:
            cname_target = dn
        else:
          dns_record = route53.Record(
              f'{resource_prefix}ec2-instance-dns-record-{dn}',
              # opts=None,
              # aliases=None, 
              # allow_overwrite=None, 
              # failover_routing_policies=None, 
              # geolocation_routing_policies=None, 
              # health_check_id=None, 
              # latency_routing_policies=None, 
              # multivalue_answer_routing_policy=None, 
              name=dn, 
              records=[ cname_target ],
              # set_identifier=None, 
              ttl=TTL_MINUTE * 10,
              type='CNAME',
              # weighted_routing_policies=None,
              zone_id=self.parent_dns_zone.zone_id,
              opts=aws_resource_options,
            )
        dns_records.append(dns_record)
        self.instance_dependencies.append(dns_record)
    self.dns_records = dns_records

    # create a security group for the new instance
    self.sg = FrontEndSecurityGroup(
      vpc = self.vpc,
      open_ports=open_ports,
      resource_prefix=resource_prefix,
    )

    if self.data_volume_size_gb > 0:
      # Create a data volume as a separate resource so it can remain stable while EC2 instance
      # is replaced.
      self.ebs_data_volume = ebs.Volume(
          f'{resource_prefix}ec2-instance-data-volume',
          availability_zone=self.vpc.azs[0],  # place in the first AZ
          size=self.data_volume_size_gb,
          tags=with_default_tags(Name=f"{self.instance_name}-data"),
          opts=aws_resource_options,
        )
      self.instance_dependencies.append(self.ebs_data_volume)

    rendered_user_data = render_user_data_base64(user_data)
    # Create an EC2 instance
    self.ec2_instance = ec2.Instance(
        f'{resource_prefix}ec2-instance',
        ami=self.ami.id,
        instance_type=self.instance_type,
        iam_instance_profile=self.instance_profile.name,
        key_name=self.keypair.keypair.key_name,
        associate_public_ip_address=self.eip is None,   # deferred until EIP is assigned. Sadly no way to do this atomically
        subnet_id=self.vpc.public_subnet_ids[0],  # Place in the first AZ
        vpc_security_group_ids=[ self.sg.sg.id ],
        root_block_device=dict(volume_size=self.sys_volume_size_gb),
        user_data_base64=rendered_user_data,
        tags=with_default_tags(Name=self.instance_name),
        volume_tags=with_default_tags(Name=f"{self.instance_name}-sys"),
        opts=ResourceOptions(provider=aws_provider, depends_on=self.instance_dependencies, delete_before_replace=True)
      )

    # associate the EIP with the instance
    if not self.eip is None:
      self.eip_association = ec2.EipAssociation(
          f"{resource_prefix}ec2-eip-assoc",
          instance_id=self.ec2_instance.id,
          allocation_id=self.eip.id,
          opts=aws_resource_options,
        )

    if not self.ebs_data_volume is None:
      # attach the ebs data volume volume to the instance. Unfortunately no way to do this at launch time
      data_volume_attachment = ec2.VolumeAttachment(
          f"{resource_prefix}ec2-data-volume-attachment",
          device_name="/dev/sdf",
          volume_id = self.ebs_data_volume.id,
          instance_id = self.ec2_instance.id,
          stop_instance_before_detaching=True,
          opts=ResourceOptions(provider=aws_provider, delete_before_replace=True)
        )
      self.data_volume_attachment = data_volume_attachment


  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    # pulumi.export(f'{export_prefix}ec2_instance_id', self.ec2_instance.id)
    pulumi.export(f'{export_prefix}dns_names', self.dns_names)
    if not self.eip is None:
      pulumi.export(f'{export_prefix}public_ip', self.eip.public_ip)
