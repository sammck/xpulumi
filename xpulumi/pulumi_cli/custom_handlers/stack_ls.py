#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for "pulumi stack ls" command"""

from typing import (Any, Dict, List, Optional, Union, Set, Type, Tuple, cast)

from copy import deepcopy
from lib2to3.pgen2.token import OP
import os
import sys
import json
import subprocess
import tabulate
import dateutil.parser
import datetime
import pytz
import humanize

from ...backend import XPulumiBackend
from ...project import XPulumiProject
from ...base_context import XPulumiContextBase
from ...exceptions import XPulumiError
from ...internal_types import JsonableDict, JsonableTypes
from ..help_metadata import (
    PulumiMetadata,
    ParsedPulumiCmd,
  )
from ...stack import XPulumiStack

from ..wrapper import (
    CmdExitError,
    PulumiCommandHandler,
    PulumiWrapper,
    PosStackArgPulumiCommandHandler,
    PrecreatePosStackArgPulumiCommandHandler,
    PrecreatePulumiCommandHandler,
    Fore,
    Back,
    Style,
  )

class PulumiCmdHandlerStackLs(PrecreatePulumiCommandHandler):
  full_subcmd = "stack ls"

  def print_tabular_metadata(self, md: Dict[str, JsonableDict]) -> None:
    otable: List[Tuple[str, str, str]] = []
    for stack_name in sorted(md.keys()):
      stack_md = md[stack_name]
      name_col = stack_name
      if stack_md.get('current', False):
        name_col += '*'
      if stack_md.get('updateInProgress', False):
        last_update_col = 'in progress'
      else:
        last_update_time_s = cast(Optional[str], stack_md.get('lastUpdate', None))
        if last_update_time_s is None or last_update_time_s == "0001-01-01T00:00:00Z":
          last_update_col = 'n/a'
        else:
          last_update_time = dateutil.parser.isoparse(last_update_time_s)
          last_update_col = humanize.naturaltime(last_update_time, when=datetime.datetime.now(tz=pytz.UTC))
      resource_count = cast(Optional[int], stack_md.get('resourceCount', None))
      if resource_count is None:
        resource_count_col = 'n/a'
      else:
        resource_count_col = str(resource_count)

      orow = (name_col, last_update_col, resource_count_col)
      otable.append(orow)
    print(tabulate.tabulate(otable, headers=['NAME', 'LAST UPDATE', 'RESOURCE COUNT'], tablefmt='plain'))

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    use_json = self.get_parsed().get_option_bool('--json')
    project = self.wrapper.project
    if project is None:
      raise XPulumiError(f"Working directory is not in an xpulumi project: {self.cwd}")

    md = project.get_stacks_metadata()

    if use_json:
      md_list: List[JsonableDict] = []
      for stack_name in sorted(md.keys()):
        md_list.append(md[stack_name])
      print(json.dumps(md_list, indent=2, sort_keys=True))
    else:
      self.print_tabular_metadata(md)
    return 0
