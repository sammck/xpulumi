#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for "pulumi refresh" command"""

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

class PulumiCmdHandlerRefresh(PulumiCommandHandler):
  full_subcmd: str
  _recursive: bool

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    stack = self.require_stack()
    if not stack.is_deployed():
      raise XPulumiError(f"Stack {stack.full_stack_name} has not been deployed, or has been destroyed")
    if not stack.is_deployable():
      raise XPulumiError(f"Stack {stack.full_stack_name} is not refreshable by this project")
    return None
