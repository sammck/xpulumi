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

from .wrapper import (
    CmdExitError,
    PulumiCommandHandler,
    PulumiWrapper,
    PosStackArgPulumiCommandHandler,
    PrecreatePosStackArgPulumiCommandHandler,
    PrecreatePulumiCommandHandler,
  )

custom_handlers: Dict[str, Type[PulumiCommandHandler]] = {
    # "up": CmdHandlerUp,
  }
