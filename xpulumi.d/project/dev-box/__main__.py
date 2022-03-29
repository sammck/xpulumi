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
    CloudWatch,
    MIMEMultipart,
    MIMEText,
  )

xpulumi.runtime.enable_debugging()
#breakpoint()

aws_env_stack_name = 'aws-env:dev'

cw_log_group_id = require_stack_output('cloudwatch_log_group', stack=aws_env_stack_name)
cw = CloudWatch(log_group_id=cw_log_group_id)
cw.stack_export()

vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name)

dns_zone_id = require_stack_output('main_dns_zone_id', stack=aws_env_stack_name)
dns_zone = DnsZone(resource_prefix='main-', zone_id=dns_zone_id)
dns_zone.stack_export(export_prefix='main_')

ud = MIMEMultipart(boundary='@@==@@')

boothook_text = '''echo "It works, instance ID = $INSTANCE_ID!!!"'''
ud.attach(MIMEText(boothook_text, _subtype='cloud-boothook'))

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
    user_data=ud,
  )
ec2_instance.stack_export()
