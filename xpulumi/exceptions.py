#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Exceptions defined by this package"""

from typing import Optional

from .internal_types import JsonableDict

class XPulumiError(Exception):
  """Base class for all error exceptions defined by this package."""
  #pass

class PulumiApiError(XPulumiError):
  _url: str
  _data: JsonableDict

  def __init__(self, url: str, data: JsonableDict):
    message = data.get('message', "The Pulumi API request failed")
    super().__init__(message)
    self._data = data

  @property
  def status_code(self) -> int:
    result = self._data.get('code', 0)
    assert isinstance(result, (int, str))
    return int(result)

  @property
  def url(self) -> str:
    return self._url
