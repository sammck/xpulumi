# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Runtime utilities directly usable from within pulumi project __main__.py code"""

from .stack_outputs import StackOutputs, SyncStackOutputs, get_stack_outputs, get_stack_output, require_stack_output
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
    shell_quote_promise,
    list_of_promises,
    default_val,
    enable_debugging,
    xbreakpoint,
    sync_gen_etc_shadow_password_hash,
    gen_etc_shadow_password_hash,
    dedent,
    concat_and_dedent,
    future_dedent,
  )
from ..util import split_s3_uri
from .s3_object_waiter import (
    sync_wait_and_get_s3_object,
    sync_wait_s3_object,
    async_wait_and_get_s3_object,
    async_wait_s3_object,
    wait_s3_object,
    wait_and_get_s3_object,
    wait_and_get_s3_object_str,
    wait_and_get_s3_json_object,
  )

from .vpc import VpcEnv
from .dns import DnsZone
from .security_group import FrontEndSecurityGroup
from .ec2_keypair import Ec2KeyPair
from .user_data import (
    UserData,
    UserDataPart,
    CloudInitDoc,
    CloudInitPart,
    CloudInitDocConvertible,
    CloudInitPartConvertible,
    CloudInitRenderable,
    UserDataConvertible,
    UserDataPartConvertible,
    MimeHeadersConvertible,
    render_user_data_text,
    render_user_data_base64,
    render_user_data_binary,
    render_cloud_init_binary,
    render_cloud_init_base64,
    render_cloud_init_text,
  )
from .ec2_instance import (
    Ec2Instance,
  )
from .cloudwatch import CloudWatch
from ..runtime_support import (
    HashedPasswordProvider,
    HashedPassword,
    S3FutureObject,
    S3FutureObjectProvider,
    SshCachedHostKey,
    SshCachedHostKeyProvider,
  )
