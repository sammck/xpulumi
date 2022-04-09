import pulumi
import pulumi_aws as aws
from xpulumi.runtime import pconfig, aws_provider

bucket_name = pconfig.require("bucket_name")
backend_subkey = pconfig.require("backend_subkey")

bucket = aws.s3.Bucket("bucket",
    arn="arn:aws:s3:::492598163938-us-west-2-cloud-dev",
    bucket="492598163938-us-west-2-cloud-dev",
    hosted_zone_id="Z3BJ6K6RIION7M",
    request_payer="BucketOwner",
    opts=pulumi.ResourceOptions(
        provider=aws_provider,
        protect=True
      )
  )

backend_uri = f"s3://{bucket_name}/{backend_subkey}"
if not backend_subkey is None:
  while backend_subkey.startswith('/'):
    backend_subkey = backend_subkey[1:]
  while backend_subkey.endswith('/'):
    backend_subkey = backend_subkey[:-1]
  if backend_subkey != '':
    backend_uri = backend_uri + '/' + backend_subkey

pulumi.export("backend_bucket", bucket.bucket)
pulumi.export("backend_uri", backend_uri)
