# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""
Abtract context for working with Pulumi.

Allows the application to provide certain requirements such as passphrases, defaults, etc.
on demand.

"""

from typing import Optional, cast, Dict, Tuple
from .internal_types import Jsonable, JsonableDict, JsonableList

import os
from abc import ABC, abstractmethod
from pulumi import automation as pauto
from urllib.parse import urlparse, ParseResult, urlunparse, unquote as url_unquote
from copy import deepcopy
import boto3.session
from boto3.session import Session as BotoAwsSession
#from botocore.session import Session as BotocoreSession
import tempfile
import json
import requests
import zlib
import base64

from project_init_tools.util import file_url_to_pathname, pathname_to_file_url
from .exceptions import XPulumiError, PulumiApiError
from .context import XPulumiContext
from .constants import PULUMI_STANDARD_BACKEND

class PulumiApiClient:
  _api_url: str
  _api_url_parts: ParseResult
  _requests_session: requests.Session
  _access_token: Optional[str] = None
  _username: Optional[str] = None
  _user_agent: str

  def __init__(self, api_url: str, access_token: Optional[str]=None, username: Optional[str]=None):
    api_url_parts = urlparse(api_url)
    if api_url_parts.scheme not in ('https', 'http'):
      raise XPulumiError(f"Unsupported scheme in Pulumi API URL {api_url}")
    api_path = api_url_parts.path
    while api_path.endswith('/'):
      api_path = api_path[:-1]
    api_url_parts = api_url_parts._replace(path=api_path)
    self._api_url = urlunparse(api_url_parts)
    self._api_url_parts = api_url_parts
    self._access_token = access_token
    self._username = username
    self._requests_session = requests.Session()
    self._user_agent = "pulumi-cli/1 (v3.25.1; linux)"   # just to keep API stable hopefully

  @property
  def api_url(self) -> str:
    return self._api_url

  @property
  def scheme(self) -> str:
    return self._api_url_parts.scheme

  @property
  def access_token(self) -> Optional[str]:
    return self._access_token

  def raw_api_request(
        self,
        method: str,
        api_path: str,
        req_params: Optional[Dict[str, str]]=None,
        req_data: Optional[bytes]=None,
        gzip_req_data: bool=False,
      ) -> Tuple[str, requests.Response]:
    while api_path.startswith('/'):
      api_path = api_path[1:]
    req_url = self.api_url + '/' + api_path
    headers = {
        "Accept": "application/vnd.pulumi+8",
        "Content-Type": "application/json",
        "Authorization": f"token {self.access_token}",
        "User-Agent": self._user_agent,
      }
    if gzip_req_data and not req_data is None and len(req_data) > 0:
      req_data = zlib.compress(req_data)
      headers['Content-Encoding'] = 'gzip'
    resp = self._requests_session.request(method, req_url, params=req_params, data=req_data, headers=headers)
    # NOTE: A warning message may be returned in header X-Pulumi-Warning, and we could log that
    return req_url, resp

  def api_request(
        self,
        method: str,
        api_path: str,
        req_params: Optional[Dict[str, str]]=None,
        req_data: Jsonable=None,
        gzip_req_data: bool=False,
      ) -> JsonableDict:
    bin_req_data = None if req_data is None else json.dumps(req_data).encode('utf-8')
    req_url, resp = self.raw_api_request(method, api_path, req_params=req_params, req_data=bin_req_data, gzip_req_data=gzip_req_data)
    bin_resp_data = resp.content
    resp_data: Jsonable = None
    if len(bin_resp_data) > 0:
      try:
        resp_data = resp.json()
        if not isinstance(resp_data, dict):
          resp_data = dict(json_content=resp_data)
      except Exception:
        resp_data = dict(code=resp.status_code, message=bin_resp_data.decode('utf-8'))
    else:
      resp_data = {}
    if (resp.status_code < 200 or resp.status_code >= 300):
      if not 'code' in resp_data:
        resp_data['code'] = resp.status_code
      if not 'message' in resp_data:
        resp_data['message'] = f"Unexpected HTTP status code {resp.status_code} from {req_url}"
    if resp.status_code >= 400 and resp.status_code < 600:
      if resp.status_code == 401 and self.access_token is None or self.access_token == "":
        raise XPulumiError(f"Pulumi API requires an access token: {req_url}")
    if resp.status_code < 200 or resp.status_code >= 300:
      raise PulumiApiError(req_url, resp_data)
    return resp_data

  def api_get(
        self,
        api_path: str,
        req_params: Optional[Dict[str, str]]=None,
      ) -> Jsonable:
    return self.api_request("get", api_path, req_params=req_params)

  def api_head(
        self,
        api_path: str,
        req_params: Optional[Dict[str, str]]=None,
      ) -> Jsonable:
    return self.api_request("head", api_path, req_params=req_params)

  def api_post(
        self,
        api_path: str,
        req_params: Optional[Dict[str, str]]=None,
        req_data: Jsonable=None,
        gzip_req_data: bool=False,
      ) -> Jsonable:
    return self.api_request("post", api_path, req_params=req_params, req_data=req_data, gzip_req_data=gzip_req_data)

  def get_user_info(self) -> JsonableDict:
    resp = self.api_get("api/user")
    assert isinstance(resp, dict)
    return resp

  def require_username(self) -> str:
    if self._username is None:
      resp = self.get_user_info()
      result = resp.get('githubLogin', None)
      if result is None or not isinstance(result, str) or result == '':
        raise XPulumiError(
            f"Unable to retrieve pulumi account name from {self.api_url}: "
            f"/api/user response has missing or empty githubUser")
      self._username = result
    return self._username

  def get_project_api_path(self, project: str, *args: Optional[str], organization: Optional[str]=None) -> str:
    if organization is None:
      organization = self.require_username()
    result = f"/api/stacks/{organization}/{project}"
    for arg in args:
      if not arg is None:
        result += '/' + arg
    return result

  def get_stack_api_path(self, project: str, stack: str, *args, organization: Optional[str]=None) -> str:
    project_path = self.get_project_api_path(project, organization=organization)
    result = f"{project_path}/{stack}"
    for arg in args:
      if not arg is None:
        result += '/' + arg
    return result

  def list_stacks(self, project: Optional[str]=None) -> JsonableList:
    params = {}
    if not project is None:
      params['project'] = project

    resp = self.api_get("/api/user/stacks", req_params=params)
    assert isinstance(resp, dict)
    result = resp['stacks']
    return result

  def project_exists(self, project: str, organization: Optional[str]=None) -> bool:
    api_path = self.get_project_api_path(project=project, organization=organization)
    try:
      self.api_head(api_path)
    except PulumiApiError as e:
      if e.status_code == 404:
        return False
      raise
    return True

  def encrypt_raw_value(self, project: str, stack: str, data: bytes, organization: Optional[str]=None) -> bytes:
    api_path = self.get_stack_api_path(project, stack, "encrypt", organization=organization)
    req_data = dict(plaintext=base64.b64encode(data).decode('utf-8'))
    resp = self.api_post(api_path, req_data=req_data)
    assert isinstance(resp, dict)
    result = base64.b64decode(resp['ciphertext'])
    return result

  def decrypt_raw_value(self, project: str, stack: str, data: bytes, organization: Optional[str]=None) -> bytes:
    api_path = self.get_stack_api_path(project, stack, "decrypt", organization=organization)
    req_data = dict(ciphertext=base64.b64encode(data).decode('utf-8'))
    resp = self.api_post(api_path, req_data=req_data)
    assert isinstance(resp, dict)
    result = base64.b64decode(resp['plaintext'])
    return result

  def get_stack(self, project: str, stack: str, organization: Optional[str]=None) -> JsonableDict:
    api_path = self.get_stack_api_path(project, stack, organization=organization)
    resp = self.api_get(api_path)
    if not isinstance(resp, dict):
      raise XPulumiError(
          f"Malformed response to stack metadata request for backend={self.api_url}, "
          f"organization={organization}, project={project}, stack={stack}")
    return resp

  def export_stack_deployment(
        self,
        project: str,
        stack: str,
        organization: Optional[str]=None,
        version: Optional[int]=None
      ) -> JsonableDict:
    api_path = self.get_stack_api_path(project, stack, 'export', organization=organization)
    if not version is None:
      api_path += f"/{version}"
    resp = self.api_get(api_path)
    if not isinstance(resp, dict) or not 'deployment' in resp:
      raise XPulumiError(
          f"Malformed response to stack export request for backend={self.api_url}, "
          f"organization={organization}, project={project}, stack={stack}")
    return resp
