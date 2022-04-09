# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Package xpulumi provides tools to improve the usability of Pulumi
"""

from .version import __version__

from .internal_types import Jsonable, JsonableDict, JsonableList

from .exceptions import (
    XPulumiError,
    PulumiApiError
  )

from .backend import XPulumiBackend
from .project import XPulumiProject
from .stack import XPulumiStack
from .api_client import PulumiApiClient
from .context import XPulumiContext
from .base_context import XPulumiContextBase

from project_init_tools import (
   run_once,
   get_tmp_dir,
   hash_pathname,
   full_name_of_type,
   full_type,
   clone_json_data,
   file_url_to_pathname,
   pathname_to_file_url,
   get_git_config_value,
   get_git_user_email,
   get_git_user_friendly_name,
   get_git_root_dir,
   append_lines_to_file_if_missing,
   multiline_indent,
   gen_etc_shadow_password_hash,
  )
