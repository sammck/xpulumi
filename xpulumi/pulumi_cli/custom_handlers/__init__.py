from typing import Dict, Type, TYPE_CHECKING

from .preview_up import PulumiCmdHandlerUp, PulumiCmdHandlerPreview
from .destroy import PulumiCmdHandlerDestroy
from .stack_rm import PulumiCmdHandlerStackRm
from .stack_ls import PulumiCmdHandlerStackLs
from .refresh import PulumiCmdHandlerRefresh

from ..wrapper import PulumiCommandHandler

custom_handlers: Dict[str, Type[PulumiCommandHandler]] = {
    "up": PulumiCmdHandlerUp,
    "preview": PulumiCmdHandlerPreview,
    "destroy": PulumiCmdHandlerDestroy,
    "refresh": PulumiCmdHandlerRefresh,
    "stack rm": PulumiCmdHandlerStackRm,
    "stack ls": PulumiCmdHandlerStackLs,
  }
