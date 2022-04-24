from typing import Dict, Type, TYPE_CHECKING

if TYPE_CHECKING:
  from ..wrapper import PulumiCommandHandler

def get_custom_handlers() -> Dict[str, Type['PulumiCommandHandler']]:
  from ..wrapper import PulumiCommandHandler
  from .preview_up import PulumiCmdHandlerUp, PulumiCmdHandlerPreview
  from .destroy import PulumiCmdHandlerDestroy
  from .stack_rm import PulumiCmdHandlerStackRm
  from .stack_ls import PulumiCmdHandlerStackLs
  from .refresh import PulumiCmdHandlerRefresh


  custom_handlers: Dict[str, Type[PulumiCommandHandler]] = {
      "up": PulumiCmdHandlerUp,
      "preview": PulumiCmdHandlerPreview,
      "destroy": PulumiCmdHandlerDestroy,
      "refresh": PulumiCmdHandlerRefresh,
      "stack rm": PulumiCmdHandlerStackRm,
      "stack ls": PulumiCmdHandlerStackLs,
    }
  return custom_handlers
