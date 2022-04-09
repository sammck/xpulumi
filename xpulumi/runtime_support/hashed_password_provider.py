from ..internal_types import JsonableDict
from project_init_tools import gen_etc_shadow_password_hash as sync_gen_etc_shadow_password_hash
from typing import Any, Optional, List, cast
from pulumi.dynamic import ResourceProvider, CreateResult, Resource, DiffResult, UpdateResult, CheckResult, CheckFailure
from pulumi import ResourceOptions, Input, Output
import pulumi

_DEBUG_PROVIDER = False

class HashedPasswordProvider(ResourceProvider):
  def _gen_outs(self, name: str, password: str) -> JsonableDict:
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider._gen_outs(name={name}, password={password})")
    hashed_password = sync_gen_etc_shadow_password_hash(password)
    result: JsonableDict = dict(
        name=name,
        password=password,
        hashed_password=hashed_password,
      )
    return result

  def check(self, oldProps: JsonableDict, newProps: JsonableDict) -> CheckResult:  # pylint: disable=arguments-renamed
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.check(oldProps={oldProps}, newProps={newProps})")
    old_name = oldProps.get('name', None)
    name = newProps.get('name', None)
    password = newProps.get('password', None)
    failures: List[CheckFailure] = []
    if not isinstance(name, str):
      failures.append(CheckFailure('name', f'name must be a string: {name}'))
    if not old_name is None and name != old_name:
      failures.append(CheckFailure('name', f'name property cannot be changed: {name}'))
    if not isinstance(password, str) or password == '':
      failures.append(CheckFailure('password', f'Password must be a nonempty string: {password}'))
    if password == '[secret]':
      failures.append(CheckFailure('password', f'Password is set to the literal string "[secret]", which indicates a pulumi property serialization bug: {password}'))

    inputs = dict(name=name, password=password)

    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.check() ==> CheckResult(inputs={inputs}, failures={failures})")
    return CheckResult(inputs, failures)

  def create(self, props: JsonableDict) -> CreateResult:
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.create(props={props})")
      # since we don't have a unique ID, use the resource name provided
      # by the caller
      rid = cast(str, props["name"])
      outs = self._gen_outs(rid, cast(str, props['password']))
      pulumi.log.info(f"HashedPasswordProvider.create() ==> CreateResult(id={rid}, outs={outs})")
    except Exception as e:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"HashedPasswordProvider.create() ==> Exception: {repr(e)}")
      raise
    return CreateResult(rid, outs)

  def update(self, id: str, oldProps: JsonableDict, newProps: JsonableDict): # pylint: disable=redefined-builtin
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.update(oldProps={oldProps}, newProps={newProps})")
    outs = self._gen_outs(cast(str, newProps['name']), cast(str, newProps['password']))
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.update() ==> UpdateResult(outs={outs})")
    return UpdateResult(outs)

  def diff(self, id: str, oldProps: JsonableDict, newProps: JsonableDict) -> DiffResult:   # pylint: disable=redefined-builtin
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.diff(oldProps={oldProps}, newProps={newProps})")
    replaces: List[str] = []
    stables: List[str] = [ 'name' ]
    # We should only generate a new output if the input changes.
    changes: bool = oldProps['password'] != newProps['password']
    if changes:
      replaces.append('hashed_password')
    else:
      stables.append('hashed_password')
    if _DEBUG_PROVIDER: pulumi.log.info(f"HashedPasswordProvider.diff() ==> DiffResult(changes={changes}, replaces={replaces}, stables={stables})")
    return DiffResult(changes=changes, replaces=replaces, stables=stables)

class HashedPassword(Resource):
  name: Output[str]
  password: Output[str]
  hashed_password: Output[str]

  def __init__(
        self,
        name: str,
        password: Input[str],
        opts: Optional[ResourceOptions] = None
      ):
    if opts is None:
      opts = ResourceOptions(additional_secret_outputs=['password', 'hashed_password'])
    assert isinstance(name, str)
    super().__init__(
        HashedPasswordProvider(),
        name,
        # NOTE: Pulumi doesn't populate output properties unless they are also inputs...
        dict(
            name=name,
            password=password,
            hashed_password=None
          ),
        opts=opts
      )
