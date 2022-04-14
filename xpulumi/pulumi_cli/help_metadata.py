#!/usr/bin/env python3

#from xpulumi.exceptions import XPulumiError

from email.generator import Generator
from typing import List, Optional, Union, Mapping, Dict, cast, Tuple, Generator
import os
import subprocess
import re
import json
import sys

from project_init_tools import (
    get_git_root_dir,
    searchpath_prepend,
    multiline_indent,
  )

# This module runs with -m; do not use relative imports
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import Jsonable, JsonableDict

class OptionInfo:
  option_regex = re.compile(
      r'\A  '
      r'(((?P<short_flag>-[a-zA-Z0-9]), )|(    ))'
      r'(?P<long_flag>--[a-zA-Z0-9_.\-]+)( (?P<value_name>[a-zA-Z0-9_]+)(\[=(?P<default_value>[^\]]+)\])?:?)?'
      r'  \s*'
      r'(?P<description>[^ ].*)\Z',
      flags=re.MULTILINE | re.DOTALL
    )

  flags: List[str]
  value_name: Optional[str] = None
  description: str

  def __init__(self, help_line: Optional[str]=None, json_data: Optional[JsonableDict] = None):
    if json_data is None:
      assert not help_line is None
      m = self.option_regex.match(help_line)
      if not m:
        raise XPulumiError(f"Invalid flag description line: {json.dumps(help_line)}")
      self.flags = [ m.group('long_flag') ]
      short_flag = m.group('short_flag')
      if not short_flag is None and short_flag != '':
        self.flags.append(short_flag)
      self.description = m.group('description')
      value_name = m.group('value_name')
      if value_name != '':
        self.value_name = value_name
    else:
      assert isinstance(json_data, dict)
      self.flags = json_data.get('flags', [])
      assert isinstance(self.flags, list) and all(isinstance(x, str) for x in self.flags)
      self.value_name = json_data.get('value_name', None)
      assert self.value_name is None or isinstance(self.value_name, str)
      self.description = json_data['description']
      assert isinstance(self.description, str)

  def __str__(self) -> str:
    return f"<OptionInfo(flags={self.flags}, value_name='{self.value_name}', description={json.dumps(self.description)})>"

  def as_jsonable(self) -> JsonableDict:
    result: JsonableDict = dict(flags=self.flags, description=self.description)
    if not self.value_name is None:
      result.update(value_name=self.value_name)
    return result

class TopicInfo:
  subcmd_regex = re.compile(r'^  (?P<subcmd_name>[a-zA-Z0-9\-]+)\s+(?P<subcmd_description>[^ ].*)$')

  metadata: 'PulumiMetadata'
  parent: Optional['TopicInfo']
  parent_description: Optional[str]
  subcmds: List[str]
  title: str
  detailed_description: str
  usage: str
  aliases: List[str]
  subtopics: Dict[str, 'TopicInfo']
  subtopic_aliases: Dict[str, 'TopicInfo']
  merged_subtopics: Dict[str, 'TopicInfo']
  option_list: List[OptionInfo]
  global_option_list: List[OptionInfo]
  epilog: str

  def __init__(
        self,
        metadata: 'PulumiMetadata',
        subcmd: Optional[Union[str, List[str]]] = None,
        parent: Optional['TopicInfo'] = None,
        parent_description: Optional[str] = None,
        json_data: Optional[JsonableDict] = None
      ):
    self.metadata = metadata
    self.parent = parent
    self.parent_description = parent_description
    self.subcmds = metadata.normalize_subcmd(subcmd)

    if not json_data is None:
      self._init_from_json_data(json_data)
      return

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
      self.subtopic_aliases = {}
      self.merged_subtopics = {}
      if lines[i] == 'Available Commands:':
        i += 1
        while lines[i] != '':
          m = self.subcmd_regex.match(lines[i])
          if not m:
            raise RuntimeError(f"Invalid subcommand description: {lines[i]}")
          subcmd_name = m.group('subcmd_name')
          subcmd_description = m.group('subcmd_description')
          subtopic = TopicInfo(self.metadata, self.subcmds + [ subcmd_name ], parent=self, parent_description=subcmd_description)
          assert not subcmd_name in self.merged_subtopics
          self.merged_subtopics[subcmd_name] = subtopic
          self.subtopics[subcmd_name] = subtopic
          for alias in subtopic.aliases:
            assert not alias in self.merged_subtopics
            self.merged_subtopics[alias] = subtopic
            self.subtopic_aliases[alias] = subtopic

          i += 1
        i += 1

      assert lines[i] == 'Flags:'
      i += 1
      self.option_list = []
      while i < len(lines) and lines[i] != '':
        oline = lines[i]
        while i + 1 < len(lines) and lines[i + 1].startswith("        "):
          i += 1
          oline += '\n' + lines[i].lstrip()
        self.option_list.append(OptionInfo(oline))
        i += 1
      i += 1
      self.global_option_list = []
      if lines[i] == 'Global Flags:':
        i += 1
        while i < len(lines) and lines[i] != '':
          oline = lines[i]
          while i + 1 < len(lines) and lines[i + 1].startswith("        "):
            i += 1
            oline += '\n' + lines[i].lstrip()
          self.global_option_list.append(OptionInfo(oline))
          i += 1
        i += 1
      self.epilog = '\n'.join(lines[i:])
    except Exception as e:
      if i >= len(lines):
        raise RuntimeError(f"[{self.full_subcmd}]: Error in line {i}: {e}") from e
      else:
        raise RuntimeError(f"[{self.full_subcmd}]: Error in line {i} ({json.dumps(lines[i])}): {e}") from e

  @property
  def full_subcmd(self) -> str:
    return ' '.join(self.subcmds)

  @property
  def short_subcmd(self) -> str:
    return '' if len(self.subcmds) == 0 else self.subcmds[-1]

  def as_jsonable(self) -> JsonableDict:
    result: JsonableDict = dict(
        title=self.title,
        description=self.detailed_description,
        usage=self.usage,
        epilog=self.epilog,
      )
    if not self.parent_description is None:
      result.update(parent_description=self.parent_description)
    if len(self.aliases) > 0:
      result.update(aliases=self.aliases)
    if len(self.option_list) > 0:
      result.update(options=[x.as_jsonable() for x in self.option_list])
    if len(self.global_option_list) > 0:
      result.update(global_options=[x.as_jsonable() for x in self.global_option_list])
    if len(self.subtopics) > 0:
      result.update(subcommands=dict((x, y.as_jsonable()) for x, y in self.subtopics.items()))
    return result

  def update_metadata(self, metadata: 'PulumiMetadata') -> None:
    self.metadata = metadata
    for st in self.subtopics.values():
      st.update_metadata(metadata)

  def _init_from_json_data(self, json_data: JsonableDict) -> None:
    assert isinstance(json_data, dict)
    self.title = json_data['title']
    assert isinstance(self.title, str)
    self.parent_description = json_data.get('parent_description', None)
    assert self.parent_description is None or isinstance(self.parent_description, str)
    self.detailed_description = json_data['description']
    assert isinstance(self.detailed_description, str)
    self.usage = json_data['usage']
    assert isinstance(self.usage, str)
    self.epilog = json_data['epilog']
    assert isinstance(self.epilog, str)
    self.aliases = json_data.get('aliases', [])
    assert isinstance(self.aliases, list) and all(isinstance(x, str) for x in self.aliases)
    self.option_list = []
    for opt_data in json_data.get('options', []):
      option = OptionInfo(json_data=opt_data)
      self.option_list.append(option)
    self.global_option_list = []
    for opt_data in json_data.get('global_options', []):
      option = OptionInfo(json_data=opt_data)
      self.global_option_list.append(option)
    self.subtopics = {}
    self.merged_subtopics = {}
    self.subtopic_aliases = {}
    json_subcommands = json_data.get('subcommands', {})
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
        self.subtopic_aliases[alias] = subtopic

  def dump(self, include_children: bool=False) -> None:
    print(f"========= Subcommand [{self.full_subcmd}] ================")
    if not self.parent is None:
      if self.parent.full_subcmd == '':
        parent_subcmd = '<main command>'
      else:
        parent_subcmd = self.parent.full_subcmd
      print(f"  parent: {parent_subcmd}")
      print(f"  parent's description of this subcmd: {self.parent_description}")
    print(f"  title: {self.title}")
    print(f"  detailed description:\n{multiline_indent(self.detailed_description, 4)}")
    print(f"  usage:\n{multiline_indent(self.usage, 4)}")
    if len(self.aliases) > 0:
      print(f"  aliases: {self.aliases}")

    print("  flags:")
    for flag in self.option_list:
      print(f"    {flag}")
    print("  global flags:")
    for flag in self.global_option_list:
      print(f"    {flag}")
    print(f"  epilog:\n{multiline_indent(self.epilog, 4)}")
    print("  subcommands:")
    for subtopic_name in sorted(self.subtopics.keys()):
      subtopic = self.subtopics[subtopic_name]
      print(f"    {subtopic_name}: {subtopic.parent_description}")
    print("=========================\n")
    if include_children:
      for subtopic_name in sorted(self.subtopics.keys()):
        subtopic = self.subtopics[subtopic_name]
        subtopic.dump(include_children=True)

  def iter_subtopics(self) -> Generator['TopicInfo', None, None]:
    for subtopic_name in sorted(self.subtopics.keys()):
      subtopic = self.subtopics[subtopic_name]
      yield subtopic
      yield from subtopic.iter_subtopics()

class PulumiMetadata:
  pulumi_dir: str
  pulumi_bin_dir: str
  pulumi_prog: str
  prog_env: Dict[str, str]
  pulumi_prog: str
  pulumi_version: str
  main_topic: TopicInfo

  def __init__(
        self,
        pulumi_dir: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        clean: bool = False,
        json_data: Optional[JsonableDict] = None,
      ):
    if pulumi_dir is None:
      project_root_dir = get_git_root_dir()
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
    self.prog_env = prog_env
    if json_data is None:
      pulumi_version = subprocess.check_output(
          [self.pulumi_prog, 'version'],
          env=self.prog_env,
          stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
      assert pulumi_version != ''
      self.pulumi_version = pulumi_version
      cache_filename = os.path.join(pulumi_dir, 'pulumi_help_metadata.json')
      if not clean:
        if os.path.exists(cache_filename):
          try:
            with open(cache_filename, encoding='utf-8') as f:
              json_data = cast(JsonableDict, json.load(f))
            md = PulumiMetadata(pulumi_dir=pulumi_dir, env=env, json_data=json_data)
            if md.pulumi_version == self.pulumi_version:
              self.main_topic = md.main_topic
              self.main_topic.update_metadata(self)
              #print(f"Loaded Pulumi help metadata from cache: {cache_filename}", file=sys.stderr)
              sys.stderr.flush()
              return
          except Exception as e:
            print(f"Unable to load pulumi help metadata from {cache_filename}; rebuilding: {e}", file=sys.stderr)
            raise
      self.main_topic = TopicInfo(self)
      tmp_cache_filename = cache_filename + '.tmp'
      with open(tmp_cache_filename, 'w', encoding='utf-8') as f:
        json.dump(self.as_jsonable(), f)
      subprocess.check_call(['mv', tmp_cache_filename, cache_filename ])
    else:
      assert isinstance(json_data, dict)
      self.pulumi_version = cast(str, json_data['version'])
      assert isinstance(self.pulumi_version, str)
      self.main_topic = TopicInfo(self, json_data=json_data['help_data'])

  def iter_topics(self) -> Generator[TopicInfo, None, None]:
    yield self.main_topic
    yield from self.main_topic.iter_subtopics()

  def get_help(self, subcmd: Optional[Union[str, List[str]]] = None) -> str:
    subcmds = self.normalize_subcmd(subcmd)

    pcmd = [ self.pulumi_prog ] + subcmds + [ '--help' ]

    with subprocess.Popen(             # type: ignore [misc]
          pcmd,
          env=self.prog_env,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE
        ) as proc:
      (stdout_bytes, stderr_bytes) = cast(Tuple[Union[str, bytes], Union[str, bytes]], proc.communicate())
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

  

if __name__ == '__main__':
  import argparse

  def cmd_bare(args: argparse.Namespace) -> None:
    use_json = args.json

    pulumi_metadata = PulumiMetadata(clean=args.clean)
    if use_json:
      json.dump(pulumi_metadata.as_jsonable(), sys.stdout, sort_keys=True, indent=2)
    else:
      pulumi_metadata.dump()

  def cmd_subcommands(args: argparse.Namespace) -> None:
    pulumi_metadata = PulumiMetadata(clean=args.clean)
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
      json.dump(odata, sys.stdout, sort_keys=True, indent=2)
    else:
      import tabulate
      otable: List[Tuple[str, str]] = []
      for topic in pulumi_metadata.iter_topics():
        tt = topic_tuple(topic)
        otable.append(tt)
        for alias in topic.aliases:
          otable.append((' '.join(topic.subcmds[:-1] + [alias]), f"Alias for '{topic.full_subcmd}'"))
      print(tabulate.tabulate(sorted(otable), headers=['Command', 'Description']))


  parser = argparse.ArgumentParser(description="Manage pulumi-based projects.")

  parser.add_argument('-C', '--cwd', default='.',
                      help="Change the effective directory used to search for configuration")
  parser.add_argument('-j', '--json', action='store_true', default=False,
                      help='''Output the metadata as json.''')
  parser.add_argument('--clean', action='store_true', default=False,
                      help='''Force recreation of cached metadata.''')
  parser.set_defaults(func=cmd_bare)

  subparsers = parser.add_subparsers(
                      title='Commands',
                      description='Valid commands',
                      help='Additional help available with "help_metadata <command-name> -h"')

  parser_subcommands = subparsers.add_parser('subcommands', description="List all subcommands with a brief description.")
  parser_subcommands.set_defaults(func=cmd_subcommands)

  cmd_args = parser.parse_args()
  os.chdir(cmd_args.cwd)
  cmd_args.func(cmd_args)
