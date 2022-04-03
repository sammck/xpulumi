#!/usr/bin/env python3

import pulumi
from pulumi import Output, config as pconfig
import pulumi_aws as aws
import xpulumi
import xpulumi.runtime
from xpulumi.runtime import (
    VpcEnv,
    DnsZone,
    FrontEndSecurityGroup,
    Ec2KeyPair,
    Ec2Instance,
    CloudWatch,
    pconfig,
    stack_name,
    pulumi_project_name,
    long_stack,
    aws_account_id,
    aws_resource_options,
    default_tags,
  )

stack_name = pulumi.get_stack()

root_zone_name = pconfig.require("root_zone_name")

# create a CloudWatch log group for all our logging needs
cw = CloudWatch()
cw.stack_export()


# Create a VPC, public and private subnets in multiple AZ2, router, and gateway
vpc = VpcEnv.load()
vpc.stack_export()

aws_region = vpc.aws_region

# Create an S3 bucket for general use that can be shared by all stacks that
# use this aws-env. We will define a root key and then each stack
# will create a subdir <pulumi-org>/<pulumi-project-name>/<pulumi-stack> under
# that root key that it can play in to avoid stepping on each others toes.

stack_suffix = '' if stack_name == 'global' else f"-{stack_name}"
long_stack_suffix = f"{pulumi_project_name}{stack_suffix}"

bucket_name = f"{aws_account_id}-{aws_region}-{long_stack_suffix}"

bucket = aws.s3.Bucket(
    "shared-bucket",
    bucket=bucket_name,
    acl='private',
    tags=default_tags,
    opts=aws_resource_options,
  )

project_root_key = 'projects'
project_root_uri = Output.concat("s3://", bucket.bucket, "/", project_root_key)

pulumi.export("shared_s3_bucket", bucket.bucket)
pulumi.export("shared_s3_root_key", project_root_key)
pulumi.export("shared_s3_uri", project_root_uri)

# Start with our precreated root DNS zone (the one we pay for every year that it managed by Route53; e.g., "mycompany.com")
parent_dns_zone = DnsZone(resource_prefix='parent-', subzone_name=root_zone_name, create=False)
parent_dns_zone.stack_export(export_prefix='parent_')

# create a subzone "xhub.<root-domain>" For us to party in. All projects that share this aws-env
# stack will build on this subzone
dns_zone = DnsZone(resource_prefix='main-', subzone_name='xhub', parent_zone=parent_dns_zone)
dns_zone.stack_export(export_prefix='main_')
