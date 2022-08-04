import pulumi

#pulumi.info(f"Loading {__name__}")

from ..internal_types import Jsonable, JsonableDict
from ..util import split_s3_uri
from typing import Any, Optional, List, cast, Tuple, TYPE_CHECKING
import json
import subprocess
import os
import socket

from pulumi.dynamic import ResourceProvider, CreateResult, Resource, DiffResult, UpdateResult, CheckResult, CheckFailure
from pulumi import ResourceOptions, Input, Output

if TYPE_CHECKING:
  from .s3_future_object_provider import S3FutureObject

_DEBUG_PROVIDER = False

class CmdError(Exception):
  pass

def _run_cmd(args: List[str]) -> str:
  with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
    (stdout_bytes, _) = proc.communicate()
    stdout_s = stdout_bytes.decode('utf-8')
    exit_code = proc.returncode
  if exit_code != 0:
    raise CmdError(f"SShCachedHostKey: {args} failed with exit code {exit_code}: {stdout_s}")
  return stdout_s

def _run_cmd_separate(args: List[str]) -> Tuple[str, str]:
  with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
    (stdout_bytes, stderr_bytes) = proc.communicate()
    stdout_s = stdout_bytes.decode('utf-8')
    stderr_s = stderr_bytes.decode('utf-8')
    exit_code = proc.returncode
  if exit_code != 0:
    raise CmdError(f"SShCachedHostKey: {args} failed with exit code {exit_code}: {stderr_s}")
  return stdout_s, stderr_s

def _remove_entry(name: str) -> str:
  known_hosts = os.path.expanduser("~/.ssh/known_hosts")
  result = _run_cmd(["ssh-keygen", "-f", known_hosts, "-R", name])
  return result

def _get_ip_address_of_dns_name(dns_name: str) -> str:
  result = socket.gethostbyname(dns_name)
  return result

def _scan_hosts(hostname: str) -> Tuple[str, str]:
  stdout_s, stderr_s = _run_cmd_separate(["ssh-keyscan", "-H", hostname])
  return stdout_s, stderr_s

def update_host_keys(ip_address: Optional[str]=None, dns_name: Optional[str] = None) -> str:
  result: str = ""
  if not ip_address is None:
    result += _remove_entry(ip_address)
  if not dns_name is None:
    result += _remove_entry(dns_name)
    ip2 = _get_ip_address_of_dns_name(dns_name)
    if ip2 != ip_address:
      result += _remove_entry(ip2)
  hostname = dns_name if not dns_name is None else ip_address
  if not hostname is None:
    new_hosts, log_text = _scan_hosts(hostname)
    result += log_text
    if len(new_hosts) > 0:
      known_hosts = os.path.expanduser("~/.ssh/known_hosts")
      if not new_hosts.endswith('\n'):
        new_hosts += '\n'
      with open(known_hosts, 'a', encoding='utf-8') as f:
        f.write(new_hosts)
  return result

class SshCachedHostKeyProvider(ResourceProvider):
  def _gen_outs(self, props: JsonableDict) -> JsonableDict:
    if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider._gen_outs(props={props})")

    instance_id = cast(str, props['instance_id'])
    ip_address = cast(Optional[str], props.get('ip_address', None))
    dns_name = cast(Optional[str], props.get('dns_name', None))

    log_text = update_host_keys(ip_address=ip_address, dns_name=dns_name)
    if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKey: cmd output={log_text}")

    result: JsonableDict = dict(
        instance_id = instance_id,
        ip_address = ip_address,
        dns_name = dns_name,
        cmd_out=log_text
      )
    return result

  def check(self, oldProps: JsonableDict, newProps: JsonableDict) -> CheckResult:   # pylint: disable=arguments-renamed
    if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.check(oldProps={oldProps}, newProps={newProps})")
    instance_id = newProps.get('instance_id', None)
    ip_address = newProps.get('ip_address', None)
    dns_name = newProps.get('dns_name', None)

    failures: List[CheckFailure] = []
    if not isinstance(instance_id, pulumi.output.Unknown):
      if not isinstance(instance_id, str):
        failures.append(CheckFailure('instance_id', f'instance_id must be a string: {instance_id}'))
    if not isinstance(ip_address, pulumi.output.Unknown):
      if not ip_address is None and not isinstance(ip_address, str):
        failures.append(CheckFailure('ip_address', f'ip_address must be None or a string: {ip_address}'))
    if not isinstance(dns_name, pulumi.output.Unknown):
      if not dns_name is None and not isinstance(dns_name, str):
        failures.append(CheckFailure('dns_name', f'dns_name must be None or a string: {dns_name}'))
    inputs = dict(instance_id=instance_id, ip_address=ip_address, dns_name=dns_name)

    if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.check() ==> CheckResult(inputs={inputs}, failures={failures})")
    return CheckResult(inputs, failures)

  def create(self, props: JsonableDict) -> CreateResult:
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.create(props={props})")
      instance_id = cast(str, props["instance_id"])
      ip_address = cast(Optional[str], props.get("ip_address", None))
      dns_name = cast(Optional[str], props.get("dns_name", None))
      rid = f"ssh-hostkey-{instance_id}"
      if not dns_name is None:
        rid += f"-{dns_name}"
      if not ip_address is None:
        rid += f"-{ip_address}"

      outs = self._gen_outs(props)
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.create() ==> CreateResult(id={rid}, outs={outs})")
    except Exception as e:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"SshCachedHostKeyProvider.create() ==> Exception: {repr(e)}")
      raise
    return CreateResult(rid, outs)

  def update(self, id: str, oldProps: JsonableDict, newProps: JsonableDict):  # pylint: disable=redefined-builtin
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.update(id={id}, oldProps={oldProps}, newProps={newProps})") # pylint: disable=redefined-builtin
      outs = self._gen_outs(newProps)
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.update() ==> UpdateResult(outs={outs})")
    except Exception as e:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"SshCachedHostKeyProvider.update() ==> Exception: {repr(e)}")
      raise
    return UpdateResult(outs)

  def diff(self, id: str, oldProps: JsonableDict, newProps: JsonableDict) -> DiffResult:   # pylint: disable=redefined-builtin
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.diff(id={id}, oldProps={oldProps}, newProps={newProps})")
      replaces: List[str] = []
      stables: List[str] = []
      for propname in ['instance_id', 'ip_address', 'dns_name']:
        if oldProps.get(propname, None) != newProps.get(propname, None):
          if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.diff() : {propname}: {oldProps.get(propname, None)} != {newProps.get(propname, None)}")
          replaces.append(propname)
      changes = len(replaces) > 0
      if changes:
        replaces.append('cmd_out')
      else:
        stables.append('cmd_out')
      if _DEBUG_PROVIDER: pulumi.log.info(f"SshCachedHostKeyProvider.diff() ==> DiffResult(changes={changes}, replaces={replaces}, stables={stables})")
    except Exception as e:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"SshCachedHostKeyProvider.diff() ==> Exception: {repr(e)}")
      raise
    return DiffResult(changes=changes, replaces=replaces, stables=stables)

class SshCachedHostKey(Resource):
  instance_id: Output[str]
  ip_address: Output[Optional[str]]
  dns_name: Output[Optional[str]]
  cloudinit_result_str: Output[str]
  cmd_out: Output[str]

  def __init__(
        self,
        name: str,
        instance_id: Input[str],
        ip_address: Input[Optional[str]]=None,
        dns_name: Input[Optional[str]]=None,
        cloudinit_result: Optional['S3FutureObject']=None,
        opts: Optional[ResourceOptions]=None,
      ):

    super().__init__(
        SshCachedHostKeyProvider(),
        name,
        # NOTE: Pulumi doesn't populate output properties unless they are also inputs...
        dict(
            instance_id=instance_id,
            ip_address=ip_address,
            dns_name=dns_name,
            cloudinit_result_str='None' if cloudinit_result is None else cloudinit_result.content,
            cmd_out=None,
          ),
        opts=opts
      )

#pulumi.info(f"Done Loading {__name__}")
