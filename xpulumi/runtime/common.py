# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Common runtime values"""
from typing import (
    Optional,
    Dict,
    Callable,
    Type,
    cast,
    Any,
    Union,
    Mapping,
    Iterator,
    Iterable,
    ItemsView,
    KeysView,
    ValuesView,
    TypeVar,
    overload,
  )

from copy import deepcopy
import os
import string
from dataclasses import dataclass
import collections.abc
import pulumi
import threading
from pulumi import ( InvokeOptions, ResourceOptions, get_stack, Output, Config as RawPulumiConfig)
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

from ..internal_types import Jsonable, JsonableDict
from xpulumi.exceptions import XPulumiError
from .util import (
    get_current_xpulumi_project_name,
    get_current_xpulumi_project,
    get_xpulumi_context,
    default_val,
    get_current_cloud_subaccount,
    enable_debugging,
  )
from project_init_tools import RoundTripConfig, get_git_user_email, run_once

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
#pulumi.info(f"should_update_config_info={should_update_config_info}")

@run_once
def get_current_project_round_trip_config() -> RoundTripConfig:
  xproject = get_current_xpulumi_project()
  config_filename = xproject.xpulumi_project_config_file_name
  #pulumi.info(f"rtc file={config_filename}")
  rtc = RoundTripConfig(config_filename)
  return rtc

def register_config_property(key: str, info: Optional[ConfigPropertyInfo]=None) -> None:
  if not key in known_config_properties:
    info = config_property_info(base=info)
    known_config_properties[key] = info
    if should_update_config_info:
      rtc = get_current_project_round_trip_config()
      cfg_desc = cast(Optional[Dict[str, JsonableDict]], rtc.get('stack_config_properties', None))
      if cfg_desc is None:
        cfg_desc = {}
        rtc['stack_config_properties'] = cfg_desc
        rtc.save()
        cfg_desc = cast(Dict[str, JsonableDict], rtc['stack_config_properties'])
        assert not cfg_desc is None

      if not key in cfg_desc:
        rtc_data = dict((k, deepcopy(v)) for k, v in info.__dict__.items() if not v is None)
        cfg_desc[key] = rtc_data
        rtc.save()

class Config(RawPulumiConfig):
  # pylint: disable=unused-argument

  def register_config_property(
        self,
        key: str,
        info: Optional[ConfigPropertyInfo]=None
      ) -> None:
    full_key = self.full_key(key)
    register_config_property(full_key, info)

  def _get(
        self,
        key: str,
        use: Optional[Callable] = None,
        instead_of: Optional[Callable] = None,
        info: Optional[ConfigPropertyInfo] = None,
      ) -> Optional[str]:
    self.register_config_property(key, info)
    result = super()._get(key, use=use, instead_of=instead_of)
    #pulumi.info(f"Config._get('{key}') ==> {result}")
    return result

  def get(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[str]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[str]'))
    return super().get(key)

  def get_secret(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[str]]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[str]', is_secret=True))
    return super().get_secret(key)

  def get_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[bool]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[bool]'))
    return super().get_bool(key)

  def get_secret_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[bool]]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[bool]', is_secret=True))
    return super().get_secret_bool(key)

  def get_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[int]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[int]'))
    return super().get_int(key)

  def get_secret_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[int]]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[int]', is_secret=True))
    return super().get_secret_int(key)

  def get_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[float]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[float]'))
    return super().get_float(key)

  def get_secret_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[float]]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[float]', is_secret=True))
    return super().get_secret_float(key)

  def get_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Any]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[Json]'))
    return super().get_object(key)

  def get_secret_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Optional[Output[Any]]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Optional[Json]', is_secret=True))
    return super().get_secret_object(key)

  def require(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> str:
    self.register_config_property(key, config_property_info(base=info, type_desc='str'))
    return super().require(key)

  def require_secret(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[str]:
    self.register_config_property(key, config_property_info(base=info, type_desc='str', is_secret=True))
    return super().require_secret(key)

  def require_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> bool:
    self.register_config_property(key, config_property_info(base=info, type_desc='bool'))
    return super().require_bool(key)

  def require_secret_bool(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[bool]:
    self.register_config_property(key, config_property_info(base=info, type_desc='bool', is_secret=True))
    return super().require_secret_bool(key)

  def require_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> int:
    self.register_config_property(key, config_property_info(base=info, type_desc='int'))
    return super().require_int(key)

  def require_secret_int(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[int]:
    self.register_config_property(key, config_property_info(base=info, type_desc='int', is_secret=True))
    return super().require_secret_int(key)

  def require_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> float:
    self.register_config_property(key, config_property_info(base=info, type_desc='float'))
    return super().require_float(key)

  def require_secret_float(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[float]:
    self.register_config_property(key, config_property_info(base=info, type_desc='float', is_secret=True))
    return super().require_secret_float(key)

  def require_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Any:
    self.register_config_property(key, config_property_info(base=info, type_desc='Json'))
    return super().require_object(key)

  def require_secret_object(self, key: str, info: Optional[ConfigPropertyInfo] = None) -> Output[Any]:
    self.register_config_property(key, config_property_info(base=info, type_desc='Json', is_secret=True))
    return super().require_secret_object(key)

pconfig = Config()

_T = TypeVar('_T')

class TemplateEnv(Mapping[str, str]):
  _data: Dict[str, Union[str, Callable[[], str]]]
  def __init__(self):
    self._data = {}

  def __getitem__(self, key: str) -> str:
    result = self._data[key]
    if not isinstance(result, str):
      result = result()
    return result

  @overload
  def get(self, key: str) -> Optional[str]:  # pylint: disable=arguments-differ
    ...
  @overload
  def get(self, key: str, default: _T) -> Union[str, _T]:  # pylint: disable=signature-differs
    ...

  def get(self, key: str, default: Any = None) -> Any:
    if key in self._data:
      result: Any = self[key]
    else:
      result = default
    return result

  def __contains__(self, key: Any) -> bool:
    return key in self._data

  def __len__(self) -> int:
    return len(self._data)

  def __iter__(self) -> Iterator[str]:
    return iter(self._data)

  def get_resolved_dict(self) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for k, v in self._data.items():
      if not isinstance(v, str):
        v = v()
      result[k] = v
    return result

  def items(self) -> ItemsView[str, str]:
    return self.get_resolved_dict().items()

  def values(self) -> ValuesView[str]:
    return self.get_resolved_dict().values()

  def add_items(self, data: Dict[str, Union[str, Callable[[], str]]]) -> None:
    self._data.update(data)

template_env = TemplateEnv()

class TemplateConfig(Config):
  _parent: Config
  _env: Mapping[str, str]
  def __init__(
        self,
        parent: Optional[Config] = None,
        env: Optional[Mapping[str, str]] = None
      ) -> None:
    if parent is None:
      parent = pconfig
    super().__init__(parent.name)
    if env is None:
      env = template_env
    self._parent = parent
    self._env = env

  def register_config_property(
        self,
        key: str,
        info: Optional[ConfigPropertyInfo]=None
      ) -> None:
    full_key = self.full_key(key)
    info = config_property_info(base=info)
    if info.type_desc is None:
      info.type_desc = 'Template'
    else:
      info.type_desc = f'Template[{info.type_desc}]'
    register_config_property(full_key, info)

  # pylint: disable=unused-argument
  def _get(
      self,
      key: str,
      use: Optional[Callable] = None,
      instead_of: Optional[Callable] = None,
      info: Optional[ConfigPropertyInfo] = None,
  ) -> Optional[str]:
    self.register_config_property(key, info)
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
_aws_default_region = cast(Optional[str], Config('aws').get('region'))
assert _aws_default_region is None or isinstance(_aws_default_region, str)
if _aws_default_region is None:
  _aws_default_region = 'us-west-2'
aws_default_region: str = _aws_default_region
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

#aws_region_data = get_aws_region_data(aws_region)
#aws_provider = aws_region_data.aws_provider
def get_aws_provider(region: Optional[str]=None) -> pulumi_aws.Provider:
  return get_aws_region_data(region).aws_provider
def get_aws_resource_options(region: Optional[str]=None) -> ResourceOptions:
  return get_aws_region_data(region).resource_options
def get_aws_invoke_options(region: Optional[str]=None) -> InvokeOptions:
  return get_aws_region_data(region).invoke_options
def get_aws_account_id(region: Optional[str]=None) -> str:
  return get_aws_region_data(region).account_id
def get_aws_full_subaccount_account_id(region: Optional[str]=None) -> str:
  return get_aws_region_data(region).full_subaccount_id

#aws_resource_options = aws_region_data.resource_options
#aws_invoke_options = aws_region_data.invoke_options
#aws_account_id = aws_region_data.account_id
#aws_full_subaccount_account_id = aws_region_data.full_subaccount_id

def get_aws_global_region_data() -> AwsRegionData:
  return get_aws_region_data(aws_global_region)
def get_aws_global_provider() -> pulumi_aws.Provider:
  return get_aws_global_region_data().aws_provider
def get_aws_global_resource_options() -> ResourceOptions:
  return get_aws_global_region_data().resource_options
def get_aws_global_invoke_options() -> InvokeOptions:
  return get_aws_global_region_data().invoke_options
def get_aws_global_account_id() -> str:
  return get_aws_global_region_data().account_id
def get_aws_global_full_subaccount_account_id() -> str:
  return get_aws_global_region_data().full_subaccount_id

#aws_global_region_data = get_aws_region_data(aws_global_region)
#aws_global_provider = aws_global_region_data.aws_provider
#aws_global_resource_options = aws_global_region_data.resource_options
#aws_global_invoke_options = aws_global_region_data.invoke_options
#aws_global_account_id = aws_global_region_data.account_id
#aws_global_full_subaccount_account_id = aws_global_region_data.full_subaccount_id

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

template_env.add_items(dict(
    stack_name=stack_name,
    pulumi_project_name=pulumi_project_name,
    xpulumi_project_name=xpulumi_project_name,
    long_stack=long_stack,
    stack_short_prefix=stack_short_prefix,
    cloud_subaccount_prefix=cloud_subaccount_prefix,
    long_xstack=long_xstack,
    aws_global_region=aws_global_region,
    aws_region=aws_region,
    aws_account_id=get_aws_account_id,
    aws_full_subaccount_account_id=get_aws_full_subaccount_account_id,
    aws_global_account_id=get_aws_global_account_id,
    aws_global_full_subaccount_account_id=get_aws_global_full_subaccount_account_id,
  ))
