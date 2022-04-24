# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Common runtime values"""
from typing import Optional, Dict, Callable, Type, cast, Any

from copy import deepcopy
import os
import string
from dataclasses import dataclass
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
    get_current_xpulumi_project,
    get_xpulumi_context,
    default_val,
    get_current_cloud_subaccount,
    enable_debugging
  )
from project_init_tools import RoundTripConfig, get_git_user_email, run_once, round_trip_config

# If environment variable XPULUMI_DEBUGGER is defined, this
# will cause the program to stall waiting for vscode to
# connect to port 5678 for debugging. Useful if Pulumi logging isn't
# cutting it.
enable_debugging()

@dataclass
class ConfigPropertyInfo:
  description: Optional[str] = None
  type_desc: Optional[str] = None
  is_secret: Optional[bool] = None

def config_property_info(**kwargs) -> ConfigPropertyInfo:
  base = cast(Optional[ConfigPropertyInfo], kwargs.pop('base', None))
  if not base is None:
    kwargs.update((k, v) for k,v in base.__dict__.items() if not v is None)
  result = ConfigPropertyInfo(**kwargs)
  return result

known_config_properties: Dict[str, ConfigPropertyInfo] = {}
should_update_config_info = os.environ.get('XPULUMI_UPDATE_CONFIG_INFO', '') != ''

@run_once
def get_current_project_round_trip_config() -> RoundTripConfig:
  xproject = get_current_xpulumi_project()
  config_filename = xproject.xpulumi_project_config_file_name
  rtc = RoundTripConfig(config_filename)
  return rtc

def register_config_property(key: str, info: Optional[ConfigPropertyInfo]=None) -> None:
  if not key in known_config_properties:
    info = config_property_info(base=info)
    known_config_properties[key] = info
    if should_update_config_info:
      rtc = get_current_project_round_trip_config()
      if not key in rtc:
        rtc.save()

class Config(pulumi.Config):
  # pylint: disable=unused-argument
  def _get(
        self,
        key: str,
        use: Optional[Callable] = None,
        instead_of: Optional[Callable] = None,
        info: Optional[ConfigPropertyInfo] = None,
      ) -> Optional[str]:
    full_key = self.full_key(key)
    register_config_property(key, info)
    result = super()._get(full_key, use=use, instead_of=instead_of)
    pulumi.info(f"Config._get('{key}') ==> {result}")
    return result

  def get(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[str]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[str]'))
    return super().get(key)

  def get_secret(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[str]]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[str]', is_secret=True))
    return super().get_secret(key)

  def get_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[bool]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[bool]'))
    return super().get_bool(key)

  def get_secret_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[bool]]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[bool]', is_secret=True))
    return super().get_secret_bool(key)

  def get_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[int]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[int]'))
    return super().get_int(key)

  def get_secret_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[int]]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[int]', is_secret=True))
    return super().get_secret_int(key)

  def get_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[float]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[float]'))
    return super().get_float(key)

  def get_secret_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[float]]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[float]', is_secret=True))
    return super().get_secret_float(key)

  def get_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Any]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[Json]'))
    return super().get_object(key)

  def get_secret_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[Any]]:
    register_config_property(key, config_property_info(base=info, type_desc='Optional[Json]', is_secret=True))
    return super().get_secret_object(key)

  def require(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> str:
    register_config_property(key, config_property_info(base=info, type_desc='str'))
    return super().require(key)

  def require_secret(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[str]:
    register_config_property(key, config_property_info(base=info, type_desc='str', is_secret=True))
    return super().require_secret(key)

  def require_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> bool:
    register_config_property(key, config_property_info(base=info, type_desc='bool'))
    return super().require_bool(key)

  def require_secret_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[bool]:
    register_config_property(key, config_property_info(base=info, type_desc='bool', is_secret=True))
    return super().require_secret_bool(key)

  def require_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> int:
    register_config_property(key, config_property_info(base=info, type_desc='int'))
    return super().require_int(key)

  def require_secret_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[int]:
    register_config_property(key, config_property_info(base=info, type_desc='int', is_secret=True))
    return super().require_secret_int(key)

  def require_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> float:
    register_config_property(key, config_property_info(base=info, type_desc='float'))
    return super().require_float(key)

  def require_secret_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[float]:
    register_config_property(key, config_property_info(base=info, type_desc='float', is_secret=True))
    return super().require_secret_float(key)

  def require_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Any:
    register_config_property(key, config_property_info(base=info, type_desc='Json'))
    return super().require_object(key)

  def require_secret_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[Any]:
    register_config_property(key, config_property_info(base=info, type_desc='Json', is_secret=True))
    return super().require_secret_object(key)

pconfig = Config()
template_env: Dict[str, str] = {}

class TemplateConfig(Config):
  _parent: Config
  _env: Dict[str, str]
  def __init__(
        self,
        parent: Optional[Config] = None,
        env: Optional[Dict[str, str]] = None
      ) -> None:
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
      info: Optional[ConfigPropertyInfo] = None,
  ) -> Optional[str]:
    info = config_property_info(base=info)
    if info.type_desc is None:
      info.type_desc = 'Template'
    else:
      info.type_desc = f'Template[{info.type_desc}]'
    result = self._parent._get(   # pylint: disable=protected-access
        key,
        use=use,
        instead_of=instead_of,
        info=info,
      )
    if not result is None and not self._env is None and len(self._env) > 0:
      try:
        result = string.Template(result).substitute(self._env)
      except ValueError as e:
        raise ValueError(f"Could not expand template config value {key}='{result}': {e}") from e
      return result
    return None

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
