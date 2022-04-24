#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for "pulumi up" and "pulumi preview" commands"""

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

class PulumiCmdHandlerUpPreview(PrecreatePulumiCommandHandler):
  full_subcmd: str
  _recursive: bool

  @classmethod
  def modify_metadata(cls, wrapper: PulumiWrapper, metadata: PulumiMetadata):
    topic = metadata.topic_by_full_name[cls.full_subcmd]
    topic.add_option([ '-R', '--recursive' ], description='[xpulumi] Recursively deploy dependencies first', is_persistent = True)
    if cls.full_subcmd == 'preview':
      topic.add_option([ '-y', '--yes' ], description='[xpulumi] On recursion, automatically approve and perform the update after previewing it', is_persistent = True)

  def custom_tweak(self) -> None:
    self._recursive = not not self.get_parsed().pop_option_optional_bool('--recursive')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    yes_flag = self.get_parsed().get_option_bool('--yes')
    stack = self.require_stack()
    if not stack.is_deployable():
      if stack.is_deployed():
        print(
            f"{self.ecolor(Fore.GREEN)}NOTE: xpulumi stack '{stack.full_stack_name}' is not deployable "
            f"by this project, but it has already been deployed. It is assumed to be up-to-date.{self.ecolor(Style.RESET_ALL)}", file=sys.stderr
          )
        return 0
      raise XPulumiError(f"Stack {stack.full_stack_name} is not deployable")

    dependencies = stack.get_stack_build_order(include_self=False)
    if len(dependencies) > 0:
      if not self._recursive:
        remaining: List[XPulumiStack] = []
        for dep in dependencies:
          if not dep.is_deployed():
            remaining.append(dep)
        if len(remaining) > 0:
          action_desc = "deploy" if self.full_subcmd == "up" else "preview"
          raise XPulumiError(
              f"Cannot {action_desc} stack {stack.full_stack_name} "
              f"until dependencies are deployed: {', '.join(x.full_stack_name for x in remaining)}"
            )
      else:
        for dep in dependencies:
          dep_stack_name = dep.stack_name
          dep_project = dep.project
          print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
          print(f"     Deploying prerequisite xpulumi project {dep_project.name}, stack {dep_stack_name}", file=sys.stderr)
          print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
          cmd = [ 'up' ]
          if yes_flag:
            cmd.append('--yes')
          rc = dep_project.call_project_pulumi(cmd, stack_name=dep_stack_name)
          if rc != 0:
            return rc

        action_desc = "deploying" if self.full_subcmd == "up" else "previewing"

        print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
        print(f"     All prerequisites deployed; {action_desc} xpulumi project {stack.project.name}, stack {stack.stack_name}", file=sys.stderr)
        print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)

    stack.project.init_stack(stack.stack_name)
    return None


class PulumiCmdHandlerUp(PulumiCmdHandlerUpPreview):
  full_subcmd = "up"

class PulumiCmdHandlerPreview(PulumiCmdHandlerUpPreview):
  full_subcmd = "preview"
