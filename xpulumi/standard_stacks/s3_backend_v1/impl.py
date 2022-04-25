# Copyright (c) 2022 Sam McKelvie
#
# See LICENSE file accompanying this package.
#

def load_stack(resource_prefix: str = '', cfg_prefix: str = '', export_prefix: str = ''):
  import pulumi
  import pulumi_aws as aws
  from xpulumi.runtime import (
      split_s3_uri,
  )
  from xpulumi.runtime.common import (
      tconfig,
      get_aws_account_id,
      aws_default_region,
      cloud_subaccount,
      get_aws_full_subaccount_account_id,
      get_aws_resource_options,
      default_tags,
      config_property_info,
  )

  region = aws_default_region

  backend_url = tconfig.require(
      f"{cfg_prefix}backend_url",
      info=config_property_info(description="The full s3:// URI of the backend, omitting organization and project if those are not included in the backend")
    )
  #pulumi.log.info(f"backend_url={backend_url}")
  bucket_name, backend_subkey = split_s3_uri(backend_url)
  while backend_subkey.endswith('/'):
    backend_subkey = backend_subkey[:-1]

  aws.s3.Bucket(f"{resource_prefix}bucket",
      bucket=bucket_name,
      opts=get_aws_resource_options(region),
      tags=default_tags,
    )

  pulumi.export(f"{export_prefix}backend_bucket", bucket_name)
  pulumi.export(f"{export_prefix}backend_subkey", backend_subkey)
  pulumi.export(f"{export_prefix}backend_url", backend_url)
  pulumi.export(f"{export_prefix}aws_region", region)
  pulumi.export(f"{export_prefix}aws_account", get_aws_account_id(region))
  pulumi.export(f"{export_prefix}aws_full_subaccount", get_aws_full_subaccount_account_id(region))
  if not cloud_subaccount is None:
    pulumi.export(f"{export_prefix}cloud_subaccount", cloud_subaccount)
