#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Utilities to assist in constructing well-formed user-data blocks to be
processed by cloud-init.

See https://cloudinit.readthedocs.io/en/latest/topics/format.html for
details on the format of the cloud-init user-data block.

cloud-init and user-data are commonly used by cloud infrastructure
services (e.g., AWS EC2) to give a way for the user to force a
newly provisioned cloud VM to initialize itself on first boot. For
example, you can provide a command script to run, or you can specify
a list of packages to be installed, or user accounts to be created.

The specification for user-data is quite rich, and it is even possible
to embed multiple independent initialization documents in a single
user-data block. This is achieved with multi-part MIME encoding.

Some user-data parts may include structured data, encoded as YAML.

The classes and functions in this module take care of all the formatting
and rendering, and conversion from structured Jsonable data to YAML. In
addition, if the resulting binary data exceeds 16KB (the limit for cloud-init
user-data), the block will be compressed with GZIP in an attempt to fit
it into the allowed maximum size.

Once you have built and rendered a user-data block, you pass it to
the cloud services provider at VM creation time (generally as
a base64-encoded string). For example, if you are using boto3 to
drive AWS ec2, you might say:

import boto3
from cloud_init_data import CloudInitData

ec2 = boto3.client('ec2')

user_data = CloudInitData()
user_data.add('''#boothook\n#!/bin/bash\necho "running boot-hook now" > /var/log/boothook.log''')

cloud_cfg = dict(
    groups = [
        {
            'ubuntu': [ 'root', 'sys' ]
          },
        'cloud-users'
      ]
  )
user_data.add(cloud_cfg)  # will be rendered as yaml with MIME type text/cloud-config

resp = ec2.run_instances(
    ...
    UserData=user_data.render_base64()
    ...
  )

"""
from base64 import b64encode
from typing import Optional, List, Union, Tuple, Dict, OrderedDict, Iterable, Any

import yaml
from io import BytesIO
import gzip
import email.parser
from collections import OrderedDict as ordereddict

# Note: recursive type hints are not allowed by mypy so this is simplified a bit
Jsonable = Union[str, int, float, bool, None, Dict[str, Any], List[Any]]
"""A Type hint for a simple JSON-serializable value; i.e., str, int, float, bool, None, Dict[str, Jsonable], List[Jsonable]"""

JsonableDict = Dict[str, Jsonable]
"""A type hint for a simple JSON-serializable dict; i.e., Dict[str, Jsonable]"""

class CloudInitDataError(Exception):
  pass

GZIP_FIXED_MTIME: float = 0.0
"""A fixed mktime() value that is used for the timestamp when gzipping cloud-init data.
   This makes the rendered data deterministic and stable, which helps keep infrastructure
   automation tools like terraform and Pulumi from needlessly updating cloud instances. """

class _CloudInitDataPartType:
  """
  A descriptor that correlates a MIME type with it's associated cloud-init comment
  header line; e.g., "Content-Type: text/cloud-config" with "#cloud-config". This
  is used by the renderer to pick the optimal rendering of the part.
  """

  mime_type: str
  """The full MIME type; e.g., 'text/cloud-boothook'"""
  mime_subtype: str

  comment_tag: Optional[str]=None
  """The portion of comment_line after '#'. For '#!', this is just '!', and does not include the
     script commandline. If None, there is no comment header associated with the MIME type."""

  comment_line: Optional[str]=None
  """The portion of the comment header that identifies its MIME type. For '#!', this is just '!#', and does not include the
     script commandline. If None, there is no comment header associated with the MIME type."""

  def __init__(self, mime_subtype: str, comment_tag: Optional[str]=None):
    """Construct a descriptor mapping a MIME type to a comment tag

    Args:
        mime_subtype (str): The MIME type without the leading "text/"
        comment_tag (Optional[str], optional):
                            The comment tag without the leading "#", or None
                            if there is no comment header associated with the
                            MIME type. For shebang types, this is just "!".
                            Defaults to None.
    """
    self.mime_subtype = mime_subtype
    self.mime_type = 'text/' + mime_subtype
    self.comment_tag = comment_tag
    self.comment_line = None if comment_tag is None else '#' + comment_tag

_part_type_list: List[_CloudInitDataPartType] = [
    _CloudInitDataPartType('cloud-boothook', 'boothook'),                    # A script with a shebang header
    _CloudInitDataPartType('cloud-config', 'cloud-config'),                  # A YAML doc with rich config data
    _CloudInitDataPartType('cloud-config-archive', 'cloud-config-archive'),  # a YAML doc that contains a list of docs, like multipart mime
    _CloudInitDataPartType('cloud-config-jsonp', 'cloud-config-jsonp'),      # fine-grained merging with vendor-provided cloud-config
    _CloudInitDataPartType('jinja2', "# template: jinja"),                   # expand jinja2 template. 2nd line is comment describing actual part type
    _CloudInitDataPartType('part-handler', 'part-handler'),                  # part contains python code that can process custom mime types for subsequent parts
    _CloudInitDataPartType('upstart-job', 'upstart-job'),                    # content plated into a file under /etc/init, to be consumed by upstart
    _CloudInitDataPartType('x-include-once-url', 'include-once'),            # List of urls that are read one at a time and processed as any item, but only once
    _CloudInitDataPartType('x-include-url', 'include'),                      # list of urls that are read one at a time and processed as any item
    _CloudInitDataPartType('x-shellscript', '!'),                            # simple userdata shell script (comment line has variable chars)
    _CloudInitDataPartType('x-shellscript-per-boot'),                        # shell script run on every boot
    _CloudInitDataPartType('x-shellscript-per-instance'),                    # shell script run once per unique instance
    _CloudInitDataPartType('x-shellscript-per-once'),                        # shell script run only once
  ]
"""A list of MIME types that are pre-known to cloud-init"""

mime_to_cloud_init_data_part_type: Dict[str, _CloudInitDataPartType] = dict((x.mime_type, x) for x in _part_type_list)
"""A map from full MIME type to associated _CloudInitDataPartType"""

comment_to_cloud_init_data_part_type: Dict[str, _CloudInitDataPartType] = dict((x.comment_line, x) for x in _part_type_list)
"""A map from comment header line (Just "#!" for shebang lines) to associated _CloudInitDataPartType"""

class CloudInitRenderable:
  """Abstract base class for cloud-init renderable items"""

  def is_null_content(self) -> bool:
    """Return True if this is a null document

    Returns:
        bool: True if rendering this document will return None
    """
    raise NotImplementedError("Subclass of CloudInitRenderable must implement is_null_content()")

  def render(
        self,
        include_mime_version: bool=True,
        force_mime: bool=False,
        include_from: bool=False,
      ) -> Optional[str]:
    """Renders the single cloudinit part to a string suitable for passing
       to cloud-init directly or for inclusion in a multipart document.

    Args:
        include_mime_version (bool, optional):
                        True if a MIME-Version header should be included.
                        Ignored if comment-style headers are selected. Note
                        that cloud-init REQUIRES this header for the outermost
                        MIME document. For embedded documents in a multipart
                        MIME it is optional. Defaults to True.
        force_mime (bool, optional): If True, MIME-style headers will be used.
                        By default, a comment-style header will be used if there
                        is an appropriate one for this part's MIME type.
                        Defaults to False.
        include_from (bool, optional): If True, any 'From' header associated with
                        the part will be included; otherwise it will be stripped.
                        Defaults to False.

    Returns:
        Optional[str]: The part rendered as a string suitable for passing to cloud-init
                       directly or for inclusion in a multipart document, or None if
                       this is a null part that should be stripped from final rendering.
    """
    raise NotImplementedError("Subclass of CloudInitRenderable must implement render()")

class CloudInitDataPart(CloudInitRenderable):
  """A container for a single part of a potentially multi-part cloud-init document"""

  content: Optional[str]
  """The string representation of the part's content, which is interpreted differently depending
     on its type, or None if this is a "null" part, which will be stripped from the
     final document rendering. This string does NOT include any MIME headers associated
     with the document, and except in the case of shebang comment headers (e.g.,
     "#!/bin/bash"), does NOT include the comment header. For YAML parts, this is the
     rendered YAML text. """

  mime_type: str
  """The full MIME type of the part; e.g., "text/cloud-config". """

  mime_version: Optional[str] = None
  """The MIME version, as pulled from the MIME-Version header. If None, "1.0" is assumed."""

  comment_line: Optional[str] = None
  """The full comment line associated with the part. For shebang-style parts this is
     the entire shebang line; e.g., "#!/bin/bash". If None, there is not comment header
     associated with the part's MIME type. If comment_line_included is true, then this line is also
     present in content; otherwise it has been stripped from content."""

  comment_type: Optional[str] = None
  """The portion of comment_line that identifies the part type. For shebang types, this
     is "#!". For all other types this is the same as comment_line. If None, there is
     no comment header associated with the part's MIME type."""

  comment_line_included: bool = False
  """If True, the part is a shebang-style part, and the full shebang line is included
     as the first line in content; otherwise any identifying comment line has been stripped."""

  headers: OrderedDict[str, str]
  """An ordered dictionary of MIME headers associated with the part. MIME-Version and
     Content-Type are explicitly removed from this mapping during construction."""

  def __init__(
        self,
        content: Optional[Union[str, JsonableDict, 'CloudInitDataPart']],
        mime_type: Optional[str]=None,
        headers: Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]]=None):
    """Create a container for a single part of a potentially multi-part cloud-init document

    Args:
        content (Optional[Union[str, JsonableDict, CloudInitDataPart]]): 
                            The content to be rendered for the part. If mime_type is None, this can be:
                               1. None, indicating this is a null part that will be stripped from the final
                                  document.
                               2. A string beginning with "#". The first line is interpreted as
                                  a cloud-init comment header that identifies the type. The remainder
                                  becomes the content of the part (for shebang-style parts the comment
                                  line is also left in the part's content).
                               3. A string beginning with "Content-Type:" or "MIME-Version:". The string
                                  is parsed as a MIME document with embedded headers. The headers in the
                                  document are merged with and override any headers passed to this constructor.
                                  The MIME type of the part is obtained from the "Content-Type" header, and
                                  the payload becomes the part's content.
                               4. A JsonableDict. The content is converted to YAML and the MIME type is
                                  set to "text/cloud-config". This is a common type of input to cloud-init.
                               5. Another CloudInitDataPart. In this case, a simple clone is created.
                            If mime_type is not None, then content may be:
                               1. A string. The string will be used as the content of the part without further
                                  interpretation.
                               2. A JsonableDict. The dict is converted to YAML, and the YAML string is used
                                  as the part's content.

        mime_type (Optional[str], optional):
                            The full MIME type of the part, or None to infer the MIME type from the
                            content argument, as described above. Defaults to None.

        headers (Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]], optional):
                            An optional ordered dict of MIME headers to associate with the part. Content-Type
                            and MIME-Version are explicitly removed from this dict and handled specially. Any
                            additional headers will be included in the rendering of this part if MIME
                            rendering is selected. If comment-header rendering is selected, the headers are
                            discarded. Defaults to None.

    Raises:
        CloudInitDataError: An error occured building the part
    """
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
          raise CloudInitDataError(f"CloudInitDataPart has no mime type and content has no header line: {parts[0]}")
        if parts[0].startswith('#'):
          comment_line = parts[0]
          comment_type = comment_line
          if comment_type.startswith("#!"):
            comment_type = "#!"
          part_type = comment_to_cloud_init_data_part_type.get(comment_type, None)
          if part_type is None:
            raise CloudInitDataError(f"Unrecognided CloudInitData comment tagline: {parts[0]}")
          mime_type = part_type.mime_type
          if comment_type == "#!":    # shebang comments must be left in the document even if mime is used
            comment_line_included = True
          else:
            content = parts[1]
        elif parts[0].startswith('MIME-Version:') or parts[0].startswith('Content-Type:'):
          content, embedded_headers = self.extract_headers(content)
          mime_type = embedded_headers.pop('Content-Type')
          if mime_type is None:
            raise CloudInitDataError(f"CloudInitDataPart has Content-Type header: {embedded_headers}")
          merged_headers.update(embedded_headers)
          
      if mime_type in [
            'x-shellscript',
            'x-shellscript-per-boot',
            'x-shellscript-per-instance',
            'x-shellscript-per-once' ]:
        comment_type = "#!"
        if comment_line is None:
          comment_line = content.split('\n', 1)[0]
        if not comment_line.startswith('#!'):
          raise CloudInitDataError(f"Content-Type \"{mime_type}\" requires shebang on first line of content: {comment_line}")
        comment_line_included = True
      else:
        part_type = mime_to_cloud_init_data_part_type.get(mime_type, None)
        if not part_type is None:
          comment_type = part_type.comment_line
          comment_line = comment_type

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
    """Parses MIME headers and payload from a MIME document

    Args:
        content (Optional[str]): MIME document to be parsed, or None for a null document

    Returns:
        Tuple[Optional[str], OrderedDict[str, str]]: A tuple containing:
            [0]: The document payload, or None for a null document
            [1]: an OrderedDict containing the headers. Empty for a null document.
    """
    if content is None:
      headers = ordereddict()
    else:
      parser = email.parser.Parser()
      msg = parser.parsestr(content, headersonly=True)
      content = msg.get_payload()
      headers = ordereddict(msg)
    return content, headers

  def is_null_content(self) -> bool:
    """Return True if this is a null document

    Returns:
        bool: True if rendering this document will return None
    """
    return self.content is None

  def render(
        self,
        include_mime_version: bool=True,
        force_mime: bool=False,
        include_from: bool=False,
      ) -> Optional[str]:
    """Renders the single cloudinit part to a string suitable for passing
       to cloud-init directly or for inclusion in a multipart document.

    Args:
        include_mime_version (bool, optional):
                        True if a MIME-Version header should be included.
                        Ignored if comment-style headers are selected. Note
                        that cloud-init REQUIRES this header for the outermost
                        MIME document. For embedded documents in a multipart
                        MIME it is optional. Defaults to True.
        force_mime (bool, optional): If True, MIME-style headers will be used.
                        By default, a comment-style header will be used if there
                        is an appropriate one for this part's MIME type.
                        Defaults to False.
        include_from (bool, optional): If True, any 'From' header associated with
                        the part will be included; otherwise it will be stripped.
                        Defaults to False.

    Returns:
        Optional[str]: The part rendered as a string suitable for passing to cloud-init
                       directly or for inclusion in a multipart document, or None if
                       this is a null part that should be stripped from final rendering.
    """
    result: Optional[str] = None
    if not self.content is None:
      if not force_mime and not self.comment_line is None:
        result = ("" if self.comment_line_included else self.comment_line + '\n') + self.content
      else:
        result: str = f"Content-Type: {self.mime_type}\n"
        if include_mime_version:
          mime_version = "1.0" if self.mime_version is None else self.mime_version
          result += f"MIME-Version: {mime_version}\n"
        for k,v in self.headers:
          if include_from or k != 'From':
            result += f"{k}: {v}\n"
        result += '\n'
        result += self.content
      #if result != '' and not result.endswith('\n'):
      #  result += '\n'
    return result

CloudInitDataConvertible = Optional[
          Union[
              'CloudInitData',
              CloudInitDataPart,
              str,
              bytes,
              JsonableDict,
            ]
        ]

class CloudInitData(CloudInitRenderable):
  """
  A container for a complete cloud-init user-data document, which may consist of
  zero or more CloudInnitDataPart's, or can be a raw bytes value to
  be passed directly to cloud-init.
  """

  parts: List[CloudInitRenderable]
  """If raw_binary is None, a List of renderable parts to be rendered. An empty list
     indicates an empty/null user-data document. A list with a single item
     results in that item being directly rendered. A list with more than
     one item is rendered as a multipart MIME document.  Ignored if
     raw_binary is not None."""

  raw_binary: Optional[bytes]=None
  """If not None, a raw binary encoding of the entire user-data document,
     which can be passed directly to cloud-init. This field exists only
     so that users can choose to render user-data themselves, and still
     pass the result to an API that expects CloudInitData."""

  def __init__(
        self,
        content: Optional[Union[str, bytes, JsonableDict, CloudInitRenderable]]=None,
        mime_type: Optional[str]=None,
        headers: Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]]=None,
        raw_binary: Optional[bytes]=None
      ):
    """Create a container for a complete cloud-init user-data document,
       which may consist of zero or more CloudInnitDataPart's, or can
       be a raw bytes value to be passed directly to cloud-init.

    Args:
        content (Optional[Union[str, bytes, JsonableDict, CloudInitDataPart]], optional):
           If None, an empty document is created--parts can be added before rendering with add().
           If another CloudInitData object, creates a clone of that object.
           If a bytes value, then this parameter is directly used for final binary
           rendering of the document (This option exists only
           so that users can choose to render user-data themselves, and still
           pass the result to an API that expects CloudInitData).
           Otherwise, causes a single part to be immediately added to the document
           as if add() had been called.  Included for convenience in creating single-part
           documents, which is common.  Defaults to None.
        mime_type (Optional[str], optional):
           If content is not None and not bytes, as described for add(). Ignored if content is None. Defaults to None.
        headers (Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]], optional):
           If content is not None and not bytes, as described for add(). Ignored if content is None. Defaults to None.

    Raises:
        CloudInitDataError: An error occured building the first part of the document.
    """
    self.parts = []
    if not content is None:
      if isinstance(content, CloudInitData):
        self.parts = content.parts[:]
        self.raw_data = content.raw_data
      elif isinstance(content, bytes):
        if len(content) > 16383:
          raise CloudInitDataError(f"raw binary user data too big: {len(content)}")
        self.raw_binary = content
      else:
        self.add(content, mime_type=mime_type, headers=headers)

  def add(self,
        content: Optional[Union[CloudInitRenderable, str, JsonableDict]],
        mime_type: Optional[str]=None,
        headers: Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]]=None):
    """Add a single renderable part of a potentially multi-part cloud-init document.

    Args:
        content (Optional[Union[CloudInitDataPart, str, JsonableDict]]): 
                            The content to be rendered for the part. If mime_type is None, this can be:
                               1. None, indicating this is a null part. No action will be taken.
                               2. A string beginning with "#". The first line is interpreted as
                                  a cloud-init comment header that identifies the type. The remainder
                                  becomes the content of the part (for shebang-style parts the comment
                                  line is also left in the part's content).
                               3. A string beginning with "Content-Type:" or "MIME-Version:". The string
                                  is parsed as a MIME document with embedded headers. The headers in the
                                  document are merged with and override any headers passed to this constructor.
                                  The MIME type of the part is obtained from the "Content-Type" header, and
                                  the payload becomes the part's content.
                               4. A JsonableDict. The content is converted to YAML and the MIME type is
                                  set to "text/cloud-config". This is a common type of input to cloud-init.
                               5. A CloudInitRenderable that has already been initialized. The item is
                                  directly added as a part.
                            If mime_type is not None, then content may be:
                               1. A string. The string will be used as the content of the part without further
                                  interpretation.
                               2. A JsonableDict. The dict is converted to YAML, and the YAML string is used
                                  as the part's content.

        mime_type (Optional[str], optional):
                            The full MIME type of the part, or None to infer the MIME type from the
                            content argument, as described above. Defaults to None.

        headers (Optional[Union[Dict[str, str], Iterable[Tuple[str, str]], OrderedDict[str, str]]], optional):
                            An optional ordered dict of MIME headers to associate with the part. Content-Type
                            and MIME-Version are explicitly removed from this dict and handled specially. Any
                            additional headers will be included in the rendering of this part if MIME
                            rendering is selected. If comment-header rendering is selected, the headers are
                            discarded. Defaults to None.

    Raises:
        CloudInitDataError: An attempt was made to add a part to a document that was created with raw_binary
        CloudInitDataError: An error occured building the part
    """
    if not content is None:
      if not self.raw_binary is None:
        raise CloudInitDataError(f"Cannot add parts to CloudInitData initialized with raw binary payload")
      if not isinstance(content, CloudInitRenderable):
        content = CloudInitDataPart(content, mime_type=mime_type, headers=headers)
      if not content.is_null_content():
        self.parts.append(content)

  def is_null_content(self) -> bool:
    """Return True if this is a null document

    Returns:
        bool: True if rendering this document will return None
    """
    return self.raw_binary is None and len(self.parts) ==0

  def render(
        self,
        include_mime_version: bool=True,
        force_mime: bool=False,
        include_from: bool=False
      ) -> Optional[str]:
    """Renders the entire cloudinit user-data document to a string suitable for passing
       to cloud-init directly. For single-part documents, renders them directly. For
       multi-part documents, wraps the parts in a multipart MIME encoding.

    Args:
        include_mime_version (bool, optional):
                        True if a MIME-Version header should be included.
                        Ignored if a single-part document and comment-style
                        headers are selected. Note that cloud-init REQUIRES
                        this header for the outermost MIME document, so for
                        compatibility it should be left at True. Defaults to True.
        force_mime (bool, optional): If True, MIME-style headers will be used.
                        By default, a comment-style header will be used if this
                        is a single-part document and there is an appropriate
                        comment header for for the single part's MIME type.
                        Defaults to False.
        include_from (bool, optional): If True, any 'From' header associated with
                        the part will be included; otherwise it will be stripped.
                        Defaults to False. This parameter is included as part of
                        CloudInitRenderable interface, but it has no effect on
                        CloudInitData.

    Returns:
        Optional[str]: The entire document rendered as a string suitable for passing to cloud-init
                       directly, or None if this is a null/empty document (with zero parts). If
                       raw_binary was provided at construction time, then that value
                       is simply decoded as UTF-8.
    """
    result: Optional[str]
    if self.raw_binary is None:
      if not len(self.parts) > 0 and not include_mime_version:
        raise CloudInitDataError("include_mime_version MUST be True for the outermost cloud_init_data part")
      if len(self.parts) == 0:
        result = None
      elif len(self.parts) == 1:
        result = self.parts[0].render(include_mime_version=include_mime_version, force_mime=force_mime)
      else:
        # Parts of a multi-part document are forced into MIME mode
        rendered_parts = [ part.render(force_mime=True, include_mime_version=False) for part in self.parts ]

        # Find a unique boundary string that is not in any of the rendered parts
        unique = 0

        while True:
          boundary = f'::{unique}::'
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
          result += f"--{boundary}\n{rp}\n"
        result += f"--{boundary}--\n"
    else:
      result = self.raw_binary.decode('utf-8')

    return result

  def render_binary(self, include_mime_version: bool=True) -> Optional[bytes]:
    """Renders the entire cloudinit user-data document to a binary bytes buffer suitable for passing
       to cloud-init directly. For single-part documents, renders them directly. For
       multi-part documents, wraps the parts in a multipart MIME encoding.

    Args:
        include_mime_version (bool, optional):
                        True if a MIME-Version header should be included.
                        Ignored if a single-part document and comment-style
                        headers are selected. Note that cloud-init REQUIRES
                        this header for the outermost MIME document, so for
                        compatibility it should be left at True. Defaults to True.
        force_mime (bool, optional): If True, MIME-style headers will be used.
                        By default, a comment-style header will be used if this
                        is a single-part document and there is an appropriate
                        comment header for for the single part's MIME type.
                        Defaults to False.

    Returns:
        Optional[bytes]: The entire document rendered as a UTF-8-encoded bytes suitable for passing to cloud-init
                       directly, or None if this is a null/empty document (with zero parts). If
                       raw_binary was provided at construction time, then that value
                       is directly returned.
    """
    if self.raw_binary is None:
      content = self.render(include_mime_version=include_mime_version)
      bcontent = content.encode('utf-8')
      if len(bcontent) >= 16383:
        buff = BytesIO()
        # NOTE: we use a fixed modification time when zipping so that the resulting compressed data is
        # always the same for a given input. This prevents Pulumi from unnecessarily replacing EC2 instances
        # because it looks like cloud_init_data changed when it really did not.
        with gzip.GzipFile(None, 'wb', compresslevel=9, fileobj=buff, mtime=GZIP_FIXED_MTIME) as g:
          g.write(bcontent)
        compressed = buff.getvalue()
        if len(compressed) > 16383:
          raise CloudInitDataError(f"EC2 cloud_init_data too big: {len(bcontent)} before compression, {len(compressed)} after")
        bcontent = compressed
    else:
      bcontent = self.raw_binary
    return bcontent

  def render_base64(self, include_mime_version: bool=True) -> Optional[str]:
    """Renders the entire cloudinit user-data document to a base-64 encoded binary block suitable for passing
       to cloud-init directly. For single-part documents, renders them directly. For
       multi-part documents, wraps the parts in a multipart MIME encoding.

    Args:
        include_mime_version (bool, optional):
                        True if a MIME-Version header should be included.
                        Ignored if a single-part document and comment-style
                        headers are selected. Note that cloud-init REQUIRES
                        this header for the outermost MIME document, so for
                        compatibility it should be left at True. Defaults to True.
        force_mime (bool, optional): If True, MIME-style headers will be used.
                        By default, a comment-style header will be used if this
                        is a single-part document and there is an appropriate
                        comment header for for the single part's MIME type.
                        Defaults to False.

    Returns:
        Optional[str]: The entire document rendered as a base-64 encoded binary block suitable
                       for passing to cloud-init directly, or None if this is a null/empty
                       document (with zero parts). If raw_binary was provided at construction
                       time, then that value is simply encoded with base-64.
    """
    bcontent = self.render_binary(include_mime_version=include_mime_version)
    b64 = b64encode(bcontent).decode('utf-8')
    return b64

        
def render_cloud_init_data_base64(
      content: CloudInitDataConvertible,
    ) -> Optional[str]:
  cloud_init_data = CloudInitData(content)
  # Note: include_mime_version is required by cloud-init for the top-level part,
  # so we don't even allow setting it to False.
  result = cloud_init_data.render_base64(include_mime_version=True)
  return result
