#!/usr/bin/env python3

import pulumi
from pulumi import Output
import pulumi_aws as aws
import xpulumi
import xpulumi.runtime
from xpulumi.runtime import (
    VpcEnv,
    DnsZone,
    FrontEndSecurityGroup,
    Ec2KeyPair,
    Ec2Instance,
    require_stack_output,
  )

xpulumi.runtime.enable_debugging()
#breakpoint()

aws_env_stack_name = 'aws-env:dev'

vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name)

dns_zone_id = require_stack_output('main_dns_zone_id', stack=aws_env_stack_name)
dns_zone = DnsZone(resource_prefix='main-', zone_id=dns_zone_id)
dns_zone.stack_export(export_prefix='main_')

ec2_instance = Ec2Instance(
    vpc=vpc,
    resource_prefix="frontend-",
    use_config=True,
    cfg_prefix="fe-",
    parent_dns_zone=dns_zone,
    dns_subnames=[ '', 'www', 'api' ],
    open_ports=[ 22, 80, 443 ],
    public_key_file="~/.ssh/id_rsa.pub",
    instance_type="t3.medium",
  )
ec2_instance.stack_export()
