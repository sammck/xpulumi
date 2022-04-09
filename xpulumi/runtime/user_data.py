from base64 import b64encode
import base64
from copy import deepcopy
from typing import Optional, List, Union, Set, Tuple, Dict, OrderedDict, Iterable, Callable, cast

import subprocess
import os
import json
import ipaddress
import yaml
import io
from io import BytesIO
import gzip
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import email.parser
import time
from collections import OrderedDict as ordereddict
from cloud_init_gen import (
    CloudInitDoc,
    CloudInitDocConvertible,
    CloudInitPart,
    CloudInitRenderable,
    MimeHeadersConvertible,
    render_cloud_init_base64,
    render_cloud_init_binary,
    render_cloud_init_text,
  )
from cloud_init_gen.part import CloudInitPartConvertible

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
  Input,
)

from pulumi_aws import (
  ec2,
  route53,
  acm,
  cognito,
  ebs,
  ecs,
  ecr,
  elasticloadbalancingv2 as elbv2,
  iam,
  cloudwatch,
  rds,
  kms,
  secretsmanager,
  AwaitableGetAmiResult,
)

from ..internal_types import JsonableDict

from .util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
  get_ami_arch_from_instance_type,
  future_func,
  yamlify_promise,
)

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_region_data,
    pconfig,
    default_tags,
    get_availability_zones,
    long_stack,
    aws_provider,
    aws_resource_options,
    aws_invoke_options,
    with_default_tags,
    long_xstack,
  )
from .. import XPulumiError
from .vpc import VpcEnv
from .security_group import FrontEndSecurityGroup
from .ec2_keypair import Ec2KeyPair
from .dns import DnsZone
from project_init_tools import multiline_indent

UserDataPartConvertible = Union['UserDataPart', Input[CloudInitPartConvertible]]

class UserDataPart:
  content: Input[CloudInitPartConvertible]
  mime_type: Input[Optional[str]]
  headers: Input[MimeHeadersConvertible]
  sync_part: Output[CloudInitPart]

  def __init__(
        self,
        content: UserDataPartConvertible,
        mime_type: Input[Optional[str]]=None,
        headers: Input[MimeHeadersConvertible]=None):
    if isinstance(content, UserDataPart):
      self.content = content.content
      self.mime_type = content.mime_type
      self.headers = content.headers
      self.sync_part = content.sync_part
    else:
      self.content = content
      self.mime_type = mime_type
      self.headers = headers
      self.sync_part = Output.all(content, mime_type, headers).apply(
          lambda args: self._resolve_sync_part(
              cast(CloudInitPartConvertible, args[0]),
              cast(Optional[str], args[1]),
              cast(MimeHeadersConvertible, args[2])
            )
        )

  def _resolve_sync_part(
        self,
        content: CloudInitPartConvertible,
        mime_type: Optional[str],
        headers: MimeHeadersConvertible
      ) -> CloudInitPart:
    result: CloudInitPart
    if isinstance(content, CloudInitPart):
      result = content
    else:
      result = CloudInitPart(content, mime_type=mime_type, headers=headers)
    return result

  def render(
        self,
        include_mime_version: Input[bool]=True,
        force_mime: Input[bool]=False,
        include_from: Input[bool]=False,
      ) -> Output[Optional[str]]:
    result = Output.all(
        cast(Output, self.sync_part),
        include_mime_version,
        force_mime,
        include_from
      ).apply(
        lambda args: cast(CloudInitPart, args[0]).render(
            include_mime_version=cast(bool, args[1]),
            force_mime=cast(bool, args[2]),
            include_from=cast(bool, args[3])
          )
      )
    return result

UserDataConvertible = Union['UserData', UserDataPart, Input[CloudInitDocConvertible]]

SyncRenderCallback = Callable[[Tuple[CloudInitDoc, bool]], Optional[Union[str, bytes]]]

class UserData:
  init_content: Input[CloudInitDocConvertible] = None
  init_mime_type: Input[Optional[str]] = None
  init_headers: Input[MimeHeadersConvertible] = None
  init_priority: int

  _priority_parts: List[Tuple[int, UserDataPart]]
  """List of (priority, document-part) tuples, in priority order (lower values first)"""
  _parts: List[UserDataPart]
  """List of document parts in priority order"""

  def __init__(
        self,
        content: UserDataConvertible=None,
        mime_type: Input[Optional[str]]=None,
        headers: Input[MimeHeadersConvertible]=None,
        priority: int=500,
      ):
    if isinstance(content, UserData):
      self.init_content = content.init_content
      self.init_mime_type = content.init_mime_type
      self.init_headers = content.init_headers
      self.init_priority = content.init_priority
      self._priority_parts = content._priority_parts[:]
    elif isinstance(content, UserDataPart):
      self._priority_parts = [ (priority, content) ]
    else:
      self._priority_parts = []
      self.init_mime_type = mime_type
      self.init_headers = headers
      self.init_content = content
      self.init_priority = priority
    self._parts = [ x[1] for x in self._priority_parts ]

  @property
  def parts(self) -> List[UserDataPart]:
    return self._parts

  def add(
        self,
        content: Union[UserDataPart, Input[CloudInitPartConvertible]],
        mime_type: Input[Optional[str]]=None,
        headers: Input[MimeHeadersConvertible]=None,
        priority: int=500,
      ) -> None:
    if not content is None:
      if not isinstance(content, UserDataPart):
        content = UserDataPart(content, mime_type=mime_type, headers=headers)
      i = len(self._priority_parts)
      while i > 0 and self._priority_parts[i-1][0] > priority:
        i -= 1
      self._priority_parts.insert(i, (priority, content))
      self._parts = [ x[1] for x in self._priority_parts ]

  def add_boothook(self, script: Input[str], priority: int=500) -> None:
    content = Output.concat('#boothook\n', script)
    self.add(content, priority=priority)

  def _sync_render_var(
        self,
        content: CloudInitDocConvertible,
        mime_type: Optional[str],
        headers: MimeHeadersConvertible,
        priority: int,
        sync_render: SyncRenderCallback,
        include_mime_version: bool,
        parts: List[CloudInitPart]
      ) -> Optional[Union[str, bytes]]:
    assert len(parts) == len(self._priority_parts)
    #sync_user_data = CloudInitDoc(content, mime_type=mime_type, headers=headers)
    if isinstance(content, CloudInitDoc):
      sync_user_data = CloudInitDoc(content)
    else:
      sync_user_data = CloudInitDoc()
      if not content is None:
        # insert the init part in the correct priority position, but
        # at the earliest point possible (before others with equal priority)
        init_part = CloudInitPart(content=cast(CloudInitPartConvertible, content), mime_type=mime_type, headers=headers)
        priorities = [ x[0] for x in self._priority_parts ]
        parts = parts[:]
        i = 0
        while i < len(priorities) and priorities[i] < priority:
          i += 1
        parts.insert(i, init_part)
        priorities.insert(i, priority)

    for part in parts:
      sync_user_data.add(part)
    result = sync_render((sync_user_data, include_mime_version))
    return result

  def _render_var(
        self,
        sync_render: SyncRenderCallback,
        include_mime_version: Input[bool]=False
      ) -> Output[Optional[Union[str, bytes]]]:
    sync_parts = [x.sync_part for x in self.parts]

    result: Output[Optional[Union[str, bytes]]] = Output.all(
        self.init_content,
        self.init_mime_type,
        self.init_headers,
        self.init_priority,
        sync_render,
        include_mime_version,
        *sync_parts
      ).apply(
        lambda args: self._sync_render_var(
            cast(CloudInitDocConvertible, args[0]),
            cast(Optional[str], args[1]),
            cast(MimeHeadersConvertible, args[2]),
            cast(int, args[3]),
            cast(SyncRenderCallback, args[4]),
            cast(bool, args[5]),
            cast(List[CloudInitPart], args[6:])
          )
      )
    return result


  def render(self, include_mime_version: Input[bool]=True) -> Output[Optional[str]]:
    result = cast(Output[Optional[str]], self._render_var(
        lambda args: args[0].render(include_mime_version=args[1]),
        include_mime_version=include_mime_version
      ))
    return result

  def render_binary(self, include_mime_version: Input[bool]=True) -> Output[Optional[bytes]]:
    result = cast(Output[Optional[bytes]], self._render_var(
        lambda args: args[0].render_binary(include_mime_version=args[1]),
        include_mime_version=include_mime_version
      ))
    return result

  def render_base64(self, include_mime_version: Input[bool]=True) -> Output[Optional[str]]:
    result = cast(Output[Optional[str]], self._render_var(
        lambda args: args[0].render_base64(include_mime_version=args[1]),
        include_mime_version=include_mime_version
      ))
    return result

def render_user_data_text(
      content: UserDataConvertible,
      debug_log: bool=False,
    ) -> Output[Optional[str]]:
  user_data = UserData(content)
  # Note: include_mime_version is required by cloud-init for the top-level part,
  # so we don't even allow setting it to False.
  result = user_data.render(include_mime_version=True)
  if debug_log:
    def report(text: Optional[str]):
      if text is None:
        pulumi.log.info("Rendered user_data is: None")
      else:
        pulumi.log.info(f"Rendered user_data is:\n{multiline_indent(text, 4)}")
    Output.all(cast(Output, result)).apply(lambda args: report(cast(Optional[str], args[0])))
  return result

def render_user_data_binary(
      content: UserDataConvertible,
      debug_log: bool=False,
    ) -> Output[Optional[bytes]]:
  user_data = UserData(content)
  if debug_log:
    render_user_data_text(content, debug_log=True)
  # Note: include_mime_version is required by cloud-init for the top-level part,
  # so we don't even allow setting it to False.
  result = user_data.render_binary(include_mime_version=True)
  return result

def render_user_data_base64(
      content: UserDataConvertible,
      debug_log: bool=False,
    ) -> Output[Optional[str]]:
  user_data = UserData(content)
  if debug_log:
    render_user_data_text(content, debug_log=True)
  # Note: include_mime_version is required by cloud-init for the top-level part,
  # so we don't even allow setting it to False.
  result = user_data.render_base64(include_mime_version=True)
  return result
