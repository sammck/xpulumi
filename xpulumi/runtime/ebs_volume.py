from typing import Optional

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
  Input,
)

from pulumi_aws import (
  ebs,
)

from .util import az_to_region

from .common import (
    pconfig,
    config_property_info,
    default_tags,
    get_availability_zones,
    long_stack,
    aws_default_region,
    get_aws_provider,
    get_aws_resource_options,
    get_aws_invoke_options,
    with_default_tags,
    long_xstack,
  )
from .. import XPulumiError

class EbsVolume:
  DEFAULT_VOLUME_SIZE_GB = 40

  resource_prefix: str = ''
  unit_name: Optional[str] = None
  region: Optional[str] = None
  az: Input[Optional[str]] = None
  volume_size_gb: int = DEFAULT_VOLUME_SIZE_GB
  name: Input[Optional[str]] = None
  volid: Input[Optional[str]] = None
  _ebs_volume: Optional[ebs.Volume] = None
  _committed: bool = False

  @property
  def unit_postfix(self) -> str:
    return '' if self.unit_name is None else f"-{self.unit_name}"

  @property
  def ebs_volume(self) -> ebs.Volume:
    if self._ebs_volume is None:
      raise XPulumiError("EbsVOlume not yet committed")
    return self._ebs_volume

  def __init__(
        self,
        resource_prefix: Optional[str] = None,
        unit_name: Optional[str] = None,
        az: Input[Optional[str]]=None,
        region: Optional[str]=None,
        volume_size_gb: Optional[int]=None,
        name: Input[Optional[str]]=None,
        use_config: bool = True,
        cfg_prefix: Optional[str]=None,
        volid: Input[Optional[str]]=None,
        commit: bool=True,
      ):
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix
    self.unit_name = unit_name
    self.name = name
    if region is None:
      if isinstance(az, str):
        region = az_to_region(az)
      else:
        region = aws_default_region
    self.region = region

    if use_config:
      if volume_size_gb is None:
        volume_size_gb = pconfig.get_int(
            f'{cfg_prefix}ebs_volume_size{self.unit_postfix}',
            config_property_info(description=f"EBS volume size in gigabytes, default={self.DEFAULT_VOLUME_SIZE_GB}"),
          )
      if az is None:
        az = pconfig.get(
            f'{cfg_prefix}ebs_volume_az{self.unit_postfix}',
            config_property_info(description="The AZ to put the EBS volume in, default=let AWS pick"),
          )
      if volid is None:
        volid = pconfig.get(
            f'{cfg_prefix}ebs_volume_id{self.unit_postfix}',
            config_property_info(description="The EBS volume id of the EBS volume, to import a volume rather than create one, default=create a new volume"),
          )

    if volume_size_gb is None:
      volume_size_gb = self.DEFAULT_VOLUME_SIZE_GB

    self.az = az
    self.volume_size_gb = volume_size_gb
    self.volid = volid

    if commit:
      self.commit()

  def commit(self):
    if self._committed:
      return

    resource_prefix = self.resource_prefix

    if self.volid is None:
      if self.az is None:
        raise XPulumiError("An availability zone must be specified for an EBS volume")

      if self.volume_size_gb is None:
        self.volume_size_gb = self.DEFAULT_VOLUME_SIZE_GB

      if self.name is None:
        self.name = f'{resource_prefix}ebs-volume{self.unit_postfix}'

      self._ebs_volume = ebs.Volume(
          f'{resource_prefix}ebs-volume{self.unit_postfix}',
          availability_zone=self.az,
          size=self.volume_size_gb,
          tags=with_default_tags(Name=self.name),
          opts=get_aws_resource_options(self.region),
        )
      self.volid = self.ebs_volume.id
    else:
      self._ebs_volume = ebs.Volume.get(
          f'{resource_prefix}ebs-volume{self.unit_postfix}',
          id = self.volid,
          opts=get_aws_resource_options(self.region),
        )
    self._committed = True

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    pulumi.export(f'{export_prefix}ebs_volume{self.unit_postfix}', self.ebs_volume.id)
