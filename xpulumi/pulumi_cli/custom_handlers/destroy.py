#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for "pulumi destroy" command"""

from typing import (Any, Dict, List, Optional, Union, Set, Type)

from copy import deepcopy
from lib2to3.pgen2.token import OP
import os
import sys
import json
import subprocess

from ...backend import XPulumiBackend
from ...project import XPulumiProject
from ...base_context import XPulumiContextBase
from ...exceptions import XPulumiError
from ...internal_types import JsonableTypes
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

class PulumiCmdHandlerDestroy(PulumiCommandHandler):
  full_subcmd = "destroy"
  _recursive: bool

  @classmethod
  def modify_metadata(cls, wrapper: PulumiWrapper, metadata: PulumiMetadata):
    topic = metadata.topic_by_full_name[cls.full_subcmd]
    topic.add_option([ '-R', '--recursive' ], description='[xpulumi] Recursively destroy dependencies first', is_persistent = True)

  def custom_tweak(self) -> None:
    self._recursive = not not self.get_parsed().pop_option_optional_bool('--recursive')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    yes_flag = self.get_parsed().get_option_bool('--yes')
    stack = self.require_stack()
    if not stack.is_deployed():
      print(
          f"{self.ecolor(Fore.GREEN)}NOTE: xpulumi stack '{stack.full_stack_name}' has already been "
          f"destroyed or has never been deployed.{self.ecolor(Style.RESET_ALL)}", file=sys.stderr
        )
      return 0
    if not stack.is_deployable():
      raise XPulumiError(f"Stack {stack.full_stack_name} is not destroyable")
    dependencies = stack.get_stack_destroy_order(include_self=False)
    remaining: List[XPulumiStack] = []
    for dep in dependencies:
      if dep.is_deployed():
        remaining.append(dep)
      elif self._recursive:
        print(
            f"{self.ecolor(Fore.GREEN)}NOTE: dependent xpulumi stack '{dep.full_stack_name}' has already been "
            f"destroyed or has never been deployed.{self.ecolor(Style.RESET_ALL)}", file=sys.stderr
          )
    if len(remaining) > 0:
      if not self._recursive:
        raise XPulumiError(
            f"Cannot destroy stack {stack.full_stack_name} "
            f"until dependencies are destroyed: {', '.join(x.full_stack_name for x in remaining)}"
          )
      for dep in remaining:
        dep_stack_name = dep.stack_name
        dep_project = dep.project
        print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
        print(f"     Destroying dependent xpulumi project {dep_project.name}, stack {dep_stack_name}", file=sys.stderr)
        print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
        cmd = ['destroy']
        if yes_flag:
          cmd.append('--yes')
        rc = dep_project.call_project_pulumi(cmd, stack_name=dep_stack_name)
        if rc != 0:
          return rc

      print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
      print(f"     All dependent stacks destroyed; destroying xpulumi project {stack.project.name}, stack {stack.stack_name}", file=sys.stderr)
      print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
    return None
