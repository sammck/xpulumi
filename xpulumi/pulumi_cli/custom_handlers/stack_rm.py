#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for "pulumi stack rm" command"""

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

class PulumiCmdHandlerStackRm(PosStackArgPulumiCommandHandler):
  full_subcmd = "stack rm"
  _recursive: bool
  _preserve_config: bool

  @classmethod
  def modify_metadata(cls, wrapper: PulumiWrapper, metadata: PulumiMetadata):
    topic = metadata.topic_by_full_name[cls.full_subcmd]
    topic.add_option([ '--remove-config' ], description='[xpulumi] Delete the corresponding Pulumi.<stack-name>.yaml configuration file for the stack', is_persistent = True)

  def custom_tweak(self) -> None:
    self._preserve_config = not not self.get_parsed().get_option_optional_bool('--preserve-config')
    remove_config = not not self.get_parsed().pop_option_optional_bool('--preserve-config')
    if self._preserve_config:
      if remove_config:
        raise XPulumiError("Cannot have both --preserve-config and --remove-config")
    elif not remove_config:
      self._preserve_config = True
      self.get_parsed().set_option_bool('--preserve-config')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    stack = self.require_stack()
    if not stack.is_inited():
      print(
          f"{self.ecolor(Fore.GREEN)}NOTE: xpulumi stack '{stack.full_stack_name}' has already been "
          f"removed or has not been initialized{self.ecolor(Style.RESET_ALL)}", file=sys.stderr
        )
      return 0
    if not stack.is_deployable():
      raise XPulumiError(f"Stack {stack.full_stack_name} is not destroyable by this project")
    if stack.is_deployed():
      raise XPulumiError(f"Cannot remove deployed xpulumi stack '{stack.full_stack_name}'; destroy it first with 'pulumi destroy'")
    return None
