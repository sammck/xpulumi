#!/usr/bin/env python3

from typing import Any, Callable, List, Tuple, TypeVar, Optional, Union, Dict, cast
from mypy_boto3_ec2.literals import InstanceTypeType
import subprocess
import os
import json
import ipaddress
from secret_kv import Jsonable
import yaml
import boto3.session
import botocore.client
import threading
import debugpy # type: ignore[import]
import time
import shlex

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
  Input,
)

import pulumi_aws as aws
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

from ..base_context import XPulumiContextBase
from ..project import XPulumiProject
from ..stack import XPulumiStack, parse_stack_name
from project_init_tools import (
    run_once,
    gen_etc_shadow_password_hash as sync_gen_etc_shadow_password_hash,
    dedent,
    )

initial_cwd = os.getcwd()

_debugger_attached: bool = False

def xbreakpoint() -> None:
  if _debugger_attached:
    breakpoint()  # pylint: disable=forgotten-debug-statement

def enable_debugging(host: str='localhost', port: int=5678, max_wait_secs: int=30, force: bool=False) -> None:
  global _debugger_attached
  if force or os.environ.get("XPULUMI_DEBUGGER", '') != '':
    pulumi.log.info("Pulumi debugger activated; waiting for debugger to attach")
    debugpy.listen((host, port))
    max_wait_s = max_wait_secs
    while max_wait_s >= 0:
      if debugpy.is_client_connected():
        _debugger_attached = True
        pulumi.log.info("Pulumi debugger attached")
        breakpoint()  # pylint: disable=forgotten-debug-statement
        break
      time.sleep(1)
      max_wait_s -= 1
    else:
      pulumi.log.info("Pulumi debugger did not attach; resuming")
  else:
    pulumi.log.info("Pulumi debugger not activated")



T = TypeVar('T')
def future_func(func: Callable[..., T]) -> Callable[..., Output[T]]:
  """A decorator for a function that takes resolved
     future inputs as arguments, and returns a resolved future result.

     The decorated function will take unresolved arguments and return
     an unresolved result

  Args:
      func (Callable[..., T]): A synchronous function

  Returns:
      Callable[..., Output[T]]: A function that accepts promises and returns a promise
  """
  def wrapper(*future_args):
    # "pulumi.Output.all(*future_args).apply(lambda args: sync_func(*args))"" is a pattern
    # provided by pulumi. It waits until all promises in future_args have been satisfied,
    # then invokes sync_func with the realized values of all the future_args as *args. Finally
    # it wraps the synchronous function as a promise and returns the new promise as the result.
    # this allows you to write synchronous code in pulumi that depends on future values, and
    # turn it into asynchronous code
    result = Output.all(*future_args).apply(lambda args: func(*args))
    return result
  return wrapper


TTL_SECOND: int = 1
TTL_MINUTE: int = TTL_SECOND * 60
TTL_HOUR: int = TTL_MINUTE * 60
TTL_DAY: int = TTL_HOUR * 24

def future_val(v: Union[T, Output[T]]) -> Output[T]:
  """Turns a synchronous value into an Output promise

  Args:
      v (Union[T, Output[T]]): A simple value

  Returns:
      Output[T]: A promise that will return the value
  """
  result = Output.all(v).apply(lambda args: args[0])
  return result

@run_once
def get_xpulumi_context() -> XPulumiContextBase:
  return XPulumiContextBase(cwd=initial_cwd)

def get_xpulumi_project(project_name: Optional[str]=None) -> XPulumiProject:
  #global _current_project_name
  ctx = get_xpulumi_context()
  return ctx.get_project(project_name=project_name, cwd=initial_cwd)

def get_current_xpulumi_project() -> XPulumiProject:
  return get_xpulumi_project()

def get_current_xpulumi_project_name() -> str:
  return get_current_xpulumi_project().name

def get_current_xpulumi_stack_name() -> str:
  return pulumi.get_stack()

def get_xpulumi_stack(
      stack_name: Optional[str]=None,
      project_name: Optional[str]=None,
    ) -> XPulumiStack:
  ctx = get_xpulumi_context()
  r_project_name, r_stack_name = parse_stack_name(
      stack_name=stack_name,
      project_name=project_name,
      ctx=ctx,
      cwd=initial_cwd,
      default_project_name=get_current_xpulumi_project_name(),
      default_stack_name=get_current_xpulumi_stack_name()
    )
  project = ctx.get_project(r_project_name)
  stack = project.get_stack(r_stack_name, create=False)
  return stack

def get_current_xpulumi_stack() -> XPulumiStack:
  return get_current_xpulumi_project().get_stack(get_current_xpulumi_stack_name())

def get_current_cloud_subaccount() -> Optional[str]:
  result = get_current_xpulumi_stack().cloud_subaccount
  if result == '':
    result = None
  return result

def get_current_cloud_subaccount_prefix() -> str:
  subaccount = get_current_cloud_subaccount()
  return '' if (subaccount is None or subaccount == '') else subaccount + '-'

def sync_get_processor_arches_from_instance_type(instance_type: str, region_name: Optional[str]=None) -> List[str]:
  """Returns a list of processor architectures supported by the given EC2 instance type

  Args:
      instance_type (str): An EC2 instance type; e.g., "t2.micro"
      region_name (Optional[str], optional): The aws region to use for querying, or None to use the default aws region. Defaults to None.

  Returns:
      List[str]: The processor architectures supported by the instance type
  """
  sess = boto3.session.Session(region_name=region_name)
  bec2 = sess.client('ec2')

  resp = bec2.describe_instance_types(
      InstanceTypes= cast(List[InstanceTypeType], [ instance_type ]),
    )
  metas = resp['InstanceTypes']
  if len(metas) == 0:
    raise RuntimeError(f"Invalid EC2 instance type \"{instance_type}\"")
  meta = metas[0]
  processor_info = meta['ProcessorInfo']
  arches: List[str] = list(processor_info['SupportedArchitectures'])
  if len(arches) < 1:
    raise RuntimeError(f"No processor architectures for instance type \"{instance_type}\"")
  return arches

def sync_get_ami_arch_from_processor_arches(processor_arches: Union[str, List[str]]) -> str:
  """Maps a processor architecture to the equivalent AMI architecture"""
  if not isinstance(processor_arches, list):
    processor_arches = [ processor_arches ]

  if len(processor_arches) == 0:
    raise RuntimeError("Empty processor architecture list--cannot determine AMI architecture")

  result: Optional[str] = None

  for processor_arch in processor_arches:
    if processor_arch in [ "x86_64", "amd64" ]:
      result = "amd64"
    elif processor_arch in [ "arm64", "aarch64" ]:
      result = "arm64"
    if not result is None:
      break
  if result is None:
    raise RuntimeError(f"Unsupported processor architectures {processor_arches}--cannot determine AMI architecture")
  return result

def sync_get_ami_arch_from_instance_type(instance_type: str, region_name: Optional[str]=None) -> str:
  """For a given EC2 instance type, returns the AMI architecture associated with the instance type

  Args:
      instance_type (str): An EC2 instance type; e.g., "t2.micro"
      region_name (Optional[str], optional): AWS region to use for query, or None to use the default region. Defaults to None.

  Returns:
      str: The AMI architecture associated with instance_type
  """
  processor_arches = sync_get_processor_arches_from_instance_type(instance_type, region_name=region_name)
  result = sync_get_ami_arch_from_processor_arches(processor_arches)
  return result

def get_ami_arch_from_instance_type(instance_type: Input[str], region_name: Input[Optional[str]]=None) -> Input[str]:
  """For a given EC2 instance type (as a promise), returns the AMI architecture associated with the instance type as a promise

  Args:
      instance_type (Input[str]): An EC2 instance type; e.g., "t2.micro", as a promise
      region_name (Input[Optional[str]]], optional):
                                  AWS region to use for query, as a promise,
                                  or None to use the default region. Defaults to None.

  Returns:
      Input[str]: The AMI architecture associated with instance_type. If parameters are concrete,
                  result is concrete; otherwise it is a promise
  """
  if isinstance(instance_type, str) and (region_name is None or isinstance(region_name, str)):
    result: Input[str] = sync_get_ami_arch_from_instance_type(instance_type, region_name=region_name)
  else:
    result = Output.all(instance_type, region_name).apply(lambda args: sync_get_ami_arch_from_instance_type(*args))  # type: ignore [arg-type]
  return result


def yamlify_promise(
      future_obj: Input[Jsonable],
      indent: Input[int]=1,
      default_flow_style: Input[Optional[bool]]=None,
      width: Input[int]=80,
      prefix_text: Input[Optional[str]]=None
    ) -> Output[str]:
  """Convert a Promised Jsonable value to a Promise to yamlify the result of that Promise.

  An asyncronous (Promise) version of yaml.dumps() that operates on Pulumi Input
  values that have not yet been evaluated. Sorts keys to provide stability of result strings.
  The result is another Pulumi output value that when evaluated will generate the
  yaml string associated with future_obj

  Args:
      future_obj(Input[Jsonable]):       A Pulumi Input Jsonable value that is not yet evaluated
      prefix_text(Input[str], optional): Optional prefix text to insert before yaml. Useful for a header comment.

  Returns:
      Output[str]   A Pulumi "output" value that will resolve to the yaml string corresponding to future_obj
  """
  def gen_yaml(obj: Jsonable, indent: int, default_flow_style: Optional[bool], width: int, prefix_text: Optional[str]) -> str:
    if prefix_text is None:
      prefix_text = ''
    return prefix_text + yaml.dump(obj, sort_keys=True, indent=indent, default_flow_style=default_flow_style, width=width)

  result = Output.all(future_obj, indent, default_flow_style, width, prefix_text).apply(lambda args: gen_yaml(*args)) # type: ignore [arg-type]
  return result


def jsonify_promise(
      future_obj: Input[Jsonable],
      indent: Input[Optional[Union[int, str]]]=None,
      separators: Input[Optional[Tuple[str, str]]]=None
    ) -> Output[str]:
  """Convert a Promise object to a Promise to jsonify the result of that Promise.

  An asyncronous (Promise) version of json.dumps() that operates on Pulumi output
  values that have not yet been evaluated. Sorts keys to provide stability of result strings.
  The result is another Pulumi output value that when evaluated will generate the
  json string associated with future_obj

  Args:
      future_obj(Input[Jsonable]):       A Pulumi Input Jsonable value that is not yet evaluated

  Returns:
      Output[str]   A Pulumi "output" value that will resolve to the json string corresponding to future_obj
  """
  def gen_json(
        obj: Jsonable,
        indent: Optional[Union[int, str]],
        separators: Optional[Tuple[str, str]]
      ) -> str:
    return json.dumps(obj, sort_keys=True, indent=indent, separators=separators)

  # "pulumi.Output.all(*future_args).apply(lambda args: sync_func(*args))"" is a pattern
  # provided by pulumi. It waits until all promises in future_args have been satisfied,
  # then invokes sync_func with the realized values of all the future_args as *args. Finally
  # it wraps the synchronous function as a promise and returns the new promise as the result.
  # this allows you to write synchronous code in pulumi that depends on future values, and
  # turn it into asynchronous code
  result = Output.all(future_obj, indent, separators).apply(lambda args: gen_json(*args)) # type: ignore[arg-type]
  return result

def list_of_promises(promises: List[Output[Any]]) -> Output[List[Any]]:
  """Converts a list of promises into a promise to return a list of values

  :param promises: A list of promises
  :type promises: List[Output[Any]]
  :return: promise to return list
  :rtype: Output[List[Any]]
  """
  def gen_result(*args: Any) -> List[Any]:
    return list(args)

  return Output.all(*tuple(promises)).apply(lambda args: gen_result(*args))

T2 = TypeVar("T2")
def default_val(x: Optional[T2], default: Optional[T2]) -> Optional[T2]:
  """Simple function that provides a default value if the argument is None

  Args:
      x (Optional[T2]): An optional value or None
      default (Optional[T2]): The default to provide if x is None. May also be None.

  Returns:
      Optional[T2]: x if x is not None; otherwise default.
  """
  if x is None:
    x = default
  return x

def gen_etc_shadow_password_hash(password: Input[str], keep_hash_secret: bool=True) -> Output[str]:
  result: Output[str] = Output.all(password).apply(
      lambda args: sync_gen_etc_shadow_password_hash(cast(str, args[0]))
    )
  if not keep_hash_secret:
    result = Output.unsecret(result)
  return result

def shell_quote_promise(
      future_str: Input[str],
    ) -> Input[str]:
  if isinstance(future_str, str):
    result: Input[str] = shlex.quote(future_str)
  else:
    result = Output.all(future_str).apply(lambda args: shlex.quote(*args))  # type: ignore[arg-type]
  return result

def future_dedent(s: Input[str], **kwargs) -> Output[str]:
  result: Output[str] = Output.all(s, kwargs).apply(
      lambda args: dedent(cast(str, args[0]), **cast(Dict[str, Any], args[1])
    ))
  return result

def concat_and_dedent(*args: str, **kwargs) -> Output[str]:
  return future_dedent(Output.concat(*args), **kwargs)
