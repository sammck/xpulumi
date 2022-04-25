# Copyright (c) 2022 Sam McKelvie
#
# See LICENSE file accompanying this package.
#

def load_stack(
      resource_prefix: str = '',
      cfg_prefix: str = '',
      export_prefix: str = '',
      aws_env_project_name: str = 'awsenv',
      aws_env_import_prefix: str = ''
    ):
  from typing import cast, Optional

  import json
  import shlex
  import os

  import pulumi
  from pulumi import Output, Input
  from xpulumi.exceptions import XPulumiError
  from xpulumi.internal_types import Jsonable, JsonableDict
  from xpulumi.runtime import (
      VpcEnv,
      DnsZone,
      Ec2Instance,
      require_stack_output,
      CloudWatch,
      enable_debugging,
      HashedPassword,
      jsonify_promise,
      S3FutureObject,
      SshCachedHostKey,
      dedent,
      future_dedent,
      concat_and_dedent,
      default_val,
    )
  from xpulumi.runtime.common import (
      get_aws_account_id,
      pulumi_project_name,
      stack_name,
      pconfig,
      config_property_info,
      long_stack,
      long_xstack,
    )

  from xpulumi.runtime.ebs_volume import EbsVolume

  # The xpulumi project name and stack name from which we will
  # import our AWS VPC network, subnets, availability zones, cloudwatch group,
  # main DNS zone, and other shared resources that can be used by multiple projects
  # We will use our own stack name, so that dev will pick up from dev, prod from prod etc.
  aws_env_stack_name = f"{aws_env_project_name}:{stack_name}"

  # Import our network configuration from the shared stack
  vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name, import_prefix=aws_env_import_prefix)
  vpc.stack_export(export_prefix=export_prefix)

  aws_region = vpc.aws_region

  # The number of gigabytes to allocate for the volume. This can be increased later
  # without destroying the volume
  volume_size_gb = cast(int, default_val(pconfig.get_int(
      f"{cfg_prefix}volume_size_gb",
      config_property_info(description="The size of the EBS data volume in gigabytes, devault=40"),
    ), 40))
  pulumi.export(f"{export_prefix}volume_size_gb", volume_size_gb)

  # The AWS availability zone within the vpc in which to place the volume. This will be the
  # same AZ that the EC2 instance runs in.
  # The config parameter can be a full AZ name, or an index into the vpc's table of AZs.
  # by default, index 0 in the vpc is used
  az = cast(str, default_val(pconfig.get_int(
      f"{cfg_prefix}az",
      config_property_info(description="the AWS AZ to deploy the data volume to. Either an AZ name or an index into VPC AZs, default=first VPC AZ"),
    ), "0"))
  try:
    az = vpc.azs[int(az)]
  except ValueError:
    pass
  pulumi.export(f"{export_prefix}volume_az", az)

  # Create a data EBS volume to be used by our EC2 instance. Unlike the built-in boot volume,
  # this volume is *NOT* destroyed when the EC2 instance is terminated/recreated due
  # to a configuration change (e.g., a change in the EC2 instance type or a change
  # to the cloud-init settings), or even if the entire pulumi stack holding the. EC2
  # instance is destroyed.
  # This volume will be used for /home as well as /var/lib/docker/volumes. So our home
  # directories and all docker volumes will be preserved across instance replacement...
  data_vol = EbsVolume(
      f'{resource_prefix}data-vol',
      az=az,
      region=aws_region,
      volume_size_gb=volume_size_gb,
      name=f'{resource_prefix}data-vol',
      use_config=False,
      cfg_prefix=cfg_prefix
  )

  data_vol.stack_export(export_prefix=export_prefix)
