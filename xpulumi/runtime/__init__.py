# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Runtime utilities directly usable from within pulumi project __main__.py code"""

from .stack_outputs import StackOutputs, SyncStackOutputs
from .util import (
    future_func,
    TTL_SECOND, TTL_MINUTE, TTL_HOUR, TTL_DAY,
    future_val,
    get_xpulumi_context,
    get_xpulumi_project,
    get_current_xpulumi_project_name,
    get_current_xpulumi_project,
    get_ami_arch_from_instance_type,
    sync_get_ami_arch_from_instance_type,
    sync_get_ami_arch_from_processor_arches,
    sync_get_processor_arches_from_instance_type,
    yamlify_promise,
    jsonify_promise,
    list_of_promises,
    default_val,    
  )
from .common import (
    pconfig,
    long_stack,
    stack_short_prefix,
    aws_global_region,
    aws_default_region,
    aws_provider,
    aws_resource_options,
    aws_invoke_options,
    AwsRegionData,
    aws_global_provider,
    aws_global_resource_options,
    aws_global_invoke_options,
    owner_tag,
    default_tags,
    with_default_tags,
    get_availability_zones,
  )

from .vpc import VpcEnv
from .dns import DnsZone
from .security_group import FrontEndSecurityGroup

