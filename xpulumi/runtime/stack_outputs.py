# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Promise-based fetching of external xpulumi stack outputs"""

from typing import Optional, Any, List, Callable, Mapping, Iterator, Iterable, Tuple, Union

import os

from pulumi import Output, Input, get_stack
from xpulumi.exceptions import XPulumiError

from xpulumi.project import XPulumiProject
from .. import JsonableDict, Jsonable
from .. import XPulumiStack
from .util import get_xpulumi_stack


class SyncStackOutputs:
  _stack: XPulumiStack
  """The xpulumi stack"""

  _outputs: JsonableDict
  """The actual outputs of the stack"""

  def __init__(
        self,
        stack_name: Optional[str]=None,
        stack: Optional[XPulumiStack]=None,
        project_name: Optional[str]=None,
        default_stack_name: Optional[str]=None,
        default_project_name: Optional[str]=None,
        decrypt_secrets: bool=False
      ) -> None:
    if stack is None:
      stack = get_xpulumi_stack(stack_name=stack_name, project_name=project_name)
    outputs = stack.get_stack_outputs(decrypt_secrets=decrypt_secrets)
    self._stack = stack
    self._outputs = outputs

  @property
  def stack(self) -> XPulumiStack:
    return self._stack

  @property
  def outputs(self) -> JsonableDict:
    return self._outputs

  def get_output(self, name: str, default: Jsonable=None) -> Jsonable:
    return self.outputs.get(name, default)
  
  def require_output(self, name: str) -> Jsonable:
    return self.outputs[name]

  def __getitem__(self, key: str) -> Jsonable:
    return self.require_output(key)

  def __len__(self) -> int:
    return len(self.outputs)

  def __contains__(self, key: str) -> bool:
    return key in self.outputs

  def __iter__(self) -> Iterator[str]:
    return iter(self.outputs)

  def keys(self) -> Iterable[str]:
    return self.outputs.keys()
  
  def values(self) -> Iterable[Jsonable]:
    return self.outputs.values()
  
  def items(self) -> Iterable[Tuple[str, Jsonable]]:
    return self.outputs.items()

class StackOutputs:
  """An encapsulation of a promise to fetch the outputs of an external deployed xpulunmi stack.

  An instance of this class can provide promises for specific output values, or for a JsonableDict
  containing all output values of the stack.
  """
  _future_outputs: Output[SyncStackOutputs]

  def __init__(
        self,
        stack_name: Input[Optional[str]]=None,
        project_name: Input[Optional[str]]=None,
        decrypt_secrets: Input[bool]=False
      ) -> None:
    """Fetch the outputs of an external deployed xpulumi stack. The returned
       StackOutputs object can provide promises for individual outputs
       as well as all outputs as a JsonableDict.

    Args:
        stack_name (Input[Optional[str]], optional): 
                      The stack name within the xpulumi project, or None to use the
                      same stack name as the current project. Defaults to None.
        project_name (Input[Optional[str]], optional):
                      The local xpulumi project name, or None to use the same project
                      as the current stack. Default is None.
        decrypt_secrets (Input[bool], optional):
                      True if secret outputs should be decrypted. Defaults to False.
    """
    self._future_outputs = Output.all(stack_name, project_name, decrypt_secrets).apply(lambda args: self._resolve(*args))

  def _resolve(self, stack_name: str, project_name: Optional[str], decrypt_secrets: bool) -> SyncStackOutputs:
    result = SyncStackOutputs(stack_name=stack_name, project_name=project_name, decrypt_secrets=decrypt_secrets)
    return result

  def get_outputs(self) -> Output[JsonableDict]:
    return Output.all(self._future_outputs).apply(lambda args: args[0].outputs)
  
  def get_output(self, name: Input[str], default: Input[Jsonable]=None) -> Output[Jsonable]:
    return Output.all(self._future_outputs, name, default).apply(lambda args: args[0].get_output(args[1], default=args[2]))
  
  def require_output(self, name: Input[str]) -> Output[Jsonable]:
    return Output.all(self._future_outputs, name).apply(lambda args: args[0].require_output(args[1]))

  def __getitem__(self, key: Input[str]) -> Output[Jsonable]:
    return self.require_output(key)

  def __len__(self) -> Output[int]:
    return Output.all(self._future_outputs).apply(lambda args: len(args[0]))

  def __contains__(self, key: Input[str]) -> Output[bool]:
    return Output.all(self._future_outputs, key).apply(lambda args: args[1] in args[0])

  def keys(self) -> Output[Iterable[str]]:
    return Output.all(self._future_outputs).apply(lambda args: args[0].keys())
  
  def values(self) -> Output[Iterable[Jsonable]]:
    return Output.all(self._future_outputs).apply(lambda args: args[0].values())
  
  def items(self) -> Output[Iterable[Tuple[str, Jsonable]]]:
    return Output.all(self._future_outputs).apply(lambda args: args[0].items())

def get_normalized_stack(
      stack: Optional[Union[str, XPulumiStack]]=None,
      project: Optional[Union[str, XPulumiProject]]=None,
    ) -> XPulumiStack:
  pstack: XPulumiStack
  if isinstance(stack, XPulumiStack):
    pstack = stack
  else:
    stack_name: Optional[str] = stack
    project_name: Optional[str]
    if isinstance(project, XPulumiProject):
      project_name = project.name
    else:
      project_name = project
    pstack = get_xpulumi_stack(stack_name, project_name)
  return pstack

def get_stack_outputs(
      stack: Optional[Union[str, XPulumiStack]]=None,
      project: Optional[Union[str, XPulumiProject]]=None,
      decrypt_secrets: bool=False,
      bypass_pulumi: bool=True
    ) -> JsonableDict:
  pstack = get_normalized_stack(stack, project)
  result = pstack.get_stack_outputs(decrypt_secrets=decrypt_secrets, bypass_pulumi=bypass_pulumi)
  return result

def get_stack_output(
      output_name: str,
      default: Jsonable=None,
      stack: Optional[Union[str, XPulumiStack]]=None,
      project: Optional[Union[str, XPulumiProject]]=None,
      decrypt_secrets: bool=False,
      bypass_pulumi: bool=True
    ) -> Jsonable:
  outputs = get_stack_outputs(stack=stack, project=project, decrypt_secrets=decrypt_secrets, bypass_pulumi=bypass_pulumi)
  result: Jsonable = outputs.get(output_name, default)
  return result

def require_stack_output(
      output_name: str,
      stack: Optional[Union[str, XPulumiStack]]=None,
      project: Optional[Union[str, XPulumiProject]]=None,
      decrypt_secrets: bool=False,
      bypass_pulumi: bool=True
    ) -> Jsonable:
  
  pstack = get_normalized_stack(stack, project)
  outputs = pstack.get_stack_outputs(decrypt_secrets=decrypt_secrets, bypass_pulumi=bypass_pulumi)
  if not output_name in outputs:
    raise XPulumiError(f"Output \"{output_name}\" does not exist in stack \"{pstack.full_stack_name}\"")
  result: Jsonable = outputs[output_name]
  return result
