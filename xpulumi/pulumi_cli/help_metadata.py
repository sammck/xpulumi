#!/usr/bin/env python3

from __future__ import annotations
from audioop import add

from typing import List, Optional, TextIO, Union, Mapping, Dict, cast, Tuple, Generator, Set, TYPE_CHECKING
import os
import subprocess
import re
import json
import sys
from queue import Queue
import shlex
import tabulate

from project_init_tools import (
    get_git_root_dir,
    searchpath_prepend,
    multiline_indent,
  )
from pulumi import RunError

# This module runs with -m; do not use relative imports
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import Jsonable, JsonableDict

if TYPE_CHECKING:
  TopicQueue = Queue["TopicInfo"] # type: ignore # pylint: disable=unsubscriptable-object
else:
  TopicQueue = Queue

class OptionInfo:
  option_regex = re.compile(
      r'\A  '
      r'(((?P<short_flag>-[a-zA-Z0-9]), )|(    ))'
      r'(?P<long_flag>--[a-zA-Z0-9_.\-]+)( (?P<value_name>[a-zA-Z0-9_]+:?)(\[=(?P<default_value>[^\]]+)\])?:?)?'
      r'  \s*'
      r'(?P<description>[^ ].*)\Z',
      flags=re.MULTILINE | re.DOTALL
    )

  flags: List[str]
  value_name: Optional[str] = None
  description: str

  def __init__(self,
        flags: Optional[Union[str, List[str]]]=None,
        value_name: Optional[str] = None,
        description: Optional[str] = None,
        help_line: Optional[str]=None,
        json_data: Optional[JsonableDict] = None
      ):
    if json_data is None:
      if help_line is None:
        assert not flags is None
        if isinstance(flags, str):
          flags = [ flags ]
        assert isinstance(flags, list) and len(flags) > 0
        if description is None or description == '':
          if len(flags) <= 1:
            description = f"Option flag '{flags[0]}'"
          else:
            description = f"Option flags {flags}"
        self.flags = flags[:]
        self.value_name = value_name
        self.description = description
      else:
        m = self.option_regex.match(help_line)
        if not m:
          raise XPulumiError(f"Invalid flag description line: {json.dumps(help_line)}")
        self.flags = [ m.group('long_flag') ]
        short_flag = m.group('short_flag')
        if not short_flag is None and short_flag != '':
          self.flags.append(short_flag)
        self.description = m.group('description')
        value_name = m.group('value_name')
        if not value_name is None and value_name != '' and value_name.lower() != 'false':
          self.value_name = value_name
    else:
      assert isinstance(json_data, dict)
      self.flags = cast(List[str], json_data.get('flags', []))
      assert isinstance(self.flags, list) and all(isinstance(x, str) for x in self.flags)
      self.value_name = cast(Optional[str], json_data.get('value_name', None))
      assert self.value_name is None or isinstance(self.value_name, str)
      self.description = cast(str, json_data['description'])
      assert isinstance(self.description, str)

  @property
  def has_value(self) -> bool:
    return not self.value_name is None

  def __str__(self) -> str:
    return f"<OptionInfo(flags={self.flags}, value_name='{self.value_name}', description={json.dumps(self.description)})>"

  def as_jsonable(self) -> JsonableDict:
    result: JsonableDict = dict(flags=self.flags, description=self.description)
    if not self.value_name is None:
      result.update(value_name=self.value_name)
    return result

  def __eq__(self, other: object):
    if not isinstance(other, OptionInfo):
      return False
    if other is self:
      return True
    return other.description == self.description and other.value_name == self.value_name and other.flags == self.flags

  def __ne__(self, other: object):
    return not self.__eq__(other)

  def clone(self) -> OptionInfo:
    return OptionInfo(flags=self.flags, value_name=self.value_name, description=self.description)

class TopicInfo:
  subcmd_regex = re.compile(r'^  (?P<subcmd_name>[a-zA-Z0-9\-]+)\s+(?P<subcmd_description>[^ ].*)$')
  cmd_category_regex = re.compile(r'^(?P<category_name>[^ ][a-zA-Z0-9\- ]*) Commands:$')

  metadata: 'PulumiMetadata'
  parent: Optional['TopicInfo']
  parent_description: Optional[str]
  subcmds: List[str]
  title: str
  detailed_description: str
  usage: str
  category: str

  aliases: List[str]
  """short command aliases for this subcommand. Affects the parent's merged_subtopics"""

  subtopics: Dict[str, 'TopicInfo']
  """Mapping of short subcommand name to subtopic (does not include child aliases)"""

  merged_subtopics: Dict[str, 'TopicInfo']
  """Mapping of subcommand short name to subtopic (includes child aliases)"""

  added_options: Dict[str, OptionInfo]
  """For options defined at the current subcommand, a map from flag ('-f' or '--flag') to
     OptionInfo. Includes both persistent and nonpersistent options. The same OptionInfo
     may appear twice, once each for the short and long flag names. """

  added_persistent_options: Dict[str, OptionInfo]
  """The subset of added_options that is persistent. Represents persistent_options that
     were added by this subcommand--not inherited from the parent."""

  #persistent_options: Dict[str, OptionInfo]
  #"""The parent's persistent options, if any, merged with added_persistent_options.
  #   Represents all persistent options available for use in this subcommand"""

  #options: Dict[str, OptionInfo]
  #"""Persistent_options merged with added_options. Represents all options available
  #   in this subcommand"""

  _inherited_option_list: List[OptionInfo]
  """Persistent options inherited from the parent or previous generation. provided as "Global Flags"
     in Pulumi help output. Not used at runtime, but temporarily used during metadata creation
     from pulumi help, to determine which options on the parent are persistent. Undefined
     when loaded from cache."""

  epilog: str
  """Any help text that follows everything else in the help output"""

  topic_path: List[TopicInfo]
  """A list of TopicInfo objects starting with the main topic and ending with this topic,
     representing the hierarchical tree path to this topic"""

  def __init__(
        self,
        metadata: 'PulumiMetadata',
        subcmd: Optional[Union[str, List[str]]] = None,
        parent: Optional['TopicInfo'] = None,
        parent_description: Optional[str] = None,
        json_data: Optional[JsonableDict] = None,
        category: Optional[str] = None
      ):
    self.metadata = metadata
    self.parent = parent
    self.subcmds = metadata.normalize_subcmd(subcmd)
    #self.persistent_options = {}
    self.added_persistent_options = {}
    self.added_options = {}
    #self.options = {}
    self.topic_path = self._gen_topic_path()

    if not json_data is None:
      self._init_from_json_data(json_data)
      return

    self.parent_description = parent_description
    self.category = category
    help_text = metadata.get_help(self.subcmds)
    lines = [ x.rstrip() for x in help_text.rstrip().split('\n') ]
    i = 0
    try:
      assert len(lines) > 2
      if lines[1] == '':
        self.title = lines[0]
        description_start = 2
      else:
        if self.parent_description is None:
          self.title = f"Subcommand '{self.full_subcmd}'"
        else:
          self.title = self.parent_description
        description_start = 0
      i = description_start
      while lines[i] != 'Usage:':
        i += 1
      assert lines[i-1] == ''
      self.detailed_description = '\n'.join(lines[description_start:i-1])
      i += 1

      usage_start = i
      while lines[i] == '' or lines[i][0] == ' ':
        i += 1
      assert lines[i-1] == ''
      self.usage = '\n'.join(lines[usage_start:i-1])

      self.aliases = []
      if lines[i] == 'Aliases:':
        i += 1
        self.aliases = [ x.strip() for x in lines[i].split(',') ]
        assert len(self.aliases) > 0
        if self.short_subcmd in self.aliases:
          self.aliases.remove(self.short_subcmd)
        i += 1
        assert lines[i] == ''
        i += 1

      self.subtopics = {}
      self.merged_subtopics = {}
      while True:
        m = self.cmd_category_regex.match(lines[i])
        if not m:
          break
        subcmd_category = m.group('category_name')
        i += 1
        while lines[i] != '':
          m = self.subcmd_regex.match(lines[i])
          if not m:
            raise RuntimeError(f"Invalid subcommand description: {lines[i]}")
          subcmd_name = m.group('subcmd_name')
          subcmd_description = m.group('subcmd_description')
          subtopic = TopicInfo(self.metadata, self.subcmds + [ subcmd_name ], parent=self, parent_description=subcmd_description, category=subcmd_category)
          assert not subcmd_name in self.merged_subtopics
          self.merged_subtopics[subcmd_name] = subtopic
          self.subtopics[subcmd_name] = subtopic
          for alias in subtopic.aliases:
            assert not alias in self.merged_subtopics
            self.merged_subtopics[alias] = subtopic

          i += 1
        i += 1

      assert lines[i] == 'Flags:'
      i += 1
      #self.added_option_list = []
      self.added_options = {}
      while i < len(lines) and lines[i] != '':
        oline = lines[i]
        while i + 1 < len(lines) and lines[i + 1].startswith("        "):
          i += 1
          oline += '\n' + lines[i].lstrip()
        optinfo = OptionInfo(help_line=oline)
        #self.added_option_list.append(optinfo)
        for flag in optinfo.flags:
          assert not flag in self.added_options
          self.added_options[flag] = optinfo
        i += 1
      self.options = dict(self.added_options)
      i += 1
      self._inherited_option_list = []
      self.inherited_option_names = set()
      if lines[i] == 'Global Flags:':
        i += 1
        while i < len(lines) and lines[i] != '':
          oline = lines[i]
          while i + 1 < len(lines) and lines[i + 1].startswith("        "):
            i += 1
            oline += '\n' + lines[i].lstrip()
          optinfo = OptionInfo(help_line=oline)
          self._inherited_option_list.append(optinfo)
          self.inherited_option_names.update(optinfo.flags)
          i += 1
        i += 1
      self.epilog = '\n'.join(lines[i:])
    except Exception as e:
      if i >= len(lines):
        raise RuntimeError(f"[{self.full_subcmd}]: Error in line {i}: {e}") from e
      raise RuntimeError(f"[{self.full_subcmd}]: Error in line {i} ({json.dumps(lines[i])}): {e}") from e
    if self.parent is None:
      # After the whole tree has been built, we can now derive persistent options:
      self._derive_added_persistent_options({})

  def gen_all_persistent_options(self) -> Dict[str, OptionInfo]:
    if self.parent is None:
      result = self.added_persistent_options
    else:
      parent_persistent_options = self.parent.gen_all_persistent_options()
      if len(self.added_persistent_options) == 0:
        result = parent_persistent_options
      else:
        result = parent_persistent_options.copy()
        result.update(self.added_persistent_options)
    return result

  def _derive_added_persistent_options(self, parent_persistent_options: Dict[str, OptionInfo]) -> None:
    added_persistent_options: Dict[str, OptionInfo] = {}
    persistent_options: Dict[str, OptionInfo] = parent_persistent_options.copy()
    for subtopic in self.subtopics.values():
      for opt in subtopic._inherited_option_list:  # pylint: disable=protected-access
        for flag in opt.flags:
          if flag in persistent_options:
            existing_opt = persistent_options[flag]
            if existing_opt != opt:
              raise RuntimeError(f"Persistent option {flag} redefined by {self.full_subcmd}; inherited={existing_opt}, new={opt}")
          else:
            added_persistent_options[flag] = opt
            persistent_options[flag] = opt
    self.added_persistent_options = added_persistent_options
    for subtopic in self.subtopics.values():
      subtopic._derive_added_persistent_options(persistent_options)  # pylint: disable=protected-access

  def _gen_topic_path(self) -> List[TopicInfo]:
    result: List[TopicInfo] = [ self ]
    if not self.parent is None:
      result = self.parent._gen_topic_path() + result  # pylint: disable=protected-access
    return result

  @property
  def main_topic(self) -> TopicInfo:
    return self.topic_path[0]

  @property
  def full_subcmd(self) -> str:
    return '<main>' if len(self.subcmds) == 0 else ' '.join(self.subcmds)

  @property
  def short_subcmd(self) -> str:
    return '<main>' if len(self.subcmds) == 0 else self.subcmds[-1]

  def topic_from_subcmds(self, subcmds: List[str]) -> TopicInfo:
    topic = self.main_topic
    for short_subcmd in subcmds:
      topic = topic.subtopics[short_subcmd]
    return topic

  def topic_from_full_subcmd(self, full_subcmd: str) -> TopicInfo:
    subcmds = [] if full_subcmd in ('', '<main>') else full_subcmd.split(' ')
    return self.topic_from_subcmds(subcmds)

  def iter_unique_added_options(self) -> Generator[OptionInfo, None, None]:
    seen: Set[int] = set()
    for option in self.added_options.values():
      oid = id(option)
      if not oid in seen:
        seen.add(oid)
        yield option

  def as_jsonable(self) -> JsonableDict:
    result: JsonableDict = dict(
        title=self.title,
        description=self.detailed_description,
        usage=self.usage,
        epilog=self.epilog,
        category=self.category,
      )
    if not self.parent_description is None:
      result.update(parent_description=self.parent_description)
    if len(self.aliases) > 0:
      result.update(aliases=self.aliases)
    added_option_jdata: List[JsonableDict] = []
    for opt in self.iter_unique_added_options():
      jdata: JsonableDict = opt.as_jsonable()
      if opt.flags[0] in self.added_persistent_options:
        jdata['persistent'] = True
      added_option_jdata.append(jdata)
    if len(added_option_jdata) > 0:
      result.update(options=added_option_jdata)
    if len(self.subtopics) > 0:
      result.update(subcommands=dict((x, y.as_jsonable()) for x, y in self.subtopics.items()))
    return result

  def update_metadata(self, metadata: 'PulumiMetadata') -> None:
    self.metadata = metadata
    for st in self.subtopics.values():
      st.update_metadata(metadata)

  def _init_from_json_data(self, json_data: JsonableDict) -> None:
    assert isinstance(json_data, dict)
    self.title = cast(str, json_data['title'])
    assert isinstance(self.title, str)
    self.parent_description = cast(Optional[str], json_data.get('parent_description', None))
    self.category = cast(Optional[str], json_data.get('category', None))
    assert self.parent_description is None or isinstance(self.parent_description, str)
    self.detailed_description = cast(str, json_data['description'])
    assert isinstance(self.detailed_description, str)
    self.usage = cast(str, json_data['usage'])
    assert isinstance(self.usage, str)
    self.epilog = cast(str, json_data['epilog'])
    assert isinstance(self.epilog, str)
    self.aliases = cast(List[str],json_data.get('aliases', []))
    assert isinstance(self.aliases, list) and all(isinstance(x, str) for x in self.aliases)
    self.added_options = {}
    self.added_persistent_options = {}
    opt_data_list = cast(List[JsonableDict], json_data.get('options', []))
    assert isinstance(opt_data_list, list)
    for opt_data in opt_data_list:
      assert isinstance(opt_data, dict)
      is_persistent = cast(bool, opt_data.pop('persistent', False))
      assert isinstance(is_persistent, bool)
      option = OptionInfo(json_data=opt_data)
      for flag in option.flags:
        assert not flag in self.added_options
        self.added_options[flag] = option
        if is_persistent:
          self.added_persistent_options[flag] = option
    # initial value for options and persistent options. will be
    # updated from parent after whole tree is loaded:
    self.options = dict(self.added_options)
    self.persistent_options = dict(self.added_persistent_options)
    self.subtopics = {}
    self.merged_subtopics = {}
    json_subcommands = cast(JsonableDict, json_data.get('subcommands', {}))
    assert isinstance(json_subcommands, dict)
    for subtopic_name, subtopic_data in json_subcommands.items():
      assert isinstance(subtopic_name, str)
      assert isinstance(subtopic_data, dict)
      subtopic = TopicInfo(
          metadata=self.metadata,
          subcmd = self.subcmds + [ subtopic_name ],
          parent = self,
          json_data=subtopic_data)
      assert not subtopic_name in self.merged_subtopics
      self.merged_subtopics[subtopic_name] = subtopic
      self.subtopics[subtopic_name] = subtopic
      for alias in subtopic.aliases:
        assert not alias in self.merged_subtopics
        self.merged_subtopics[alias] = subtopic
    if self.parent is None:
      # After the whole tree has been built, we can now derive merged persistent options:
      for topic in self.iter_subtopics(include_self=True):
        if not topic.parent is None:
          # the topic already has its own added persistent options; we
          # just need to merge in the options inherited from the parent
          topic.persistent_options.update((k, v) for k, v in topic.parent.persistent_options.items() if not k in topic.persistent_options)
          topic.options.update((k, v) for k, v in topic.persistent_options.items() if not k in topic.options)

  def add_option_info(self, opt: OptionInfo, is_persistent: bool=False) -> None:
    opt = opt.clone()
    for flag in opt.flags:
      existing_opt = self.get_option(flag, topic_path=(self.topic_path if is_persistent else None))
      if not existing_opt is None:
        raise XPulumiError(f"Option flag '{flag}' is already defined for subcmd '{self.full_subcmd}' as {existing_opt}")
    for flag in opt.flags:
      self.added_options[flag] = opt
      if is_persistent:
        self.added_persistent_options[flag] = opt

  def add_option(
        self,
        flags: Union[str, List[str]],
        has_value: Optional[bool]=None,
        description: Optional[str]=None,
        is_persistent: bool=False,
        value_name: Optional[str]=None,
      ) -> None:
    if has_value is None:
      has_value = (not value_name is None)
    assert value_name is None or has_value
    if has_value and value_name is None:
      value_name = 'string'
    opt = OptionInfo(flags, value_name=value_name, description=description)
    self.add_option_info(opt, is_persistent=is_persistent)

  def pop_option_info(
        self,
        opt: OptionInfo,
      ) -> None:
    for alias_flag in opt.flags:
      if alias_flag in self.added_options:
        del self.added_options[alias_flag]
        if alias_flag in self.added_persistent_options:
          del self.added_persistent_options[alias_flag]

  def pop_option(
        self,
        flag: str,
      ) -> None:
    opt = self.added_options.get(flag, None)
    if not opt is None:
      self.pop_option_info(opt)


  def dump(self, include_children: bool=False, include_inherited: bool=False) -> None:
    print(f"========= Subcommand [{self.full_subcmd}] ================")
    if not self.parent is None:
      print(f"  parent: {self.parent.full_subcmd}")
      print(f"  parent's description of this subcmd: {self.parent_description}")
    print(f"  title: {self.title}")
    if not self.category is None:
      print(f"  catewgory: {self.category}")
    print(f"  detailed description:\n{multiline_indent(self.detailed_description, 4)}")
    print(f"  usage:\n{multiline_indent(self.usage, 4)}")
    if len(self.aliases) > 0:
      print(f"  aliases: {self.aliases}")

    print("  added options:")
    for flag in sorted(self.added_options.keys()):
      opt = self.added_options[flag]
      plabel = 'PERSISTENT' if flag in self.added_persistent_options else '          '
      print(f"    {plabel} {flag}: {opt}")
    if include_inherited:
      persistent_options = self.gen_all_persistent_options()
      inherited_persistent_options = dict(
          (x, y) for x, y in persistent_options.items()
            if not x in self.added_persistent_options
        )
      if len(inherited_persistent_options) > 0:
        print("  Inherited persistent options:")
        for flag, opt in inherited_persistent_options.items():
          print(f"    {flag} = {opt}")
    print(f"  epilog:\n{multiline_indent(self.epilog, 4)}")
    if len(self.subtopics) > 0:
      print("  subcommands:")
      for subtopic_name in sorted(self.subtopics.keys()):
        subtopic = self.subtopics[subtopic_name]
        print(f"    {subtopic_name}: {subtopic.parent_description}")
    print("=========================\n")
    if include_children:
      for subtopic_name in sorted(self.subtopics.keys()):
        subtopic = self.subtopics[subtopic_name]
        subtopic.dump(include_children=True)

  def iter_subtopics(self, include_self: bool=False) -> Generator['TopicInfo', None, None]:
    # does a depth-first iteration of all descendants of this topic
    if include_self:
      yield self
    for subtopic_name in sorted(self.subtopics.keys()):
      subtopic = self.subtopics[subtopic_name]
      yield subtopic
      yield from subtopic.iter_subtopics()

  def iter_subtopics_breadth_first(
        self,
        include_self: bool=False,
      ) -> Generator['TopicInfo', None, None]:
    # does a breadth-first iteration of all descendants of this topic
    q = cast(TopicQueue, Queue())
    if include_self:
      q.put(self)
    else:
      for subtopic_name in sorted(self.subtopics.keys()):
        subtopic = self.subtopics[subtopic_name]
        q.put(subtopic)
    while not q.empty():
      topic = q.get()
      yield topic
      for subtopic_name in sorted(topic.subtopics.keys()):
        subtopic = self.subtopics[subtopic_name]
        q.put(subtopic)

  def get_persistent_option(self, flag: str) -> Optional[OptionInfo]:
    result = self.added_persistent_options.get(flag, None)
    if result is None and not self.parent is None:
      result = self.parent.get_persistent_option(flag)
    return result

  def get_local_or_persistent_option(self, flag: str) -> Optional[OptionInfo]:
    result = self.added_options.get(flag, None)
    if result is None and not self.parent is None:
      result = self.parent.get_persistent_option(flag)
    return result

  def get_local_or_child_option_candidates(
        self,
        flag: str,
        topic_path: Optional[List[TopicInfo]]=None
    ) -> Dict[str, OptionInfo]:
    # Returns a map from full subcmd name to OptionInfo
    result: Dict[str, OptionInfo] = {}
    if topic_path is None:
      # if no topic path is provided, then this is the final subcommand on the commandline,
      # and we should not search subchildren
      opt = self.added_options.get(flag)
      if not opt is None:
        result[self.full_subcmd] = opt
    else:
      # if a topic path is provided, then we are searching for anything on topic path
      # or any descendant of topic_path
      topic_path_match_len = min(len(topic_path), len(self.topic_path))
      if topic_path[:topic_path_match_len] == self.topic_path[:topic_path_match_len]:
        opt = self.added_options.get(flag)
        if not opt is None:
          result[self.full_subcmd] = opt
        result.update(self.get_child_option_candidates(flag, topic_path=topic_path))
    return result

  def get_child_option_candidates(
        self,
        flag: str,
        topic_path: Optional[List[TopicInfo]]=None
      ) -> Dict[str, OptionInfo]:
    # Returns a map from full subcmd name to OptionInfo
    result: Dict[str, OptionInfo] = {}
    # if topic_path is None, then we stop at the current topic and do not
    # search children
    if not topic_path is None:
      for subtopic in self.subtopics.values():
        result.update(subtopic.get_local_or_child_option_candidates(flag, topic_path=topic_path))
    return result

  def filter_child_option_candidates(
        self,
        flag: str,
        ignore_description: bool=True,
        ignore_value_name: bool=True,
        ignore_flaglist: bool=True,
        topic_path: Optional[List[TopicInfo]]=None
      ) -> Dict[str, OptionInfo]:
    unfiltered = self.get_child_option_candidates(flag, topic_path=topic_path)
    if len(unfiltered) < 2:
      return unfiltered
    filtered: Dict[str, OptionInfo] = {}
    for full_subcmd, opt in unfiltered.items():
      for existing_opt in filtered.values():
        if (
            opt.has_value == existing_opt.has_value and
            (ignore_value_name or opt.value_name == existing_opt.value_name) and
            (ignore_description or opt.description == existing_opt.description) and
            (ignore_flaglist or opt.flags == existing_opt.flags)):
          break
      else:
        filtered[full_subcmd] = opt
    return filtered

  def get_child_option(
        self,
        flag: str,
        ignore_description: bool=True,
        ignore_value_name: bool=True,
        ignore_flaglist: bool=True,
        topic_path: Optional[List[TopicInfo]]=None
      ) -> Optional[OptionInfo]:
    options = self.filter_child_option_candidates(
        flag,
        ignore_description=ignore_description,
        ignore_value_name=ignore_value_name,
        ignore_flaglist=ignore_flaglist,
        topic_path=topic_path
      )
    if len(options) > 1:
      conflict_list = ', '.join(f"'{x}'" for x in options.keys())   # pylint: disable=consider-iterating-dictionary
      raise XPulumiError(f"Commandline option '{flag}' is ambiguous to subcommand '{self.full_subcmd}'; conflicting definitions in {conflict_list}")
    if len(options) > 0:
      result = list(options.values())[0]
    else:
      result = None
    return result

  def get_option(
        self,
        flag: str,
        ignore_description: bool=True,
        ignore_value_name: bool=True,
        ignore_flaglist: bool=True,
        require_allowed: bool=False,
        topic_path: Optional[List[TopicInfo]]=None,
      ) -> Optional[OptionInfo]:
    result = self.get_local_or_persistent_option(flag)
    if result is None:
      result = self.get_child_option(
          flag,
          ignore_description=ignore_description,
          ignore_value_name=ignore_value_name,
          ignore_flaglist=ignore_flaglist,
          topic_path=topic_path
        )
      if result is None and require_allowed:
        raise XPulumiError(f"Commandline option '{flag}' is not recognized by subcommand '{self.full_subcmd}'")
    return result

  def get_allowed_option(
        self,
        flag: str,
        ignore_description: bool=True,
        ignore_value_name: bool=True,
        ignore_flaglist: bool=True,
        topic_path: Optional[List[TopicInfo]]=None
      ) -> OptionInfo:
    result = self.get_option(
        flag,
        ignore_description=ignore_description,
        ignore_value_name=ignore_value_name,
        ignore_flaglist=ignore_flaglist,
        require_allowed=True,
        topic_path=topic_path
      )
    assert not result is None
    return result

  def option_has_value(
        self,
        flag: str,
        default: Optional[bool]=None,
        topic_path: Optional[List[TopicInfo]]=None
      ) -> bool:
    opt = self.get_option(
        flag,
        ignore_description=True,
        ignore_value_name=True,
        ignore_flaglist=True,
        topic_path = topic_path
      )
    if opt is None:
      if default is None:
        raise XPulumiError(f"Commandline option '{flag}' is not known to subcommand '{self.full_subcmd}'")
      result = default
    else:
      result = opt.has_value

    return result

  def _print_options_help(self, options: Dict[str, OptionInfo], heading: str, file: TextIO) -> None:
    if len(options) > 0:
      found_ids: Set[int] = set()
      unique_options: Dict[str, Tuple[str, str]] = {}
      max_oline_len = 0
      for opt in options.values():
        oid = id(opt)
        if not oid in found_ids:
          short_flag : Optional[str] = None
          long_flag: Optional[str] = None
          value_name = opt.value_name
          description = opt.description
          for flag in opt.flags:
            assert flag.startswith('-')
            if flag.startswith('--'):
              assert long_flag is None
              long_flag = flag
            else:
              assert short_flag is None
              assert len(flag) == 2
              short_flag = flag
          k = short_flag if long_flag is None else long_flag
          assert not k is None
          if short_flag is None:
            assert not long_flag is None
            oline = '      ' + long_flag
          else:
            oline = '  ' + short_flag
            if not long_flag is None:
              oline += ', ' + long_flag
          if not value_name is None:
            oline += ' ' + value_name
          max_oline_len = max(max_oline_len, len(oline))

          unique_options[k] = (oline, description)
          found_ids.add(oid)
      print(file=file)
      print(heading, file=file)
      for k in sorted(unique_options.keys()):
        oline, description = unique_options[k]
        print(f"{oline: <{max_oline_len}}   {description}", file=file)

  def print_help(self, file: TextIO=sys.stdout) -> None:
    print(self.title, file=file)

    if self.detailed_description != '':
      print(file=file)
      print(self.detailed_description, file=file)

    if self.usage != '':
      print(file=file)
      print("Usage:")
      print(self.usage, file=file)

    if len(self.aliases) > 0:
      print(file=file)
      print("Aliases:", file=file)
      print(f"  {', '.join([ self.short_subcmd ] + sorted(self.aliases))}", file=file)

    if len(self.subtopics) > 0:
      cat_topics: Dict[str, List[str]] = {}
      for subcmd_name, subtopic in self.subtopics.items():
        category = subtopic.category
        if category is None:
          category = 'Available'
        cat_topic_list = cat_topics.get(category, None)
        if cat_topic_list is None:
          cat_topic_list = []
          cat_topics[category] = cat_topic_list
        cat_topic_list.append(subcmd_name)

      max_subcmd_name_len = max(14, max(len(x) for x in self.subtopics.keys()))  # pylint: disable=consider-iterating-dictionary
      for category in sorted(cat_topics.keys()):
        cat_topic_list = cat_topics[category]
        print(file=file)
        print(f"{category} Commands:", file=file)
        for subcmd_name in sorted(cat_topic_list):
          subtopic = self.subtopics[subcmd_name]
          print(f"  {subcmd_name: <{max_subcmd_name_len}} {subtopic.parent_description}", file=file)

    self._print_options_help(self.added_options, 'Flags:', file)

    if not self.parent is None:
      self._print_options_help(self.parent.gen_all_persistent_options(), 'Golbal Flags:', file)

    if self.epilog != '':
      print(file=file)
      print(self.epilog)

class OptionValue:
  option_name: str
  value: Optional[Union[str, bool]] = None
  option_info: Optional[OptionInfo] = None

  def __init__(self, option_name: str, value: Optional[Union[str, bool]]=None, option_info: Optional[OptionInfo] = None):
    self.option_name = option_name
    self.value = value
    self.option_info = option_info

  def to_cmd_args(self) -> List[str]:
    result: List[str] = [ self.option_name ]
    if not self.value is None:
      if isinstance(self.value, bool):
        if not self.value:
          result.append('false')
      else:
        result.append(self.value)
    return result

  def __str__(self) -> str:
    ca = self.to_cmd_args()
    return f"{' '.join(shlex.quote(x) for x in ca)}"

  def __repr__(self) -> str:
    return f"<CmdOption {str(self)}>"

  def clone(self) -> OptionValue:
    return OptionValue(self.option_name, value=self.value, option_info=self.option_info)


CmdToken = Union[str, OptionValue]

class ParsedPulumiCmd:
  metadata: 'PulumiMetadata'

  topic: TopicInfo
  """The topic for this subcommand, or the main topic for
     the main command"""

  all_tokens: List[CmdToken]
  """All commandline tokens excluding pulumi program name"""

  subcmd_token_index: int
  """The index within all_tokens where arguments to the final subcommand
     begin (basically index immediately after subcommand short name)
     For main command this will be 0."""

  subcmd_arglist_index: int
  """The index within arglist where arguments to the final subcommand
     begin (basically index immediately after subcommand short name)
     For main command this will be 0."""

  option_values: Dict[str, OptionValue]

  arglist: List[str]

  require_allowed: bool

  def __init__(
        self,
        metadata: 'PulumiMetadata',
        arglist: List[str],
        require_allowed: bool=True,
      ):
    self.metadata = metadata
    self.reset(arglist, require_allowed=require_allowed)

  def get_subcmd_arg_tokens(self) -> List[CmdToken]:
    return self.all_tokens[self.subcmd_token_index:]

  def get_subcmd_args(self) -> List[str]:
    return self.arglist[self.subcmd_arglist_index:]

  def get_pos_args(self) -> List[str]:
    return [ x for x in self.get_subcmd_arg_tokens() if isinstance(x, str) ]

  def num_pos_args(self) -> int:
    return sum(1 for x in self.get_subcmd_arg_tokens() if isinstance(x, str))

  def rescan(self) -> None:
    md = self.metadata
    topic = md.main_topic
    # make one pass through just to determine the final subcmd and full topic path
    for itoken, token in enumerate(self.all_tokens):
      if not isinstance(token, OptionValue):
        subtopic = topic.merged_subtopics.get(token)
        if subtopic is None:
          break
        topic = subtopic
    final_topic = topic

    # now go through again knowning the final topic
    topic = md.main_topic
    looking_for_subtopics = True
    subcmd_token_index = 0
    option_values: Dict[str, OptionValue] = {}
    for itoken, token in enumerate(self.all_tokens):
      if isinstance(token, OptionValue):
        option_info = final_topic.get_option(
            token.option_name,
            require_allowed=self.require_allowed,
          )
        if not option_info is None:
          new_token = OptionValue(token.option_name, token.value, option_info)
          token = new_token
          self.all_tokens[itoken] = token
        flags = [ token.option_name ] if token.option_info is None else token.option_info.flags
        for flag in flags:
          if flag in option_values:
            raise XPulumiError(f"Multiple values for command line option {flags}")
          option_values[flag] = token
      elif looking_for_subtopics:
        subtopic = topic.merged_subtopics.get(token)
        if subtopic is None:
          looking_for_subtopics = False
        else:
          topic = subtopic
          subcmd_token_index = itoken + 1
    assert topic is final_topic
    self.option_values = option_values
    self.topic = topic
    self.subcmd_token_index = subcmd_token_index
    arglist: List[str] = []
    subcmd_arglist_index: Optional[int] = None
    for itoken, token in enumerate(self.all_tokens):
      if itoken == self.subcmd_token_index:
        subcmd_arglist_index = len(arglist)
      if isinstance(token, OptionValue):
        arglist.extend(token.to_cmd_args())
      else:
        arglist.append(token)
    if subcmd_arglist_index is None:
      subcmd_arglist_index = len(arglist)
    self.subcmd_arglist_index = subcmd_arglist_index
    self.arglist = arglist

  def remove_token(self, index: int) -> CmdToken:
    result = self.all_tokens.pop(index)
    self.rescan()
    return result

  def insert_token(self, index: int, value: CmdToken):
    self.all_tokens.insert(index, value)
    self.rescan()

  def get_option_info(self, flag: str) -> Optional[OptionInfo]:
    return self.topic.get_option(flag)

  def get_option_value(self, flag: str) -> Optional[OptionValue]:
    return self.option_values.get(flag)

  def get_option_optional_bool(self, flag: str, default: Optional[bool]=None) -> Optional[bool]:
    opt = self.get_option_value(flag)
    if opt is None:
      result = default
    else:
      if opt.value is None:
        result = True
      elif isinstance(opt.value, bool):
        result = opt.value
      else:
        raise XPulumiError(f"Option '{flag}' is not boolean on subcommand '{self.topic.topic_from_full_subcmd}'")
    return result

  def get_option_bool(self, flag: str, default: bool=False) -> bool:
    result = self.get_option_optional_bool(flag, default=default)
    assert isinstance(result, bool)
    return result

  def get_option_str(self, flag: str, default: Optional[str]=None) -> Optional[str]:
    opt = self.get_option_value(flag)
    if opt is None:
      result = default
    else:
      if opt.value is None or isinstance(opt.value, bool):
        raise XPulumiError(f"Option '{flag}' is boolean, not a str on subcommand '{self.topic.topic_from_full_subcmd}'")
      assert isinstance(opt.value, str)
      result = opt.value
    return result

  def allows_option(self, flag: str) -> bool:
    return not self.get_option_info(flag) is None

  def create_option(self, flag: str, value: Optional[str]) -> OptionValue:
    option_info = self.get_option_info(flag)
    if option_info is None:
      if self.require_allowed:
        raise XPulumiError(f"Command option '{flag}' is not recognized by subcommand '{self.topic.full_subcmd}'")
    elif option_info.has_value and value is None:
      raise XPulumiError(f"Command option '{flag}' requires a value'")
    elif not option_info.has_value and not value is None:
      raise XPulumiError(f"Command option '{flag}' does not accept a value'")
    result = OptionValue(flag, value=value, option_info=option_info)
    return result

  def pop_option(self, flag: str) -> Optional[OptionValue]:
    option_info = self.get_option_info(flag)
    flags = [ flag ] if option_info is None else option_info.flags
    result: Optional[OptionValue] = None
    i = 0
    while i < len(self.all_tokens):
      token = self.all_tokens[i]
      if isinstance(token, OptionValue) and token.option_name in flags:
        self.all_tokens.pop(i)
        result = token
      else:
        i += 1
    if not result is None:
      self.rescan()
    return result

  def pop_option_optional_bool(self, flag: str, default: Optional[bool]=None) -> Optional[bool]:
    opt = self.pop_option(flag)
    if opt is None:
      result = default
    else:
      if opt.value is None:
        result = True
      elif isinstance(opt.value, bool):
        result = opt.value
      else:
        raise XPulumiError(f"Option '{flag}' is not boolean on subcommand '{self.topic.topic_from_full_subcmd}'")
    return result

  def pop_option_bool(self, flag: str, default: bool=False) -> bool:
    result = self.pop_option_optional_bool(flag, default=default)
    assert isinstance(result, bool)
    return result

  def pop_option_str(self, flag: str, default: Optional[str]=None) -> Optional[str]:
    opt = self.pop_option(flag)
    if opt is None:
      result = default
    else:
      if opt.value is None or isinstance(opt.value, bool):
        raise XPulumiError(f"Option '{flag}' is boolean, not a str on subcommand '{self.topic.topic_from_full_subcmd}'")
      assert isinstance(opt.value, str)
      result = opt.value
    return result

  def pop_option_by_token(self, value: OptionValue) -> Optional[OptionValue]:
    return self.pop_option(value.option_name)

  def set_option_by_token(self, value: OptionValue) -> Optional[OptionValue]:
    result = self.pop_option_by_token(value)
    self.insert_token(self.subcmd_token_index, value)
    return result

  def set_option(self, flag: str, value: Optional[str]) -> Optional[OptionValue]:
    ovalue = self.create_option(flag, value)
    result = self.set_option_by_token(ovalue)
    return result

  def set_option_bool(self, flag: str, value: bool=True) -> bool:
    old = self.set_option(flag, None)
    if old is None:
      result = False
    else:
      old_v = old.value
      assert old_v is None or isinstance(old_v, bool)
      result = old.value is None or cast(bool, old_v)
    return result

  def set_option_str(self, flag: str, value: str) -> Optional[str]:
    old = self.set_option(flag, value)
    return None if old is None or old.value is None else str(old.value)

  def reset(self, arglist: List[str], require_allowed: bool=True) -> None:
    self.require_allowed = require_allowed
    md = self.metadata
    topic = md.main_topic
    looking_for_subtopics = True
    i = 0
    all_tokens: List[Union[str, OptionValue]] = []
    while i < len(arglist):
      arg = arglist[i]
      i += 1
      value: Optional[Union[str, bool]]
      if arg.startswith('-') and arg != '-' and arg != '--':
        known_value: Optional[str] = None
        value_known: bool = False
        if arg.startswith('--'):
          parts = arg.split('=', 1)
          flag_names = [ parts[0] ]
          if len(parts) > 1:
            value = parts[1]
            value_known = True
        else:
          flag_names = [ '-' + c for c in arg[1:] ]
        for iflag, flag in enumerate(flag_names):
          if value_known:
            value = known_value
            has_value = not known_value is None
          else:
            has_value = topic.option_has_value(flag, topic_path=topic.topic_path)
            if has_value:
              if iflag + 1 < len(flag_names) or i + 1 > len(arglist):
                raise XPulumiError(f"Commandline option {flag} requires a value for subcommand {topic.full_subcmd}")
              value = arglist[i]
              i += 1
            else:
              value = True
          option_value = OptionValue(flag, value)
          all_tokens.append(option_value)
      else:
        all_tokens.append(arg)
        if looking_for_subtopics:
          subtopic = topic.merged_subtopics.get(arg, None)
          if subtopic is None:
            looking_for_subtopics = False
          else:
            topic = subtopic
    self.all_tokens = all_tokens
    self.rescan()

  def dump(self) -> None:
    print("Parsed command results:\n")
    print(f"  arglist: {self.arglist}")
    print(f"  Tokens: {[ str(x) for x in self.all_tokens ]}")
    print(f"  Subcommand: {self.topic.full_subcmd}")
    print("  Option values (may have duplicates for flag variants):")
    for k in sorted(self.option_values.keys()):
      ov = self.option_values[k]
      if ov.value is None or (isinstance(ov.value, bool) and ov.value):
        print(f"    {k}")
      else:
        print(f"    {k} {shlex.quote(str(ov.value))}")

  def print_help(self, file=sys.stdout) -> None:
    self.topic.print_help(file=file)

class PulumiMetadata:
  pulumi_dir: str
  pulumi_bin_dir: str
  pulumi_prog: str
  prog_env: Dict[str, str]
  pulumi_version: str
  main_topic: TopicInfo

  #global_options: Dict[str, OptionInfo]
  #"""Options defined as persistent by some subcommand. These must be parsed
  #   anywhere in the commandline even if they may not be used, because
  #   if they require a value, we need to skip an arg. The same OptionsInfo
  #   may appear twice in this dict--once each for the short and long
  #   flag names. """

  topic_by_full_name: Dict[str, TopicInfo]
  """Map from space-delimted subcommand name (e.g., "stack export") to
     its TopicInfo. """

  def __init__(
        self,
        pulumi_dir: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        clean: bool = False,
        json_data: Optional[JsonableDict] = None,
        raise_on_cache_error: bool = False
      ):
    if pulumi_dir is None:
      project_root_dir = get_git_root_dir()
      if project_root_dir is None:
        raise XPulumiError(f"Working directory is not in a git project: {os.getcwd()}")
      pulumi_dir = os.path.join(project_root_dir, '.local', '.pulumi')
    self.pulumi_dir = pulumi_dir
    self.pulumi_bin_dir = os.path.join(pulumi_dir, 'bin')
    self.pulumi_prog = os.path.join(self.pulumi_bin_dir, 'pulumi')
    if not os.path.exists(self.pulumi_prog):
      raise XPulumiError(f"Pulumi program not found at {self.pulumi_prog}")
    if env is None:
      env = os.environ
    prog_env = dict(env)
    prog_env['PATH'] = searchpath_prepend(prog_env['PATH'], self.pulumi_bin_dir)
    prog_env['PULUMI_SKIP_UPDATE_CHECK']='1'
    prog_env['XPULUMI_RAW_PULUMI']='1'
    self.prog_env = prog_env
    if json_data is None:
      # Initialize from pulumi help if cache is stale
      pulumi_version = subprocess.check_output(
          [self.pulumi_prog, 'version'],
          env=self.prog_env,
          stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
      if pulumi_version.startswith('v'):
        pulumi_version = pulumi_version[1:]
      assert pulumi_version != ''
      self.pulumi_version = pulumi_version
      cache_filename = os.path.join(pulumi_dir, 'pulumi_help_metadata.json')
      if not clean:
        if os.path.exists(cache_filename):
          try:
            with open(cache_filename, encoding='utf-8') as f:
              json_data = cast(JsonableDict, json.load(f))
            if json_data['version'] == self.pulumi_version:
              # The cache is not stale. Load a new metadata object from it and copy its state.
              md = PulumiMetadata(pulumi_dir=pulumi_dir, env=env, json_data=json_data)
              assert md.pulumi_version == self.pulumi_version
              self.main_topic = md.main_topic
              #self.global_options = md.global_options
              self.topic_by_full_name = md.topic_by_full_name
              self.main_topic.update_metadata(self)
              #print(f"Loaded Pulumi help metadata from cache: {cache_filename}", file=sys.stderr)
              #sys.stderr.flush()
              return
          except Exception as e:
            if raise_on_cache_error:
              raise
            print(f"Unable to load pulumi help metadata from {cache_filename}; rebuilding: {e}", file=sys.stderr)

      self.main_topic = TopicInfo(self)
      tmp_cache_filename = cache_filename + '.tmp'
      with open(tmp_cache_filename, 'w', encoding='utf-8') as f:
        json.dump(self.as_jsonable(), f)
      subprocess.check_call(['mv', tmp_cache_filename, cache_filename ])
    else:
      # Initialize from json
      assert isinstance(json_data, dict)
      self.pulumi_version = cast(str, json_data['version'])
      assert isinstance(self.pulumi_version, str)
      help_data = cast(JsonableDict, json_data['help_data'])
      assert isinstance(help_data, dict)
      self.main_topic = TopicInfo(self, json_data=help_data)
    self.topic_by_full_name = {}
    #global_options: Dict[str, OptionInfo] = {}
    for topic in self.iter_topics():
      self.topic_by_full_name[topic.full_subcmd] = topic
      #for flag, option in topic.added_persistent_options.items():
      #  if flag in global_options:
      #    old_option = global_options[flag]
      #    if option.flags != old_option.flags or option.has_value != old_option.has_value:
      #      raise RuntimeError(f"Command '{topic.full_subcmd}' redefines global option '{flag}' from {old_option} to {option}")
      #  else:
      #    global_options[flag] = option
    #self.global_options = global_options
    #for topic in self.iter_topics():
    #  for flag, option in topic.added_options.items():
    #    if flag in global_options:
    #      old_option = global_options[flag]
    #      if option.flags != old_option.flags or option.has_value != old_option.has_value:
    #        raise RuntimeError(f"Command '{topic.full_subcmd}' redefines global option '{flag}' from {old_option} to local option {option}")

  def iter_topics(self) -> Generator[TopicInfo, None, None]:
    yield from self.main_topic.iter_subtopics(include_self=True)

  def iter_topics_breadth_first(self) -> Generator[TopicInfo, None, None]:
    yield from self.main_topic.iter_subtopics_breadth_first(include_self=True)

  def get_help(self, subcmd: Optional[Union[str, List[str]]] = None) -> str:
    subcmds = self.normalize_subcmd(subcmd)

    pcmd = [ self.pulumi_prog ] + subcmds + [ '--help' ]

    with subprocess.Popen(             # type: ignore [misc]
          pcmd,
          env=self.prog_env,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE
        ) as proc:
      (stdout_bytes, stderr_bytes) = cast(Tuple[bytes, bytes], proc.communicate())
      assert isinstance(stdout_bytes, bytes) and isinstance(stderr_bytes, bytes)
      exit_code = proc.returncode
    if exit_code != 0:
      stderr_s = stderr_bytes.decode('utf-8').rstrip()
      print(stderr_s, file=sys.stderr)
      raise subprocess.CalledProcessError(exit_code, pcmd, stderr = stderr_s)
    return stdout_bytes.decode('utf-8')

  def normalize_subcmd(self, subcmd: Optional[Union[str, List[str]]] = None) -> List[str]:
    if subcmd is None:
      subcmds = []
    elif isinstance(subcmd, str):
      subcmds = [] if subcmd == '' else subcmd.split()
    else:
      subcmds = subcmd
    return subcmds

  def as_jsonable(self) -> JsonableDict:
    result: JsonableDict = dict(
        version=self.pulumi_version,
        help_data=self.main_topic.as_jsonable()
      )
    return result

  def dump(self):
    print(f"pulumi version: {self.pulumi_version}")
    self.main_topic.dump(include_children=True)

  def parse_command(self, arglist: List[str], require_allowed: bool = True) -> ParsedPulumiCmd:
    return ParsedPulumiCmd(self, arglist, require_allowed=require_allowed)

if __name__ == '__main__':
  import argparse

  def cmd_bare(args: argparse.Namespace) -> None:
    use_json = cast(bool, args.json)
    fail_on_cache_error = cast(bool, args.fail_on_cache_error)
    pulumi_dir = cast(Optional[str], args.pulumi_dir)

    pulumi_metadata = PulumiMetadata(pulumi_dir=pulumi_dir, clean=args.clean, raise_on_cache_error=fail_on_cache_error)
    if use_json:
      json.dump(pulumi_metadata.as_jsonable(), sys.stdout, sort_keys=True, indent=2)
    else:
      pulumi_metadata.dump()

  def cmd_subcommands(args: argparse.Namespace) -> None:
    fail_on_cache_error = cast(bool, args.fail_on_cache_error)
    pulumi_dir = cast(Optional[str], args.pulumi_dir)
    pulumi_metadata = PulumiMetadata(pulumi_dir=pulumi_dir, clean=args.clean, raise_on_cache_error=fail_on_cache_error)
    use_json = args.json
    def topic_tuple(topic: TopicInfo) -> Tuple[str, str]:
      cmd_name = topic.full_subcmd
      if cmd_name is None or cmd_name == '':
        cmd_name = '<main>'
      cmd_description = topic.parent_description
      if cmd_description is None:
        cmd_description = topic.title
      return cmd_name, cmd_description

    if use_json:
      odata: JsonableDict = {}
      for topic in pulumi_metadata.iter_topics():
        cmd_name, cmd_description = topic_tuple(topic)
        odata[cmd_name] = cmd_description
        for alias in topic.aliases:
          odata[' '.join(topic.subcmds[:-1] + [alias])] =  f"Alias for '{topic.full_subcmd}'"
      json.dump(odata, sys.stdout, sort_keys=True, indent=2)
    else:
      otable: List[Tuple[str, str]] = []
      for topic in pulumi_metadata.iter_topics():
        tt = topic_tuple(topic)
        otable.append(tt)
        for alias in topic.aliases:
          otable.append((' '.join(topic.subcmds[:-1] + [alias]), f"Alias for '{topic.full_subcmd}'"))
      print(tabulate.tabulate(sorted(otable), headers=['Command', 'Description']))

  def cmd_parse(args: argparse.Namespace) -> None:
    fail_on_cache_error = cast(bool, args.fail_on_cache_error)
    pulumi_cmd = cast(List[str], args.pulumi_cmd)
    if len(pulumi_cmd) > 0 and pulumi_cmd[0] == '--':
      pulumi_cmd = pulumi_cmd[1:]
    pulumi_dir = cast(Optional[str], args.pulumi_dir)
    pulumi_metadata = PulumiMetadata(pulumi_dir=pulumi_dir, clean=args.clean, raise_on_cache_error=fail_on_cache_error)
    cmd = pulumi_metadata.parse_command(pulumi_cmd)
    cmd.dump()

  parser = argparse.ArgumentParser(description="Manage pulumi-based projects.")

  parser.add_argument('-C', '--cwd', default='.',
                      help="Change the effective directory used to search for configuration")
  parser.add_argument('-d', '--pulumi-dir', default=None,
                      help="The location of the pulumi installation. Default is <git-project-dir>/.local/.pulumi")
  parser.add_argument('-j', '--json', action='store_true', default=False,
                      help='''Output the metadata as json.''')
  parser.add_argument('--clean', action='store_true', default=False,
                      help='''Force recreation of cached metadata.''')
  parser.add_argument('--fail-on-cache-error', action='store_true', default=False,
                      help='''Fail if the cache exists but is invalid, rather than rebuilding.''')
  parser.set_defaults(func=cmd_bare)

  subparsers = parser.add_subparsers(
                      title='Commands',
                      description='Valid commands',
                      help='Additional help available with "help_metadata <command-name> -h"')

  parser_subcommands = subparsers.add_parser('subcommands', description="List all subcommands with a brief description.")
  parser_subcommands.set_defaults(func=cmd_subcommands)

  parser_parse = subparsers.add_parser('parse', description="Parse a pulumi commandline (precede main pulumi options with '--').")
  parser_parse.add_argument('pulumi_cmd', nargs=argparse.REMAINDER,
                        help='Command and arguments as would be provided to pulumi.')
  parser_parse.set_defaults(func=cmd_parse)

  cmd_args = parser.parse_args()
  os.chdir(cmd_args.cwd)
  cmd_args.func(cmd_args)
