#!/usr/bin/env python3

from typing import Any, Callable, List, Tuple, TypeVar, Optional, Union, Dict

import subprocess
import os
import json
import ipaddress
from secret_kv import Jsonable
import yaml
import boto3.session
import botocore.client
import threading

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
from ..util import ( run_once)


initial_cwd = os.getcwd()

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

_current_project_name: Optional[str] = None
_project_cache: Dict[str, XPulumiProject] = {}
_project_cache_lock = threading.Lock()
def get_xpulumi_project(project_name: Optional[str]=None) -> XPulumiProject:
  global _current_project_name
  ctx = get_xpulumi_context()
  project: Optional[XPulumiProject] = None
  if project_name is None:
    with _project_cache_lock:
      if _current_project_name is None:
        project = XPulumiProject(cwd=initial_cwd, ctx=ctx)
        project_name = project.name
        _current_project_name = project_name
        if not project_name in _project_cache:
          _project_cache[project_name] = project
      else:
        project_name = _current_project_name
        project = _project_cache[project_name]
  else:
    with _project_cache_lock:
      project = _project_cache.get(project_name, None)
      if project is None:
        project = XPulumiProject(project_name, cwd=initial_cwd, ctx=ctx)
        _project_cache[project_name] = project
  return project

def get_current_xpulumi_project() -> XPulumiProject:
  return get_xpulumi_project()

def get_current_xpulumi_project_name() -> str:
  return get_current_xpulumi_project().name

def get_current_xpulumi_project() -> XPulumiProject:
  return get_xpulumi_project()

def get_current_xpulumi_project_name() -> str:
  return get_current_xpulumi_project().name

_stack_cache: Dict[Tuple[Optional[str], Optional[str]], XPulumiStack] = {}
_stack_cache_lock = threading.Lock()
def get_xpulumi_stack(
      stack_name: Optional[str]=None,
      project_name: Optional[str]=None,
    ) -> XPulumiStack:
  with _stack_cache_lock:
    result = _stack_cache.get((project_name, stack_name), None)
    if result is None:
      ctx = get_xpulumi_context()
      r_project_name, r_stack_name = parse_stack_name(
          stack_name=stack_name,
          project_name=project_name,
          ctx=ctx,
          cwd=initial_cwd,
          default_project_name=get_current_xpulumi_project_name(),
          default_stack_name=get_current_xpulumi_stack_name()
        )
      result = _stack_cache.get((r_project_name, r_stack_name), None)
      if result is None:
        project = get_xpulumi_project(r_project_name)
        result = XPulumiStack(stack_name=r_stack_name, project=project)
        _stack_cache[(r_project_name, r_stack_name)] = result
      if not (project_name, stack_name) in _stack_cache:
        _stack_cache[(project_name, stack_name)] = result
  return result

def get_current_xpulumi_stack_name() -> str:
  return pulumi.get_stack()

def get_current_xpulumi_stack() -> XPulumiStack:
  return get_xpulumi_stack(get_current_xpulumi_stack_name())

def sync_get_processor_arches_from_instance_type(instance_type: str, region_name: Optional[str]=None) -> List[str]:
  """Returns a list of processor architectures supported by the given EC2 instance type

  Args:
      instance_type (str): An EC2 instance type; e.g., "t2.micro"
      region_name (Optional[str], optional): The aws region to use for querying, or None to use the default aws region. Defaults to None.

  Returns:
      List[str]: The processor architectures supported by the instance type
  """
  sess = boto3.session.Session(region_name=region_name)
  bec2: botocore.client.EC2 = sess.client('ec2')

  resp = bec2.describe_instance_types(
      InstanceTypes=[ instance_type ],
    )
  metas = resp['InstanceTypes']
  if len(metas) == 0:
    raise RuntimeError(f"Invalid EC2 instance type \"{instance_type}\"")
  meta = metas[0]
  processor_info = meta['ProcessorInfo']
  arches = processor_info['SupportedArchitectures']
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

def get_ami_arch_from_instance_type(instance_type: Input[str], region_name: Input[Optional[str]]=None) -> Output[str]:
  """For a given EC2 instance type (as a promise), returns the AMI architecture associated with the instance type as a promise

  Args:
      instance_type (Input[str]): An EC2 instance type; e.g., "t2.micro", as a promise
      region_name (Input[Optional[str]]], optional):
                                  AWS region to use for query, as a promise,
                                  or None to use the default region. Defaults to None.

  Returns:
      str: The AMI architecture associated with instance_type, as a promise
  """
  if region_name is None:
    region_name = aws.get_region().name
  result = Output.all(instance_type, region_name).apply(lambda args: sync_get_ami_arch_from_instance_type(*args))
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
  def gen_yaml(obj: Any) -> str:
    return prefix_text + yaml.dump(obj, sort_keys=True, indent=indent, default_flow_style=default_flow_style, width=width)

  result = Output.all(future_obj).apply(lambda args: gen_yaml(*args))
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
  result = Output.all(future_obj, indent, separators).apply(lambda args: gen_json(*args))
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

T = TypeVar("T")
def default_val(x: Optional[T], default: Optional[T]) -> Optional[T]:
  """Simple function that provides a default value if the argument is None

  Args:
      x (Optional[T]): An optional value or None
      default (Optional[T]): The default to provide if x is None. May also be None.

  Returns:
      Optional[T]: x if x is not None; otherwise default.
  """
  if x is None:
    x = default
  return x

