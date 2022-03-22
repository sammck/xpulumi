#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""xpulumi configuration"""

from typing import TYPE_CHECKING, Optional
from .internal_types import JsonableDict

if TYPE_CHECKING:
  from base_context import XPulumiContextBase

import os
import yaml
import json
from yaml import load, dump
try:
  from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
  from yaml import Loader, Dumper  #type: ignore[misc]

from .constants import XPULUMI_CONFIG_FILENAME_BASE, XPULUMI_CONFIG_DIRNAME

def locate_xpulumi_config_file(config_path: Optional[str]=None, starting_dir: Optional[str]=None, scan_parent_dirs: bool=True) -> str:
  if starting_dir is None:
    starting_dir = '.'
  starting_dir = os.path.abspath(os.path.expanduser(starting_dir))
  if config_path is None:
    config_path = os.environ.get('XPULUMI_CONFIG', None)
    if config_path == '':
      config_path = None
  if config_path is None:
    config_path = starting_dir
  else:
    config_path = os.path.abspath(os.path.join(starting_dir, os.path.expanduser(config_path)))
  test_path = config_path
  if not os.path.exists(test_path):
      raise FileNotFoundError(f"xpulumi: Config file not found: '{config_path}'")
  if os.path.isdir(test_path):
    tail_1_json = XPULUMI_CONFIG_FILENAME_BASE + '.json'
    tail_2_json = os.path.join(XPULUMI_CONFIG_DIRNAME, tail_1_json)
    tail_1_yaml = XPULUMI_CONFIG_FILENAME_BASE + '.yaml'
    tail_2_yaml = os.path.join(XPULUMI_CONFIG_DIRNAME, tail_1_yaml)
    while True:
      p = os.path.join(test_path, tail_1_json)
      if os.path.isfile(p):
        result = p
        break
      p = os.path.join(test_path, tail_1_yaml)
      if os.path.isfile(p):
        result = p
        break
      p = os.path.join(test_path, tail_2_json)
      if os.path.isfile(p):
        result = p
        break
      p = os.path.join(test_path, tail_2_yaml)
      if os.path.isfile(p):
        result = p
        break
      old_dir = test_path
      test_path = os.path.dirname(test_path)
      if not scan_parent_dirs or old_dir == test_path:
        if scan_parent_dirs:
          raise FileNotFoundError(f"xpulumi: Config file not found in dir or parent dirs: '{config_path}'")
        else:
          raise FileNotFoundError(f"xpulumi: Config file not found in dir: '{config_path}'")
  elif os.path.isfile(test_path):
    result = test_path
  else:
    raise FileNotFoundError(f"xpulumi: Config file path not directory or file: '{config_path}'")
  return result

class XPulumiConfig:
  _config_file: str
  _config_data: JsonableDict
  _xpulumi_dir: str
  _project_root_dir: str
  _pulumi_home: str

  def __init__(self, config_path: Optional[str]=None, starting_dir: Optional[str]=None, scan_parent_dirs: bool=True):
    self._config_file = locate_xpulumi_config_file(config_path=config_path, starting_dir=starting_dir, scan_parent_dirs=scan_parent_dirs)
    with open(self._config_file) as f:
      config_text = f.read()
    if self._config_file.endswith('.yaml'):
      self._config_data = yaml.load(config_text, Loader=Loader)
    else:
      self._config_data = json.loads(config_text)
    xpulumi_dir = self._config_data.get('xpulumi_dir', '.')
    assert isinstance(xpulumi_dir, str)
    xpulumi_dir = os.path.abspath(os.path.join(os.path.dirname(self._config_file), os.path.expanduser(xpulumi_dir)))
    self._xpulumi_dir = xpulumi_dir
    project_root_dir = self._config_data.get('project_root_dir', '..')
    assert isinstance(project_root_dir, str)
    project_root_dir = os.path.abspath(os.path.join(xpulumi_dir, os.path.expanduser(project_root_dir)))
    self._project_root_dir = project_root_dir
    self._pulumi_home = os.path.join(xpulumi_dir, '.pulumi')

  @property
  def config_file(self) -> str:
    return self._config_file
    
  @property
  def config_data(self) -> JsonableDict:
    return self._config_data
  
  @property
  def xpulumi_dir(self) -> str:
    return self._xpulumi_dir

  @property
  def project_root_dir(self) -> str:
    return self._project_root_dir

  @property
  def pulumi_home(self) -> str:
    return self._pulumi_home

  def create_context(self, cwd: Optional[str]=None) -> 'XPulumiContextBase':
    from .base_context import XPulumiContextBase
    ctx = XPulumiContextBase(self, cwd=cwd)
    return ctx
