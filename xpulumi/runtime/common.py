# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Common runtime values"""

from typing import Optional, Dict

from .util import default_val
import pulumi
import threading
from pulumi import ( InvokeOptions, ResourceOptions )
import pulumi_aws
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
  secretsmanager,
)
from .util import get_current_xpulumi_project_name
from ..util import get_git_user_email

pconfig = pulumi.Config()

long_stack = "%s-%s" % (pulumi.get_project(), pulumi.get_stack())
stack_short_prefix = pulumi.get_stack()[:5] + '-'

aws_global_region = 'us-east-1'
aws_default_region = default_val(pconfig.get('aws:region'), 'us-west-2')
aws_region = aws_default_region

class AwsRegionData:
  aws_region: str
  aws_provider: pulumi_aws.Provider
  resource_options: ResourceOptions
  invoke_options: InvokeOptions

  def __init__(self, aws_region: str):
    self.aws_region = aws_region
    self.aws_provider = pulumi_aws.Provider('aws-%s' % aws_region, region=aws_region)
    self.resource_options = ResourceOptions(provider=self.aws_provider)
    self.invoke_options = InvokeOptions(provider=self.aws_provider)

_aws_regions: Dict[str, AwsRegionData] = {}
_aws_regions_lock = threading.Lock()
def get_aws_region_data(aws_region: Optional[str]=None) -> AwsRegionData:
  if aws_region is None:
    aws_region = aws_default_region
  with _aws_regions_lock:
    result = _aws_regions.get(aws_region, None)
    if result is None:
      result = AwsRegionData(aws_region)
      _aws_regions[aws_region] = result
  return result

aws_region_data = get_aws_region_data(aws_region)
aws_provider = aws_region_data.aws_provider
aws_resource_options = aws_region_data.resource_options
aws_invoke_options = aws_region_data.invoke_options

aws_global_region_data = get_aws_region_data(aws_global_region)
aws_global_provider = aws_global_region_data.aws_provider
aws_global_resource_options = aws_global_region_data.resource_options
aws_global_invoke_options = aws_global_region_data.invoke_options


def get_availability_zones(aws_region: Optional[str]=None):
  azs = sorted(pulumi_aws.get_availability_zones(opts=get_aws_region_data(aws_region).invoke_options).names)
  return azs

owner_tag: Optional[str] = default_val(pconfig.get('owner'), None)
if owner_tag is None:
  owner_tag = get_git_user_email()

default_tags = dict(Owner=owner_tag, PulumiStack=long_stack, XProject=get_current_xpulumi_project_name())
def with_default_tags(*args, **kwargs):
  result = dict(default_tags)
  result.update(*args, **kwargs)
  return result
