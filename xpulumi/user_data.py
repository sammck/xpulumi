from base64 import b64encode
import base64
from copy import deepcopy
from typing import Optional, List, Union, Set, Tuple, Dict, OrderedDict, Iterable

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

from .internal_types import JsonableDict

from .exceptions import XPulumiError

GZIP_FIXED_MTIME: float = 0.0

class UserDataPartType:
  mime_type: str
  mime_subtype: str
  comment_tag: Optional[str]=None
  comment_line: Optional[str]=None

  def __init__(self, mime_subtype: str, comment_tag: Optional[str]=None):
    self.mime_subtype = mime_subtype
    self.mime_type = 'text/' + mime_subtype
    self.comment_tag = comment_tag
    self.comment_line = None if comment_tag is None else '#' + comment_tag

_part_type_list: List[UserDataPartType] = [
    UserDataPartType('cloud-boothook', 'boothook'),                    # A script with a shebang header
    UserDataPartType('cloud-config', 'cloud-config'),                  # A YAML doc with rich config data
    UserDataPartType('cloud-config-archive', 'cloud-config-archive'),  # a YAML doc that contains a list of docs, like multipart mime
    UserDataPartType('cloud-config-jsonp', 'cloud-config-jsonp'),      # fine-grained merging with vendor-provided cloud-config
    UserDataPartType('jinja2', "# template: jinja"),                   # expand jinja2 template. 2nd line is comment describing actual part type
    UserDataPartType('part-handler', 'part-handler'),                  # part contains python code that can process custom mime types for subsequent parts
    UserDataPartType('upstart-job', 'upstart-job'),                    # content plated into a file under /etc/init, to be consumed by upstart
    UserDataPartType('x-include-once-url', 'include-once'),            # List of urls that are read one at a time and processed as any item, but only once
    UserDataPartType('x-include-url', 'include'),                      # list of urls that are read one at a time and processed as any item
    UserDataPartType('x-shellscript', '!'),                            # simple userdata shell script (comment line has variable chars)
    UserDataPartType('x-shellscript-per-boot'),                        # shell script run on every boot
    UserDataPartType('x-shellscript-per-instance'),                    # shell script run once per unique instance
    UserDataPartType('x-shellscript-per-once'),                        # shell script run only once
  ]

mime_to_user_data_part_type: Dict[str, UserDataPartType] = dict((x.mime_type, x) for x in _part_type_list)
comment_to_user_data_part_type: Dict[str, UserDataPartType] = dict((x.comment_line, x) for x in _part_type_list)


class SyncUserDataPart:
  content: Optional[str]
  mime_type: str
  mime_version: Optional[str] = None
  comment_line: Optional[str] = None
  comment_type: Optional[str] = None
  comment_line_included: bool = False
  headers: OrderedDict[str, str]   # not including MIME-Version or Content-Type

  def __init__(
        self,
        content: Optional[Union[str, JsonableDict]],
        mime_type: Optional[str]=None,
        headers: Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]]=None):
    if content is None:
      self.content = None
      self.mime_type = ''
      self.headers = ordereddict()
    else:
      original_content = content
      is_yaml = isinstance(original_content, dict)
      if is_yaml:
        content = yaml.dump(
            original_content,
            sort_keys=True,
            indent=1,
            default_flow_style=None,
            width=10000,
          )

      mime_version: Optional[str] = None
      comment_line: Optional[str] = None
      comment_type: Optional[str] = None
      comment_line_included = False
      if headers is None:
        merged_headers: OrderedDict[str, str] = ordereddict()
      else:
        merged_headers = ordereddict(headers)
        if mime_type is None:
          mime_type = headers.pop('Content-Type', None)
      if mime_type is None and is_yaml:
        mime_type = 'text/cloud-config'   # For YAML docs we assume they are cloud-config unless explicitly other
      if mime_type is None:
        parts = content.split('\n', 1)
        if len(parts) < 2:
          raise XPulumiError(f"UserDataPart has no mime type and content has no header line: {parts[0]}")
        if parts[0].startswith('#'):
          comment_line = parts[0]
          comment_type = comment_line
          if comment_type.startswith("#!"):
            comment_type = "#!"
          part_type = comment_to_user_data_part_type.get(comment_type, None)
          if part_type is None:
            raise XPulumiError(f"Unrecognided UserData comment tagline: {parts[0]}")
          mime_type = part_type.mime_type
          if comment_type == "#!":    # shebang comments must be left in the document even if mime is used
            comment_line_included = True
          else:
            content = parts[1]
        elif parts[0].startswith('MIME-Version:') or parts[0].startswith('Content-Type:'):
          content, embedded_headers = self.extract_headers(content)
          mime_type = embedded_headers.pop('Content-Type')
          if mime_type is None:
            raise XPulumiError(f"UserDataPart has Content-Type header: {embedded_headers}")
          if mime_type in [
                'x-shellscript',
                'x-shellscript-per-boot',
                'x-shellscript-per-instance',
                'x-shellscript-per-once' ]:
            comment_type = "#!"
            comment_line = content.split('\n', 1)[0]
            if not comment_line.startswith('#!'):
              raise XPulumiError(f"Content-Type \"{mime_type}\" requires shebang on first line of content: {comment_line}")
            comment_line_included = True
          else:
            part_type = mime_to_user_data_part_type.get(mime_type, None)
          if not part_type is None:
            comment_type = part_type.comment_line
            comment_line = comment_type
          merged_headers.update(embedded_headers)

      mime_version: Optional[str] = merged_headers.pop('MIME-Version', None)
      self.content = content
      self.mime_type = mime_type
      self.mime_version = mime_version
      self.comment_type = comment_type
      self.comment_line = comment_line
      self.comment_line_included = comment_line_included
      self.headers = merged_headers

  @classmethod
  def extract_headers(cls, content: Optional[str]) -> Tuple[Optional[str], OrderedDict[str, str]]:
    if content is None:
      headers = ordereddict()
    else:
      parser = email.parser.Parser()
      msg = parser.parsestr(content, headersonly=True)
      content = msg.get_payload()
      headers = ordereddict(msg)
    return content, headers

  def render(
        self,
        include_mime_version: bool=False,
        force_mime: bool=False,
        include_from: bool=False,
      ) -> Optional[str]:
    result: Optional[str] = None
    if not self.content is None:
      if not force_mime and not self.comment_line is None:
        result = ("" if self.comment_line_included else self.comment_line + '\n') + self.content
      else:
        result: str = f"Content-type: {self.mime_type}\n"
        if include_mime_version:
          mime_version = "1.0" if self.mime_version is None else self.mime_version
          result += f"Content-type: {self.mime_type}\n"
        for k,v in self.headers:
          if include_from or k != 'From':
            result += f"{k}: {v}\n"
        result += '\n'
        result += self.content
      if result != '' and not result.endswith('\n'):
        result += '\n'
    return result

class UserDataPart:
  content: Input[Optional[Union[str, JsonableDict, SyncUserDataPart]]]
  mime_type: Input[Optional[str]]
  headers: Input[Optional[Union[Dict[str, str], List[Tuple[Input[str], Input[str]]], OrderedDict[str, str]]]]
  sync_part: Output[SyncUserDataPart]

  def __init__(
        self,
        content: Input[Optional[Union[str, JsonableDict, SyncUserDataPart]]],
        mime_type: Input[Optional[str]]=None,
        headers: Input[Optional[Union[Dict[str, str], List[Tuple[Input[str], Input[str]]], OrderedDict[str, str]]]]=None):
    self.content = content
    self.mime_type = mime_type
    self.headers = headers
    self.sync_part = Output.all(content, mime_type, headers).apply(
              lambda args: self._resolve_sync_part(*args)
      )

  def _resolve_sync_part(
        self,
        content: Optional[Union[str, JsonableDict, SyncUserDataPart]],
        mime_type: Optional[str],
        headers: Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]]
      ) -> SyncUserDataPart:
    result: SyncUserDataPart
    if isinstance(content, SyncUserDataPart):
      result = content
    else:
      result = SyncUserDataPart(content, mime_type=mime_type, headers=headers)
    return result

  def render(
        self,
        include_mime_version: Input[bool]=False,
        force_mime: Input[bool]=False,
        include_from: Input[bool]=False,
      ) -> Output[Optional[str]]:
    result: Output[str] = Output.all(self.sync_part, include_mime_version, force_mime, include_from).apply(
        lambda args: args[0].render(include_mime_version=args[1], force_mime=args[2], include_from=args[3])
      )
    return result


class SyncUserData:
  parts: List[SyncUserDataPart]

  def __init__(self):
    self.parts = []

  def add(self, part: Optional[Union[SyncUserDataPart, str, JsonableDict]]):
    if not part is None:
      if not isinstance(part, SyncUserDataPart):
        part = SyncUserDataPart(part)
      if not part.content is None:
        self.parts.append(part)

  def render(self, include_mime_version: bool=False) -> Optional[str]:
    result: Optional[str]
    if len(self.parts) == 0:
      result = None
    elif len(self.parts) == 1:
      result = self.parts[0].render(include_mime_version=True)
    else:
      rendered_parts = [ part.render(force_mime=True) for part in self.parts ]

      # Find a unique boundary string that is not in any of the rendered parts
      unique = 0
      while True:
        boundary = f"@@{unique}@@"
        for rp in rendered_parts:
          assert not rp is None
          if boundary in rp:
            break
        else:
          break
        unique += 1
      
      result = f'Content-Type: multipart/mixed; boundary="{boundary}"\n'
      if include_mime_version:
        result += 'MIME-Version: 1.0\n'
      result += '\n'
      for rp in rendered_parts:
        result += f"--{boundary}\n{rp}"
      result += f"--{boundary}--\n"

    return result

  def render_binary(self, include_mime_version: bool=False) -> Optional[bytes]:
    content = self.render(include_mime_version=include_mime_version)
    bcontent = content.encode('utf-8')
    if len(bcontent) >= 16383:
      buff = BytesIO()
      # NOTE: we use a fixed modification time when zipping so that the resulting compressed data is
      # always the same for a given input. This prevents Pulumi from unnecessarily replacing EC2 instances
      # because it looks like user_data changed when it really did not.
      with gzip.GzipFile(None, 'wb', compresslevel=9, fileobj=buff, mtime=GZIP_FIXED_MTIME) as g:
        g.write(bcontent)
      compressed = buff.getvalue()
      if len(compressed) > 16383:
        raise XPulumiError(f"EC2 user_data too big: {len(bcontent)} before compression, {len(compressed)} after")
      bcontent = compressed
    return bcontent

  def render_base64(self, include_mime_version: bool=False) -> Optional[str]:
    bcontent = self.render_binary(include_mime_version=include_mime_version)
    b64 = b64encode(bcontent).decode('utf-8')
    return b64

class UserData:
  parts: List[UserDataPart]

  def __init__(self):
    parts = []

  def add(
        self,
        part: Union[UserDataPart, Input[Optional[Union[str, JsonableDict, SyncUserDataPart]]]]
      ) -> None:
    self.parts = []
    if not part is None:
      if not isinstance(part, UserDataPart):
        part = UserDataPart(part)
      self.parts.append(part)

  def render(self, include_mime_version: Input[bool]=False) -> Optional[str]:
    sync_parts = [x.sync_part for x in self.parts]
    result: Output[Optional[str]] = Output.all(include_mime_version, *sync_parts).apply(
        lambda args: self._sync_render(args[0], args[1:])
      )
    return result

  def render_binary(self, include_mime_version: Input[bool]=False) -> Optional[bytes]:
    sync_parts = [x.sync_part for x in self.parts]
    result: Output[Optional[bytes]] = Output.all(include_mime_version, *sync_parts).apply(
        lambda args: self._sync_render_binary(args[0], args[1:])
      )
    return result

  def render_base64(self, include_mime_version: Input[bool]=False) -> Optional[str]:
    sync_parts = [x.sync_part for x in self.parts]
    result: Output[Optional[str]] = Output.all(include_mime_version, *sync_parts).apply(
        lambda args: self._sync_render_base64(args[0], args[1:])
      )
    return result

  def _sync_render(self, include_mime_version: bool, parts: List[SyncUserDataPart]) -> Optional[str]:
    sync_user_data = SyncUserData()
    for part in parts:
      sync_user_data.add(part)
    result = sync_user_data.render(include_mime_version=include_mime_version)
    return result

  def _sync_render_binary(self, include_mime_version: bool, parts: List[SyncUserDataPart]) -> Optional[bytes]:
    sync_user_data = SyncUserData()
    for part in parts:
      sync_user_data.add(part)
    result = sync_user_data.render_binary(include_mime_version=include_mime_version)
    return result

  def _sync_render_base64(self, include_mime_version: bool, parts: List[SyncUserDataPart]) -> Optional[str]:
    sync_user_data = SyncUserData()
    for part in parts:
      sync_user_data.add(part)
    result = sync_user_data.render_base64(include_mime_version=include_mime_version)
    return result

SyncUserDataConvertible = Optional[
          Union[
              SyncUserData,
              SyncUserDataPart,
              str, 
              JsonableDict,
            ]
        ]
        
UserDataConvertible = Optional[
    Union[
        UserData,
        UserDataPart,
        Input[Optional[Union[str, JsonableDict, SyncUserData, SyncUserDataPart]]]
      ]
  ]

def sync_render_user_data_base64(
      content: SyncUserDataConvertible,
      include_mime_version: bool=False,
    ) -> Optional[str]:
  user_data: SyncUserData
  if isinstance(content, SyncUserData):
    user_data = content
  else:
    user_data = SyncUserData()
    if not content is None:
      user_data.add(content)
  result = user_data.render_base64(include_mime_version=include_mime_version)
  return result

def render_user_data_base64(
      content: UserDataConvertible,
      include_mime_version: Input[bool]=False,
    ) -> Output[Optional[str]]:
  user_data: UserData
  if isinstance(content, UserData):
    user_data = content
  else:
    user_data = UserData()
    user_data.add(content)
  result = user_data.render_base64(include_mime_version=include_mime_version)
  return result
