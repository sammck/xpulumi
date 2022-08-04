from project_init_tools import full_name_of_type, full_type
from ..internal_types import JsonableDict
#from project_init_tools import gen_etc_shadow_password_hash as sync_gen_etc_shadow_password_hash
from typing import Any, Optional, List, cast
from pulumi.dynamic import ResourceProvider, CreateResult, Resource, DiffResult, UpdateResult, CheckResult, CheckFailure
from pulumi import ResourceOptions, Input, Output
import pulumi

import Cryptodome
from Cryptodome.Protocol.KDF import PBKDF2
from Cryptodome.Hash import SHA256
from Cryptodome.Cipher import AES
from Cryptodome.Cipher._mode_gcm import GcmMode
from Cryptodome.Random import get_random_bytes
from base64 import b64encode, b64decode
from binascii import hexlify
import sys
import json
import traceback

from pulumi_crypto import (
    generate_key,
    encrypt_string,
    decrypt_string,
    generate_nonce,
    KEY_SIZE_BYTES
  )

_DEBUG_PROVIDER = False

class EncryptedStringProvider(ResourceProvider):
  def _gen_outs(
        self,
        name: str,
        plaintext: str,
        input_key_b64: Optional[str],
        input_key_revision: Optional[int],
        old_key_b64: Optional[str]=None,
        old_key_revision: int=0
      ) -> JsonableDict:
    if _DEBUG_PROVIDER: pulumi.log.info(
        f"EncryptedStringProvider._gen_outs(name={name}, plaintext={' '.join(plaintext)}, "
        f"input_key={' '.join(str(input_key_b64))}), old_key={' '.join(str(old_key_b64))})"
      )
    assert isinstance(name, str)
    assert input_key_b64 is None or isinstance(input_key_b64, str)
    if isinstance(input_key_revision, float):
      input_key_revision = round(input_key_revision)
    assert input_key_revision is None or isinstance(input_key_revision, int)
    assert old_key_b64 is None or isinstance(old_key_b64, str)
    if isinstance(old_key_revision, float):
      old_key_revision = round(old_key_revision)
    assert old_key_revision is None or isinstance(old_key_revision, int)
    if not input_key_b64 is None:
      key_b64 = input_key_b64
      key = b64decode(input_key_b64)
    elif not old_key_b64 is None and (input_key_revision is None or input_key_revision == old_key_revision):
      key_b64 = old_key_b64
      key = b64decode(old_key_b64)
    else:
      key = generate_key()
      key_b64 = b64encode(key).decode('utf-8')
    if input_key_revision is None:
      key_revision = old_key_revision if key_b64 == old_key_b64 else old_key_revision + 1
    else:
      key_revision = input_key_revision
    if len(key) != KEY_SIZE_BYTES:
      raise ValueError(f"Wrong key size for EncryptedStringProvider, expected {KEY_SIZE_BYTES} bytes, got {len(key)}")
    nonce = generate_nonce()
    if _DEBUG_PROVIDER: pulumi.log.info(
        f"EncryptedStringProvider._gen_outs(binary key={' '.join(str(key))}, nonce={' '.join(str(nonce))})")
    ciphertext = encrypt_string(plaintext, key, nonce=nonce)
    if _DEBUG_PROVIDER: pulumi.log.info(
        f"EncryptedStringProvider._gen_outs(ciphertext={' '.join(ciphertext)})")

    result: JsonableDict = dict(
        name=name,
        key_b64=key_b64,
        key_revision=key_revision,
        plaintext=plaintext,
        ciphertext=ciphertext,
      )
    return result

  def check(self, oldInputs: JsonableDict, newRawInputs: JsonableDict) -> CheckResult:  # pylint: disable=arguments-renamed
    """Called before create, diff, or update to normalize inputs

    Args:
        oldInputs (JsonableDict):
                Dict of previous inputs as returned by check() during the previous successful deployment. This will be an empty
                dict if the resource has not been created.
        newRawInputs (JsonableDict):
                Dict of current inputs as passed to Resource.__init__(). Not yet passed through check().

    Returns:
        CheckResult: Encapsulates two elements:
                       * A list of bad current input properties with reasons for why they are not OK
                       * a dict of normalized inputs.  These are the inputs that will be passed
                         to create(), update(), or diff().
    """
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.check(oldInputs={oldInputs}, newRawInputs={newRawInputs})")
      failures: List[CheckFailure] = []
      old_name = cast(Optional[str], oldInputs.get('name', None))
      name = cast(Optional[str], newRawInputs.get('name', None))
      plaintext = cast(Optional[str], newRawInputs.get('plaintext', None))
      input_key_b64 = cast(Optional[str], newRawInputs.get('input_key_b64', None))
      input_key_revision = cast(Optional[int], newRawInputs.get("input_key_revision", None))
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.check(): plaintext={None if plaintext is None else ' '.join(str(plaintext))}")
      if not isinstance(name, str):
        failures.append(CheckFailure('name', f'name must be a string: {name}'))
      if not old_name is None and name != old_name:
        failures.append(CheckFailure('name', f'name property cannot be changed: {name}'))
      if not isinstance(plaintext, str):
        failures.append(CheckFailure('plaintext', f'Plaintext must be a string: {plaintext}'))
      if not input_key_b64 is None:
        if not isinstance(input_key_b64, str):
          failures.append(CheckFailure('input_key_b64', f'Key must be None or a string value, got {full_type(input_key)}'))
        else:
          try:
            input_key = b64decode(input_key_b64)
            if len(input_key) != KEY_SIZE_BYTES:
              failures.append(CheckFailure('input_key_b64',
                  f"Wrong key size for EncryptedStringProvider, "
                  f"expected {KEY_SIZE_BYTES} bytes, got {len(input_key)}"))
          except Exception:
            failures.append(CheckFailure('input_key_b64', f"Invalid base-64 encoding"))
      if isinstance(input_key_revision, float):
        input_key_revision = round(input_key_revision)
      if not input_key_revision is None and not isinstance(input_key_revision, int):
        failures.append(CheckFailure('input_key_revision',
            f"Key revision number must be None or an integer, got "
            f"{full_type(input_key_revision)}: {input_key_revision}"))

      # Return a dict of inputs as they should be passed to diff, update, or create
      inputs = dict(name=name, plaintext=plaintext, input_key_b64=input_key_b64, input_key_revision=input_key_revision)

      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.check() ==> CheckResult(inputs={inputs}, failures={failures})")
      return CheckResult(inputs, failures)
    except Exception:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"EncryptedStringProvider.check() ==> Exception: {traceback.format_exc()}")
      raise

  def create(self, newInputs: JsonableDict) -> CreateResult:     # pylint: disable=arguments-renamed
    """Called to create a new instance of the resource and return its output properties.

    Args:
        newInputs (JsonableDict): dict of current inputs as returned from check()

    Returns:
        CreateResult: Encapsulation of:
                        * The resource ID
                        * a dict of output properties
    """
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.create(newInputs={newInputs})")
      # since we don't have a unique ID, use the resource name provided
      # by the caller
      rid: str = newInputs["name"]
      plaintext: str = newInputs['plaintext']
      key_b64: Optional[str] = newInputs.get('input_key_b64', None)
      key_revision: Optional[int] = newInputs.get('input_key_revision', None)
      outs = self._gen_outs(rid, plaintext, key_b64, key_revision)
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.create() ==> CreateResult(id={rid}, outs={outs})")
      return CreateResult(rid, outs)
    except Exception:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"EncryptedStringProvider.create() ==> Exception: {traceback.format_exc()}")
      raise

  def update(self, id: str, oldOutputs: JsonableDict, newInputs: JsonableDict): # pylint: disable=redefined-builtin
    """Called to update the resource and return its new output properties.

    Args:
        id(str): The resource ID, as returned from create()
        oldOutputs (JsonableDict): dict of previously deployed outputs as returned from create() or update()
        newInputs (JsonableDict): dict of current inputs as returned from check()

    Returns:
        UpdateResult: Encapsulation of a dict of output properties
    """
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.update(oldOutputs={oldOutputs}, newInputs={newInputs})")
      rid: str = newInputs["name"]
      plaintext: str = newInputs['plaintext']
      key_b64: Optional[str] = newInputs.get('input_key_b64', None)
      input_key_revision: Optional[int] = newInputs.get('input_key_revision', None)
      old_key_b64: str = oldOutputs['key_b64']
      old_key_revision: int = oldOutputs.get('key_revision', 0)
      outs = self._gen_outs(
          rid,
          plaintext,
          key_b64,
          input_key_revision,
          old_key_b64,
          old_key_revision
        )
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.update() ==> UpdateResult(outs={outs})")
      return UpdateResult(outs)
    except Exception:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"EncryptedStringProvider.update() ==> Exception: {traceback.format_exc()}")
      raise

  def diff(self, id: str, oldOutputs: JsonableDict, newInputs: JsonableDict) -> DiffResult:   # pylint: disable=redefined-builtin
    """Called to decide if the resource needs to be replaced or updated

    Args:
        id (str): The resource ID as returned from create()
        oldOutputs (JsonableDict): dict of previously deployed outputs as returned from create() or update()
        newInputs (JsonableDict): dict of current inputs as returned from check()

    Returns:
        DiffResult: Encapsulation of:
           * "changes": A boolean indicating either a replacement or an update is necessary
           * "replaces": A list of property names for properties that have changed requiring a replacement, If an empty
                         list, then an update-in-place is selected.
           * "stables": A list of property names for properties that are known to remain stable if this up/replace is
                        performed. This allows promise dependencies based on these properties to be evaluated without
                        waiting for update; e.g., during "preview".
    """
    try:
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.diff(oldOutputs={oldOutputs}, newInputs={newInputs})")
      replaces: List[str] = []
      stables: List[str] = []
      # We should only generate a new output if the input changes.
      plaintext_changes: bool = oldOutputs['plaintext'] != newInputs['plaintext']
      input_key_b64: Optional[str] = newInputs.get('input_key_b64', None)
      old_key_b64: str = oldOutputs['key_b64']
      input_key_revision: Optional[int] = newInputs.get('input_key_revision', None)
      if isinstance(input_key_revision, float):
        input_key_revision = round(input_key_revision)
      old_key_revision: int = oldOutputs.get('key_revision', 0)
      if isinstance(old_key_revision, float):
        old_key_revision = round(old_key_revision)
      key_revision_changes = not input_key_revision is None and input_key_revision != old_key_revision
      key_changes = key_revision_changes or (
          not input_key_b64 is None and input_key_b64 != old_key_b64
        )
      key_revision_changes = key_revision_changes or (input_key_revision is None and key_changes)
      changes = plaintext_changes or key_changes or key_revision_changes
      stables.append('name')
      if not plaintext_changes:
        stables.append('plaintext')
      if not key_changes:
        stables.append('key_b64')
      if not key_revision_changes:
        stables.append('key_revision')
      if not changes:
        stables.append('ciphertext')
      if _DEBUG_PROVIDER: pulumi.log.info(f"EncryptedStringProvider.diff() ==> DiffResult(changes={changes}, replaces={replaces}, stables={stables})")
      return DiffResult(changes=changes, replaces=replaces, stables=stables)
    except Exception:
      if _DEBUG_PROVIDER: pulumi.log.warn(f"EncryptedStringProvider.diff() ==> Exception: {traceback.format_exc()}")
      raise

class EncryptedString(Resource):
  """Provides an encrypted form of a string based on a 256-bit AES symmetric key.

  The resulting string can be decrypted with pulumi_crypto.decrypt_string().
  """

  name: Output[str]
  """The unique name of this resource"""

  key_revision: Output[int]
  """A sequence number that if changed will force the key to be regenerated"""

  key_b64: Output[str]
  """The random 256-bit (32-byte) symmetric AES encryption key. Marked secret."""

  plaintext: Output[str]
  """The unencrypted plaintext string. Marked secret."""

  ciphertext: Output[str]
  """The encrypted ciphertext string"""

  @property
  def key(self) -> Output[bytes]:
    return self.key_b64.apply(lambda x: b64decode(x))

  def __init__(
        self,
        name: str,
        plaintext: Input[str],
        key: Input[Optional[bytes]] = None,
        key_revision: Optional[int]=None,
        opts: Optional[ResourceOptions] = None
      ):
    """Create an ecrypted string resource.

    The resulting ciphertext output property can be decrypted by
    anyone with the key, using pulumi_crypto.decrypt_string()

    Args:
        name (str):   The unique stack resource name
        plaintext (Input[str]):
                      The unencrypted text, should generally be marked secret.
        key (Input[Optional[bytes]]):
                      An optional 256-bit (32-byte) AES symmetric key. Should
                      generally be marked secret. If None, a random key will
                      be generated and exposed in output property "key". Default
                      is None
        key_revision (Input[Optional[int]]):
                      An optional int identifying a revision number for the key.
                      If different that the existing revision, will force a new key
                      to be generated. If None, a new key will not be forced.
        opts (Optional[ResourceOptions], optional): _description_.
                      Resource options. If None, a default set of options will
                      be generated. Defaults to None.
    """
    assert isinstance(name, str)
    opts = ResourceOptions.merge(opts, ResourceOptions(additional_secret_outputs=['key', 'plaintext']))
    input_key_b64 = Output.all(key).apply(
        lambda args: None if args[0] is None else b64encode(args[0]).decode('utf-8')
      )

    super().__init__(
        EncryptedStringProvider(),
        name,
        # NOTE: Pulumi doesn't populate output properties unless they are also inputs.
        #       It does not matter what their input values are..
        dict(
            name=name,
            input_key_b64=input_key_b64,
            plaintext=plaintext,
            key_b64=None,
            ciphertext=None,
            input_key_revision=key_revision,
            key_revision=None
          ),
        opts=opts
      )
