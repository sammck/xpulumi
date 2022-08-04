from ..internal_types import Jsonable, JsonableDict
from ..util import split_s3_uri
from typing import Any, Optional, List, cast
import json

from pulumi.dynamic import ResourceProvider, CreateResult, Resource, DiffResult, UpdateResult, CheckResult, CheckFailure
from pulumi import ResourceOptions, Input, Output
import pulumi

from ..s3_object_waiter import (
    DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
    DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS,
    sync_wait_and_get_s3_object
  )

_DEBUG_PROVIDER = False

class S3FutureObjectProvider(ResourceProvider):
  def _gen_outs(self, props: JsonableDict) -> JsonableDict:
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider._gen_outs(props={props})")
    uri = cast(str, props['uri'])
    aws_region = cast(str, props['aws_region'])
    max_wait_seconds = cast(float, props['max_wait_seconds'])
    poll_interval = cast(float, props['poll_interval'])
    content = sync_wait_and_get_s3_object(
      uri=uri,
      region_name=aws_region,
      max_wait_seconds=max_wait_seconds,
      poll_interval=poll_interval,
    ).decode('utf-8')
    result: JsonableDict = dict(
        uri=uri,
        aws_region=aws_region,
        content=content
      )
    return result

  def check(self, oldProps: JsonableDict, newProps: JsonableDict) -> CheckResult:   # pylint: disable=arguments-renamed
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.check(oldProps={oldProps}, newProps={newProps})")
    old_uri = oldProps.get('uri', None)
    uri = newProps.get('uri', None)
    aws_region = newProps.get('aws_region', None)
    if aws_region is None:
      aws_region = 'us-east-1'
    max_wait_seconds = newProps.get('max_wait_seconds', None)
    if max_wait_seconds is None:
      max_wait_seconds = DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS
    if isinstance(max_wait_seconds, int):
      max_wait_seconds = float(max_wait_seconds)
    poll_interval = newProps.get('poll_interval', None)
    if poll_interval is None:
      poll_interval = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS
    if isinstance(poll_interval, int):
      poll_interval = float(poll_interval)

    failures: List[CheckFailure] = []
    if not isinstance(uri, pulumi.output.Unknown):
      if not isinstance(uri, str):
        failures.append(CheckFailure('uri', f'uri must be a string: {uri}'))
      try:
        split_s3_uri(cast(str, uri))
      except Exception:
        failures.append(CheckFailure('uri', f'uri must be a valid S3 uri: {uri}'))
      if not old_uri is None and uri != old_uri:
        failures.append(CheckFailure('name', f'uri property cannot be changed: {uri}'))
    if not isinstance(aws_region, str) or aws_region == '':
      failures.append(CheckFailure('aws_region', f'aws_region must be None or a nonempty string: {aws_region}'))
    if not isinstance(max_wait_seconds, float):
      failures.append(CheckFailure('max_wait_seconds', 'max_wait_seconds must be a float'))
    if not isinstance(poll_interval, float) or poll_interval < 0:
      failures.append(CheckFailure('poll_interval', 'poll_interval must be a float >= 0'))

    inputs = dict(uri=uri, aws_region=aws_region, max_wait_seconds=max_wait_seconds, poll_interval=poll_interval)

    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.check() ==> CheckResult(inputs={inputs}, failures={failures})")
    return CheckResult(inputs, failures)

  def create(self, props: JsonableDict) -> CreateResult:
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.create(props={props})")
      # we will use the URI as the unique ID
      uri = cast(str, props["uri"])
      outs = self._gen_outs(props)
      if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.create() ==> CreateResult(id={uri}, outs={outs})")
    except Exception as e:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"S3FutureObjectProvider.create() ==> Exception: {repr(e)}")
      raise
    return CreateResult(uri, outs)

  def update(self, id: str, oldProps: JsonableDict, newProps: JsonableDict):  # pylint: disable=redefined-builtin
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.update(id={id}, oldProps={oldProps}, newProps={newProps})") # pylint: disable=redefined-builtin
    outs = self._gen_outs(newProps)
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.update() ==> UpdateResult(outs={outs})")
    return UpdateResult(outs)

  def diff(self, id: str, oldProps: JsonableDict, newProps: JsonableDict) -> DiffResult:   # pylint: disable=redefined-builtin
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.diff(id={id}, oldProps={oldProps}, newProps={newProps})")
    replaces: List[str] = []
    stables: List[str] = []
    # We should only generate a new output if the uri changed.
    changes: bool = oldProps['uri'] != newProps['uri']
    if changes:
      replaces.append('content')
    else:
      stables.append('content')
      stables.append('uri')
    if _DEBUG_PROVIDER: pulumi.log.info(f"S3FutureObjectProvider.diff() ==> DiffResult(changes={changes}, replaces={replaces}, stables={stables})")
    return DiffResult(changes=changes, replaces=replaces, stables=stables)

class S3FutureObject(Resource):
  uri: Output[str]
  aws_region: Output[Optional[str]]
  content: Output[Optional[str]]

  def __init__(
        self,
        name: str,
        uri: Input[str],
        aws_region: Input[Optional[str]]=None,
        max_wait_seconds: float=DEFAULT_S3_OBJECT_WAIT_TIMEOUT_SECONDS,
        poll_interval: float = DEFAULT_S3_OBJECT_POLL_INTERVAL_SECONDS,
        opts: Optional[ResourceOptions]=None,
      ):

    super().__init__(
        S3FutureObjectProvider(),
        name,
        # NOTE: Pulumi doesn't populate output properties unless they are also inputs...
        dict(
            uri=uri,
            aws_region=aws_region,
            max_wait_seconds=max_wait_seconds,
            poll_interval=poll_interval,
            content=None
          ),
        opts=opts
      )

  def get_json_content(self) -> Output[Jsonable]:
    result: Output[Jsonable] = self.content.apply(lambda x: None if x is None else json.loads(x))
    return result
