# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Common runtime values"""
from typing import Optional, Dict, Callable

import string
import pulumi
import threading
from pulumi import ( InvokeOptions, ResourceOptions, get_stack, Output )
import pulumi_aws
import pulumi_random
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
  s3,
  kms,
  secretsmanager,
)

from xpulumi.exceptions import XPulumiError
from .util import (
    get_current_xpulumi_project_name,
    get_xpulumi_context,
    default_val,
    get_current_cloud_subaccount,
    enable_debugging
  )
from project_init_tools import get_git_user_email, run_once

# If environment variable XPULUMI_DEBUGGER is defined, this
# will cause the program to stall waiting for vscode to
# connect to port 5678 for debugging. Useful if Pulumi logging isn't
# cutting it.
enable_debugging()

pconfig = pulumi.Config()
template_env: Dict[str, str] = {}

class TemplateConfig(pulumi.Config):
  _parent: pulumi.Config
  _env: Dict[str, str]
  def __init__(self, parent: Optional[pulumi.Config] = None, env: Optional[Dict[str, str]] = None) -> None:
    if parent is None:
      parent = pconfig
    super().__init__(parent.name)
    if env is None:
      env = template_env
    self._parent = parent
    self._env = template_env

  # pylint: disable=unused-argument
  def _get(
      self,
      key: str,
      use: Optional[Callable] = None,
      instead_of: Optional[Callable] = None,
  ) -> Optional[str]:
    result = self._parent._get(key, use=use, instead_of=instead_of)  # pylint: disable=protected-access
    if not result is None and not self._env is None and len(self._env) > 0:
      try:
        result = string.Template(result).substitute(self._env)
      except ValueError as e:
        raise ValueError(f"Could not expand template config value {key}='{result}': {e}") from e
      return result

tconfig = TemplateConfig()

xpulumi_ctx = get_xpulumi_context()
stack_name = pulumi.get_stack()
pulumi_project_name = pulumi.get_project()
xpulumi_project_name = get_current_xpulumi_project_name()
cloud_subaccount = get_current_cloud_subaccount()
cloud_subaccount_prefix = '' if cloud_subaccount is None else cloud_subaccount + '-'
long_stack = f"{pulumi_project_name}-{stack_name}"
long_subaccount_stack = long_stack
if not cloud_subaccount is None:
  long_subaccount_stack = f"{cloud_subaccount}-{long_stack}"
stack_short_prefix = pulumi.get_stack()[:5] + '-'
long_xstack = f"{xpulumi_project_name}:{stack_name}"
long_subaccount_xstack = f"{xpulumi_project_name}:{stack_name}"
if not cloud_subaccount is None:
  long_subaccount_xstack = f"{cloud_subaccount}::{long_xstack}"

aws_global_region = 'us-east-1'
aws_default_region = pconfig.get('aws:region')
if aws_default_region is None:
  aws_default_region = 'us-west-2'
aws_region = aws_default_region

class AwsRegionData:
  aws_region: str
  aws_provider: pulumi_aws.Provider
  resource_options: ResourceOptions
  invoke_options: InvokeOptions
  caller_identity: pulumi_aws.GetCallerIdentityResult

  def __init__(self, region: str):
    self.aws_region = region
    self.aws_provider = pulumi_aws.Provider(f'aws-{region}', region=region)
    self.resource_options = ResourceOptions(provider=self.aws_provider)
    self.invoke_options = InvokeOptions(provider=self.aws_provider)
    self.caller_identity = pulumi_aws.get_caller_identity(opts=self.invoke_options)

  @property
  def account_id(self) -> str:
    return self.caller_identity.account_id

  @property
  def full_subaccount_id(self) -> str:
    return self.account_id if cloud_subaccount is None else cloud_subaccount + '-' + self.account_id

_aws_regions: Dict[str, AwsRegionData] = {}
_aws_regions_lock = threading.Lock()
def get_aws_region_data(region: Optional[str]=None) -> AwsRegionData:
  if region is None:
    region = aws_default_region
  if region is None:
    raise XPulumiError("An AWS region must be specified")
  with _aws_regions_lock:
    result = _aws_regions.get(region, None)
    if result is None:
      result = AwsRegionData(region)
      _aws_regions[region] = result
  return result

aws_region_data = get_aws_region_data(aws_region)
aws_provider = aws_region_data.aws_provider
aws_resource_options = aws_region_data.resource_options
aws_invoke_options = aws_region_data.invoke_options
aws_account_id = aws_region_data.account_id
aws_full_subaccount_account_id = aws_region_data.full_subaccount_id

aws_global_region_data = get_aws_region_data(aws_global_region)
aws_global_provider = aws_global_region_data.aws_provider
aws_global_resource_options = aws_global_region_data.resource_options
aws_global_invoke_options = aws_global_region_data.invoke_options
aws_global_account_id = aws_global_region_data.account_id
aws_global_full_subaccount_account_id = aws_global_region_data.full_subaccount_id


def with_subaccount_prefix(s: str) -> str:
  return s if cloud_subaccount is None else f"{cloud_subaccount}-{s}"

def get_availability_zones(region: Optional[str]=None):
  azs = sorted(pulumi_aws.get_availability_zones(opts=get_aws_region_data(region).invoke_options).names)
  return azs

owner_tag: Optional[str] = default_val(pconfig.get('owner'), None)
if owner_tag is None:
  owner_tag = get_git_user_email()

default_tags = dict(
    Owner=owner_tag,
    PulumiStack=long_stack,
    XStack=long_xstack,
  )

if not cloud_subaccount is None:
  default_tags.update(subaccount=cloud_subaccount)

def with_default_tags(*args, **kwargs):
  result = dict(default_tags)
  result.update(*args, **kwargs)
  return result

@run_once
def get_stack_pulumi_random_id() -> pulumi_random.RandomId:
  result = pulumi_random.RandomId('xstack-random-id', byte_length=64)
  return result

def get_stack_random_alphanumeric_id(numchars: int=16) -> Output[str]:
  pulumi_id = get_stack_pulumi_random_id()
  result = pulumi_id.b64_url.apply(lambda x: x.replace('_','').replace('-', '')[:numchars])
  return result

template_env.update(
    stack_name=stack_name,
    pulumi_project_name=pulumi_project_name,
    xpulumi_project_name=xpulumi_project_name,
    long_stack=long_stack,
    stack_short_prefix=stack_short_prefix,
    cloud_subaccount_prefix=cloud_subaccount_prefix,
    long_xstack=long_xstack,
    aws_global_region=aws_global_region,
    aws_region=aws_region,
    aws_account_id=aws_account_id,
    aws_full_subaccount_account_id=aws_full_subaccount_account_id,
    aws_global_account_id=aws_global_account_id,
    aws_global_full_subaccount_account_id=aws_global_full_subaccount_account_id,
  )
