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
    CloudWatch
  )


# create a CloudWatch log group for all our logging needs
cw = CloudWatch()
cw.stack_export()

vpc = VpcEnv.load()
vpc.stack_export()

parent_dns_zone = DnsZone(resource_prefix='parent-', subzone_name='mckelvie.org', create=False)
parent_dns_zone.stack_export(export_prefix='parent_')

dns_zone = DnsZone(resource_prefix='main-', subzone_name='xhub', parent_zone=parent_dns_zone)
dns_zone.stack_export(export_prefix='main_')
