# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Common runtime values"""

from typing import Optional

from .util import default_val
import pulumi
from pulumi import ( ResourceOptions )
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

global_region = 'us-east-1'
region = default_val('aws_region', pulumi_aws.get_region())

paws = pulumi_aws.Provider('aws-%s' % region, region=region)
resource_options_aws = ResourceOptions(provider=paws)

# We create a seperate AWS pulumi provider bound to us-east-1 because certain AWS resources must be provisioned in that region (e.g., cloudfront
# certificates)
if region == global_region:
  global_aws = paws
  resource_options_global_aws = resource_options_aws
else:
  global_aws = paws.Provider('aws-%s' % global_region, region=global_region)
  resource_options_global_aws = ResourceOptions(provider=global_aws)

owner_tag: Optional[str] = default_val(pconfig.get('owner'), None)
if owner_tag is None:
  owner_tag = get_git_user_email()

default_tags = dict(Owner=owner_tag, PulumiStack=long_stack, XProject=get_current_xpulumi_project_name())
def with_default_tags(*args, **kwargs):
  result = dict(default_tags)
  result.update(*args, **kwargs)
  return result
