# Copyright (c) 2022 Sam McKelvie
#
# See LICENSE file accompanying this package.
#

def load_stack(resource_prefix: str = '', cfg_prefix: str = '', export_prefix: str = ''):
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
      CloudWatch,
    )
  from xpulumi.runtime.common import (
      pconfig,
      tconfig,
      stack_name,
      pulumi_project_name,
      long_stack,
      get_aws_account_id,
      get_aws_resource_options,
      default_tags,
      get_aws_full_subaccount_account_id,
      cloud_subaccount,
      with_subaccount_prefix,
    )

  stack_name = pulumi.get_stack()

  root_zone_name = pconfig.require(f"{cfg_prefix}root_zone_name")
  subzone_name = tconfig.require(f"{cfg_prefix}subzone_name")

  # create a CloudWatch log group for all our logging needs
  cw = CloudWatch(resource_prefix=resource_prefix)
  cw.stack_export(export_prefix=export_prefix)


  # Create a VPC, public and private subnets in multiple AZ2, router, and gateway
  vpc = VpcEnv.load(resource_prefix=resource_prefix, cfg_prefix=cfg_prefix)
  vpc.stack_export(export_prefix=export_prefix)

  aws_region = vpc.aws_region

  # Create an S3 bucket for general use that can be shared by all stacks that
  # use this aws-env. We will define a root key and then each stack
  # will create a subdir <pulumi-org>/<pulumi-project-name>/<pulumi-stack> under
  # that root key that it can play in to avoid stepping on each others toes.

  bucket_name = f"{get_aws_full_subaccount_account_id(aws_region)}-{aws_region}-{long_stack}"

  bucket = aws.s3.Bucket(
      f"{resource_prefix}aws-env-shared-bucket",
      bucket=bucket_name,
      acl='private',
      tags=default_tags,
      opts=get_aws_resource_options(aws_region),
    )

  project_root_key = 'projects'
  project_root_uri = Output.concat("s3://", bucket.bucket, "/", project_root_key)

  pulumi.export(f"{export_prefix}shared_s3_bucket", bucket.bucket)
  pulumi.export(f"{export_prefix}shared_s3_root_key", project_root_key)
  pulumi.export(f"{export_prefix}shared_s3_uri", project_root_uri)

  # Start with our precreated root DNS zone (the one we pay for every year that it managed by Route53; e.g., "mycompany.com")
  parent_dns_zone = DnsZone(resource_prefix=f'{resource_prefix}parent-', subzone_name=root_zone_name, create=False)
  parent_dns_zone.stack_export(export_prefix=f'{export_prefix}parent_')

  # create a subzone "<subzone_name>.<root-domain>" For us to party in. All projects that share this aws-env
  # stack will build on this subzone
  dns_zone = DnsZone(resource_prefix=f'{resource_prefix}main-', subzone_name=subzone_name, parent_zone=parent_dns_zone)
  dns_zone.stack_export(export_prefix=f'{export_prefix}main_')

  if not cloud_subaccount is None:
    pulumi.export(f'{export_prefix}cloud_subaccount', cloud_subaccount)
  