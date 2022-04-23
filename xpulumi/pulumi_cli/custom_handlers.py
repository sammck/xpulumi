#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Wrapper for standard Pulumi CLI that passes xpulumi envionment forward"""

from typing import (Any, Dict, List, Optional, Union, Set, Type)

from copy import deepcopy
from lib2to3.pgen2.token import OP
import os
import sys
import json
import subprocess

# NOTE: this module runs with -m; do not use relative imports
from xpulumi.backend import XPulumiBackend
from xpulumi.project import XPulumiProject
from xpulumi.base_context import XPulumiContextBase
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import JsonableTypes
from xpulumi.pulumi_cli.help_metadata import (
    PulumiMetadata,
    ParsedPulumiCmd,
  )
from xpulumi.stack import XPulumiStack

from .wrapper import (
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

  def custom_tweak(self) -> None:
    self._recursive = self.get_parsed().pop_option_optional_bool('--recursive')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    stack = self.require_stack()
    if not stack.is_deployable():
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
          dep_project.init_stack(dep_stack_name)
          rc = dep_project.call_project_pulumi(['up'], stack_name=dep_stack_name)
          if rc != 0:
            return rc

        action_desc = "deploying" if self.full_subcmd == "up" else "previewing"

        print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
        print(f"     All prerequisites deployed; {action_desc} xpulumi project {stack.project.name}, stack {stack.stack_name}", file=sys.stderr)
        print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
    return None


class PulumiCmdHandlerUp(PulumiCmdHandlerUpPreview):
  full_subcmd = "up"

class PulumiCmdHandlerPreview(PulumiCmdHandlerUpPreview):
  full_subcmd = "preview"

class PulumiCmdHandlerDestroy(PulumiCommandHandler):
  full_subcmd = "destroy"
  _recursive: bool

  @classmethod
  def modify_metadata(cls, wrapper: PulumiWrapper, metadata: PulumiMetadata):
    topic = metadata.topic_by_full_name[cls.full_subcmd]
    topic.add_option([ '-R', '--recursive' ], description='[xpulumi] Recursively destroy dependencies first', is_persistent = True)

  def custom_tweak(self) -> None:
    self._recursive = self.get_parsed().pop_option_optional_bool('--recursive')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
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
      else:
        for dep in remaining:
          dep_stack_name = dep.stack_name
          dep_project = dep.project
          print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
          print(f"     Destroying dependent xpulumi project {dep_project.name}, stack {dep_stack_name}", file=sys.stderr)
          print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
          dep_project.init_stack(dep_stack_name)
          rc = dep_project.call_project_pulumi(['destroy'], stack_name=dep_stack_name)
          if rc != 0:
            return rc

        print(f"\n{self.ecolor(Fore.GREEN)}===============================================================================", file=sys.stderr)
        print(f"     All dependent stacks deployed; destroying xpulumi project {stack.project.name}, stack {stack.stack_name}", file=sys.stderr)
        print(f"==============================================================================={self.ecolor(Style.RESET_ALL)}\n", file=sys.stderr)
    return None

class PulumiCmdHandlerStackRm(PosStackArgPulumiCommandHandler):
  full_subcmd = "stack rm"
  _recursive: bool
  _preserve_config: bool

  @classmethod
  def modify_metadata(cls, wrapper: PulumiWrapper, metadata: PulumiMetadata):
    topic = metadata.topic_by_full_name[cls.full_subcmd]
    topic.add_option([ '--remove-config' ], description='[xpulumi] Delete the corresponding Pulumi.<stack-name>.yaml configuration file for the stack', is_persistent = True)

  def custom_tweak(self) -> None:
    self._preserve_config = self.get_parsed().get_option_optional_bool('--preserve-config')
    remove_config = self.get_parsed().pop_option_optional_bool('--preserve-config')
    if self._preserve_config:
      if remove_config:
        raise XPulumiError("Cannot have both --preserve-config and --remove-config")
    elif not remove_config:
      self._preserve_config = True
      self.get_parsed().set_option_bool('--preserve-config')

  def do_pre_raw_pulumi(self, cmd: List[str], env: Dict[str, str]) -> Optional[int]:
    stack = self.require_stack()
    if stack.is_deployed():
      raise XPulumiError(f"Cannot remove deployed xpulumi stack '{stack.full_stack_name}'; destroy it first with 'pulumi destroy'")
    if not stack.is_inited():
      print(
          f"{self.ecolor(Fore.GREEN)}NOTE: xpulumi stack '{stack.full_stack_name}' has already been "
          f"removed or has not been initialized{self.ecolor(Style.RESET_ALL)}", file=sys.stderr
        )
      return 0
    return None

custom_handlers: Dict[str, Type[PulumiCommandHandler]] = {
    "up": PulumiCmdHandlerUp,
    "preview": PulumiCmdHandlerPreview,
    "destroy": PulumiCmdHandlerDestroy,
    "stack rm": PulumiCmdHandlerStackRm,
  }
