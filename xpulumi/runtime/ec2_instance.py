#!/usr/bin/env python3

from base64 import b64encode
from copy import deepcopy
from typing import Optional, List, Union, Set, Tuple, cast

import subprocess
import os
import json
import ipaddress
import yaml
import io
from io import BytesIO

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
  Input,
)

from pulumi_aws import (
  ec2,
  route53,
  acm,
  cognito,
  ebs,
  ecs,
  ecr,
  elasticloadbalancingv2 as elbv2,
  iam,
  cloudwatch,
  rds,
  kms,
  secretsmanager,
  AwaitableGetAmiResult,
)

from xpulumi.internal_types import Jsonable, JsonableDict

from .util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
  get_ami_arch_from_instance_type,
  future_func,
  yamlify_promise,
)

from .stack_outputs import SyncStackOutputs
from .common import (
    aws_default_region,
    get_aws_region_data,
    pconfig,
    default_tags,
    get_availability_zones,
    long_stack,
    aws_provider,
    aws_resource_options,
    aws_invoke_options,
    with_default_tags,
    long_xstack,
  )
from .. import XPulumiError
from .vpc import VpcEnv
from .security_group import FrontEndSecurityGroup
from .ec2_keypair import Ec2KeyPair
from .dns import DnsZone
from .user_data import (
    UserData,
    render_user_data_base64,
    UserDataConvertible,
    UserDataPart,
    CloudInitPartConvertible,
    MimeHeadersConvertible,
  )
from .ebs_volume import EbsVolume

@future_func
def get_ami_name_filter(ami_arch: str, ami_distro: str, ami_os_version: str) -> str:
  return f"ubuntu/images/hvm-ssd/ubuntu-{ami_distro}-{ami_os_version}-{ami_arch}-server-*"

class Ec2Volume:
  r"""
  Metadata about a single mounted data volume on an EC2 instance.

  Keeps track of the EBS volume, as well as which device name it is
  associated with on the EC2 instance.

  For non-HVM/non-nitro instances, the chosen device name, e.g., '/dev/sdf'
  will be the same device name seen inside the instance.

  For nitro-based instances (most HVM instances, and the default), the
  device names seen inside the instance will be different, and will
  be potentially reordered, as:
    $ lsblk -o +serial
    NAME        MAJ:MIN RM  SIZE RO TYPE MOUNTPOINT                  SERIAL
    nvme0n1     259:0    0   40G  0 disk                             vol0c14668ff49981879
    └─nvme0n1p1 259:1    0   40G  0 part /
    nvme1n1     259:2    0   40G  0 disk                             vol019ea9659aaae62e2

  In this case nvme0n1 is the standard boot drive, forked from the instance AMI, with
  its partition nvme0n1p1 becoming the root of the filesystem. nvme1n1 is the additional
  data volume which was specified as '/dev/sdf' at instance launch time. The serial
  number for attached EBS volumes is set to the EBS volume id.

  In nitro instances, one way to reliably determine which internal device corresponds
  to which attached EBS volume is to extract the serial number from the local device
  and correlate it with the attached volume.

  Another way is to use the nvme tool (requires root):

    $ sudo apt-get install -y nvme-cli
    $ sudo nvme id-ctrl -v /dev/nvme1n1 | grep 0000: | cut -c 56-71 | sed 's/\.//g'
    /dev/sdf

  Another way is to install ebsnvme-id from https://github.com/amazonlinux/amazon-ec2-utils
  (also requires sudo). It is a simple python app, no dependencies, 7056 bytes long:

    sudo ./ebsnvme-id -b /dev/nvme1n1
    /dev/sdf

  Another way is to refer to the device with the symlink:

    /dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol019ea9659aaae62e2

  substituting the correct EBS volume ID as needed

  See https://docs.aws.amazon.com/efs/latest/ug/installing-amazon-efs-utils.html#installing-other-distro



  """

  vol: EbsVolume
  unit_name: str
  """The device name visible to AWS EBS API, without the '/dev/' prefix; e.g., 'sdf', 'sdg', ..."""
  attachment: Optional[ec2.VolumeAttachment] = None


  _volume_id: Optional[Output[str]] = None
  _shortened_volume_id: Optional[Output[str]] = None
  _internal_device_name: Optional[Output[str]] = None


  def __init__(self, vol: EbsVolume, unit_name: str):
    self.vol = vol
    self.unit_name = unit_name

  def commit(self):
    self.vol.commit()

  def get_volume_id(self) -> Output[str]:
    if self._volume_id is None:
      self.commit()
      self._volume_id = self.vol.ebs_volume.id
      assert not self._volume_id is None
    return self._volume_id

  def get_shortened_volume_id(self) -> Output[str]:
    if self._shortened_volume_id is None:
      vol_id = self.get_volume_id()
      self._shortened_volume_id = Output.all(
          cast(Output, vol_id)
        ).apply(
            lambda args: cast(str, args[0]).replace('vol-', '')
          )
    return self._shortened_volume_id

  def get_internal_device_name(self) -> Output[str]:
    if self._internal_device_name is None:
      s_vol_id = self.get_shortened_volume_id()
      self._internal_device_name = Output.all(
          cast(Output, s_vol_id)
        ).apply(
            lambda args: f"/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol{args[0]}"
          )
    return self._internal_device_name

  @property
  def external_dev_name(self) -> str:
    return f'/dev/{self.unit_name}'

def sync_gen_wait_for_volumes_script(
      internal_device_names: List[str],
      max_wait_seconds: int = 120,
      redirect_output_filename: Optional[str] = "/var/log/vol-wait.log"
    ) -> str:
  script = '''#!/usr/bin/env python3
import os,time'''
  if not redirect_output_filename is None:
    script += """
os.close(1)
os.dup2(os.open('""" + redirect_output_filename + """',flags=os.O_WRONLY|os.O_CREAT|os.O_TRUNC,mode=0o644), 1)
os.close(2)
os.dup2(1,2)"""

  if len(internal_device_names) == 0:
    script += '''
print("No vols to wait for. All vols up.")'''
  else:
    quoted_dev_names = '"' + '","'.join(internal_device_names) + '"'
    script += '''
dl=[''' + quoted_dev_names + ''']
nt=0
print(f"Waiting for vols {dl}")
pdl=[]
while len(dl) > 0:
 if nt>0:'''
    if max_wait_seconds > 0:
      script += '''
  if nt>''' + str(max_wait_seconds) + ''':
   raise RuntimeError(f"Timeout waiting for {dl}")'''
    script += '''
  if dl != pdl:
    print(f"{dl} nonexistent; sleeping")
    pdl=dl
  time.sleep(1)
 nt+=1
 for d in dl[:]:
  if os.path.exists(d):
   dl.remove(d)
print("All vols up")
'''
  script += '\n'
  return script

def gen_wait_for_volumes_script(
      internal_device_names: Union[List[str], List[Input[str]]],
      max_wait_seconds: int = 120,
      redirect_output_filename: Optional[str] = "/var/log/vol-wait.log"
    ) -> Output[str]:
  """Given a list of device names (e.g., "/dev/sda1"), generates a short python script that will
     wait until all of the devices exist before exiting. This is useful to delay cloud-init
     processing until required EBS volumes have been attached to a newly started EC2 instance.

  Args:
      internal_device_names (Input[List[str]]): Future list of linus device names to wait for
      max_wait_seconds (int):
                The maximum number of seconds to wait before giving up and failing.
                If 0, will wait forever.  Default 120.
      redirect_output_filename:
                The filename to write logging output to. If None, will write to stdout/stderr.
                Default is "/var/log/vol-wait.log".

  Returns:
      Output[str]: A future python script with "#!/usr/bin/env python3" header that will wait for the devices.
                   NOTE: Does *not* include a "#boothook" cloud-init header.
  """
  result: Output[str] = Output.all(
      max_wait_seconds, redirect_output_filename, *internal_device_names
    ).apply(
        lambda args: sync_gen_wait_for_volumes_script(args[2:], max_wait_seconds=args[0], redirect_output_filename=args[1])
      )
  return result

class Ec2Instance:
  # define a policy that allows EC2 to assume our roles for the purposes of creating EC2 instances
  DEFAULT_ASSUME_ROLE_POLICY_OBJ: JsonableDict = {
      "Version": "2012-10-17",
      "Statement": [
          {
              "Action": "sts:AssumeRole",
              "Principal": {
                "Service": "ec2.amazonaws.com",
              },
              "Effect": "Allow",
              "Sid": "",
            },
        ],
    }
  DEFAULT_ROLE_POLICY_OBJ: JsonableDict = {
      "Version": "2012-10-17",
      "Statement": [
          # Nondestructive EC2 queries
          {
              "Action": ["ec2:Describe*"],
              "Effect": "Allow",
              "Resource": "*",
            },
          # Read-only access to ECR, to fetch docker images
          {
              "Action": [
                  "ecr:GetAuthorizationToken",
                  "ecr:DescribeRepositories",
                  "ecr:BatchGetImage",
                  "ecr:BatchCheckLayerAvailability",
                  "ecr:GetDownloadUrlForLayer",
                ],
              "Effect": "Allow",
              "Resource": "*",
            },
        ],
    }
  DEFAULT_INSTANCE_TYPE = "t3.medium"
  DEFAULT_SYS_VOLUME_SIZE_GB = 40
  DEFAULT_DATA_VOLUME_SIZE_GB = 40
  AMI_OWNER_CANONICAL: str = "099720109477"  # The publisher of Ubunti AMI's
  DEFAULT_AMI_DISTRO = "focal"
  DEFAULT_AMI_OS_VERSION = "20.04"

  resource_prefix: str = ''
  assume_role_policy_obj: Optional[JsonableDict] = None
  role: iam.Role
  instance_profile: iam.InstanceProfile
  instance_type: str
  sys_volume_size_gb: int
  #data_volume_size_gb: int
  keypair: Ec2KeyPair
  instance_dependencies: List[Output]
  cloudwatch_agent_attached_policy: iam.RolePolicyAttachment
  ssm_attached_policy: iam.RolePolicyAttachment
  role_policy_obj: Input[Optional[JsonableDict]] = None
  role_policy: iam.Policy
  attached_policy: iam.RolePolicyAttachment
  _ami_arch: Optional[str]=None
  ami_owner: str
  ami_distro: str
  ami_os_version: str
  ami_name_filter: Output[str]
  ami: AwaitableGetAmiResult
  eip: Optional[ec2.Eip] = None
  use_elastic_ip: bool
  parent_dns_zone: Optional[DnsZone] = None
  dns_names: List[str]
  register_dns: Optional[bool] = None
  primary_dns_name: Optional[str] = None
  description: str
  instance_name: str
  open_ports: Optional[List[Union[int, JsonableDict]]] = None
  dns_records: List[route53.Record]
  vpc: VpcEnv
  az: str
  subnet_id: Input[str]
  sg: FrontEndSecurityGroup
  #ebs_data_volume: Optional[ebs.Volume] = None
  data_volumes: List[Ec2Volume]
  ec2_instance: ec2.Instance
  eip_association: Optional[ec2.EipAssociation] = None
  # data_volume_attachment: Optional[ec2.VolumeAttachment] = None
  user_data: UserData
  wait_for_volumes_at_boot: bool = True
  _wait_for_volumes_added: bool = False
  _volumes_committed: bool = False
  _committed: bool = False

  def __init__(
        self,
        vpc: VpcEnv,
        resource_prefix: Optional[str] = None,
        use_config: bool = True,
        cfg_prefix: Optional[str]=None,
        assume_role_policy_obj: Optional[JsonableDict]=None,
        description: Optional[str]=None,
        instance_type: Optional[str]=None,
        sys_volume_size_gb: Optional[int]=None,
        #data_volume_size_gb: Optional[int]=None,
        data_volumes: Optional[List[Union[int, EbsVolume]]]=None,
        public_key: Optional[str]=None,
        public_key_file: Optional[str]=None,
        role_policy_obj: Input[Optional[JsonableDict]]=None,
        ami_os_version: Optional[str] = None,
        use_elastic_ip: Optional[bool] = None,
        parent_dns_zone: Optional[Union[str, DnsZone]] = None,
        dns_names: Optional[Union[str, List[str]]] = None,
        dns_subnames: Optional[Union[str, List[str]]] = None,
        register_dns: Optional[bool] = None,
        instance_name: Optional[str] = None,
        open_ports: Optional[List[Union[int, JsonableDict]]]=None,
        user_data: UserDataConvertible = None,
        wait_for_volumes_at_boot: bool = True,
        az: Optional[str] = None,
        commit: bool=True,
      ):
    self.data_volumes = []
    if resource_prefix is None:
      resource_prefix = ''
    self.resource_prefix = resource_prefix

    self.vpc = vpc

    ami_distro = self.DEFAULT_AMI_DISTRO
    ami_owner = self.AMI_OWNER_CANONICAL

    self.instance_dependencies = []

    if use_config:
      if instance_type is None:
        instance_type = pconfig.get(f'{cfg_prefix}ec2_instance_type')
      if sys_volume_size_gb is None:
        sys_volume_size_gb = pconfig.get_int(f'{cfg_prefix}ec2_sys_volume_size')
      if data_volumes is None:
        data_volumes_data = cast(Optional[Union[str, List[int]]], pconfig.get(f'{cfg_prefix}ec2_data_volume_sizes'))
        if not data_volumes_data is None:
          if isinstance(data_volumes_data, str):
            data_volumes_data = [ int(data_volumes_data) ]
        data_volumes = cast(Optional[List[Union[int, EbsVolume]]], data_volumes_data)
      if ami_os_version is None:
        ami_os_version = pconfig.get(f'{cfg_prefix}ec2_ami_os_version')
      if use_elastic_ip is None:
        use_elastic_ip = pconfig.get_bool(f'{cfg_prefix}ec2_use_elastic_ip')
      if parent_dns_zone is None:
        parent_dns_zone = pconfig.get(f'{cfg_prefix}ec2_parent_dns_zone')
      if dns_names is None and dns_subnames is None:
        dns_names = pconfig.get(f'{cfg_prefix}ec2_dns_names')
        dns_subnames = pconfig.get(f'{cfg_prefix}ec2_dns_subnames')
      if register_dns is None:
        register_dns = pconfig.get_bool(f'{cfg_prefix}ec2_register_dns')
      if instance_name is None:
        instance_name = pconfig.get(f'{cfg_prefix}ec2_instance_name')
      if open_ports is None:
        op_data = cast(Jsonable, pconfig.get(f'{cfg_prefix}ec2_open_ports'))
        if isinstance(op_data, str):
          op_data = cast(Jsonable, json.loads(op_data))
        assert op_data is None or isinstance(op_data, list)
        open_ports = cast(Optional[List[Union[int, JsonableDict]]], op_data)

      if az is None:
        az = pconfig.get(f'{cfg_prefix}ec2_instance_az')
      if user_data is None:
        user_data = pconfig.get(f'{cfg_prefix}ec2_user_data')

    if az is None:
      az = vpc.get_default_az()
    subnet_id = vpc.subnet_ids[vpc.get_index_of_az(az)]

    if instance_type is None:
      instance_type = self.DEFAULT_INSTANCE_TYPE

    if sys_volume_size_gb is None:
      sys_volume_size_gb = self.DEFAULT_SYS_VOLUME_SIZE_GB

    if ami_os_version is None:
      ami_os_version = self.DEFAULT_AMI_OS_VERSION

    if use_elastic_ip is None:
      use_elastic_ip = True

    if isinstance(parent_dns_zone, str):
      parent_dns_zone = DnsZone(parent_dns_zone, resource_prefix=f'{resource_prefix}parent-', create=False)

    if register_dns is None:
      register_dns = not parent_dns_zone is None

    if register_dns and not use_elastic_ip:
      raise XPulumiError("use_elastic_ip is required if register_dns is specified")

    if dns_names is None:
      dns_names = []
    elif not isinstance(dns_names, list):
      dns_names = [ dns_names ]
    if dns_subnames is None:
      dns_subnames = []
    elif not isinstance(dns_subnames, list):
      dns_subnames = [ dns_subnames ]

    all_dns_names: Set[str] = set()
    ordered_dns_names: List[str] = []
    for fq_name in dns_names:
      if not fq_name in all_dns_names:
        all_dns_names.add(fq_name)
        ordered_dns_names.append(fq_name)
    if len(dns_subnames) > 0:
      if parent_dns_zone is None:
        raise XPulumiError("parent_dns_zone must be provided if dns_subnames are provided")
      for sn in dns_subnames:
        if sn in [ '', '.' ]:
          fq_name = parent_dns_zone.zone_name
        else:
          fq_name = sn + '.' + parent_dns_zone.zone_name
        if not fq_name in all_dns_names:
          all_dns_names.add(fq_name)
          ordered_dns_names.append(fq_name)

    primary_dns_name: Optional[str] = None
    if not parent_dns_zone is None:
      for dn in ordered_dns_names:
        if dn == parent_dns_zone.zone_name:
          primary_dns_name = dn
        elif not dn.endswith('.' + parent_dns_zone.zone_name):
          raise XPulumiError(f"Requested DNS name {dn} is not a child of parent zone {parent_dns_zone.zone_name}")
    if primary_dns_name is None and len(ordered_dns_names) > 0:
      primary_dns_name = ordered_dns_names[0]

    if len(ordered_dns_names) > 0:
      assert not primary_dns_name is None
      # put the primary name first
      ordered_dns_names.remove(primary_dns_name)
      ordered_dns_names = [ primary_dns_name ] + ordered_dns_names

    if description is None:
      description = f"EC2 instance ({resource_prefix}) in Pulumi stack {long_stack}"

    if instance_name is None:
      instance_name = primary_dns_name
      if instance_name is None:
        instance_name = f"{resource_prefix}ec2-instance"

    self.instance_type = instance_type
    self.sys_volume_size_gb = sys_volume_size_gb
    self.ami_os_version = ami_os_version
    self.ami_owner = ami_owner
    self.ami_distro = ami_distro
    self.use_elastic_ip = use_elastic_ip
    self.dns_names = ordered_dns_names
    self.primary_dns_name = primary_dns_name
    self.register_dns = register_dns
    self.description = description
    self.instance_name = instance_name
    self.open_ports = open_ports
    self.parent_dns_zone = parent_dns_zone
    self.user_data = UserData(user_data)
    self.wait_for_volumes_at_boot = wait_for_volumes_at_boot
    self.az = az
    self.subnet_id = subnet_id


    if not data_volumes is None:
      for dv in data_volumes:
        if isinstance(dv, int):
          self.add_data_volume(volume_size_gb=dv)
        else:
          self.add_data_volume(vol=dv)

    # define an assume role policy that allows EC2 to assume a role for our instance
    if assume_role_policy_obj is None:
      assume_role_policy_obj = self.DEFAULT_ASSUME_ROLE_POLICY_OBJ
    assume_role_policy_obj = deepcopy(assume_role_policy_obj)
    self.assume_role_policy_obj = assume_role_policy_obj

    # define a role policy that allows our instance to access needed AWS resources
    if role_policy_obj is None:
      role_policy_obj = self.DEFAULT_ROLE_POLICY_OBJ
    self.role_policy_obj = role_policy_obj

    self.keypair = Ec2KeyPair(
      resource_prefix=resource_prefix,
      use_config=use_config,
      cfg_prefix=cfg_prefix,
      public_key=public_key,
      public_key_file=public_key_file,
      commit=False
    )

    # ---- start creating resources
    if commit:
      self.commit()

  @property
  def ami_arch(self) -> str:
    if self._ami_arch is None:
      self._ami_arch = cast(str, get_ami_arch_from_instance_type(self.instance_type))
      assert isinstance(self._ami_arch, str)
    return self._ami_arch

  def add_user_data(
        self,
        content: Union[UserDataPart, Input[CloudInitPartConvertible]],
        mime_type: Input[Optional[str]]=None,
        headers: Input[MimeHeadersConvertible]=None,
        priority: int=500,
      ) -> None:
    self.user_data.add(
        content,
        mime_type=mime_type,
        headers=headers,
        priority=priority,
      )

  def add_wait_for_volumes_boothook(
        self,
        max_wait_seconds: int=120,
        redirect_output_filename: Optional[str]="/var/log/vol-wait.log",
        priority: int=0
      ) -> None:
    if not self._wait_for_volumes_added:
      self.commit_volumes()
      data_device_names = [ x.get_internal_device_name() for x in self.data_volumes ]

      if len(data_device_names) > 0:
        # Add a boothook to wait for all data volumes to be attached before
        # proceeding with cloud-init. This is necessary because EC2 in its wisdom
        # does not let you create an EC2 instance without starting it, and
        # there is no way to attach a volume to an EC2 instance until after the
        # instance is created.
        script = gen_wait_for_volumes_script(
            cast(List[Input[str]], data_device_names),
            max_wait_seconds=max_wait_seconds,
            redirect_output_filename=redirect_output_filename
          )
        self.user_data.add_boothook(script, priority=priority)
      self._wait_for_volumes_added = True

  def add_data_volume(
        self,
        vol: Optional[EbsVolume]=None,
        volume_size_gb: Optional[int]=None,
        name: Input[Optional[str]]=None,
        use_config: bool=True,
        cfg_prefix: Optional[str]=None,
        volid: Input[Optional[str]]=None,
      ) -> Ec2Volume:
    resource_prefix = self.resource_prefix
    unit_name = 'sd' + chr(ord('f') + len(self.data_volumes))

    if vol is None:
      vol = EbsVolume(
          f'{resource_prefix}ec2-instance-',
          unit_name=unit_name,
          az=self.az,
          volume_size_gb=volume_size_gb,
          name=name,
          use_config=use_config,
          cfg_prefix=cfg_prefix,
          volid=volid,
          commit=False   # We will commit at self.commit_volumes() time
        )
    assert isinstance(vol, EbsVolume)
    ec2_vol = Ec2Volume(vol, unit_name)
    self.data_volumes.append(ec2_vol)
    return ec2_vol

  def commit_volumes(self):
    if not self._volumes_committed:
      for ec2_vol in self.data_volumes:
        ec2_vol.commit()
      self._volumes_committed = True

  def commit(self):
    if self._committed:
      return

    resource_prefix = self.resource_prefix
    self.role_policy = iam.Policy(
        f"{resource_prefix}ec2-role-policy",
        path="/",
        description=f"Custom role policy for {self.description}",
        policy=jsonify_promise(self.role_policy_obj),
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-role"),
        opts=aws_resource_options,
      )

    # Create an IAM role for our EC2 instance to run in.
    self.role = iam.Role(
        f'{resource_prefix}ec2-instance-role',
        assume_role_policy=json.dumps(self.assume_role_policy_obj, sort_keys=True),
        description=f"Role for {self.description}",
        # force_detach_policies=None,
        max_session_duration=12*TTL_HOUR,
        name=f'{resource_prefix}{long_xstack.replace(":", "-")}-ec2-role',
        # name_prefix=None,
        path=f'/pstack={long_stack}/',
        # permissions_boundary=None,
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-role"),
        opts=aws_resource_options,
      )

    # Attach policy to the EC2 instance role to allow cloudwatch monitoring.
    self.cloudwatch_agent_attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy-cloudwatch-agent',
        role=self.role.name,
        policy_arn="arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy",
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.cloudwatch_agent_attached_policy)

    # Attach policy to the EC2 instance role to allow SSM management.
    self.ssm_attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy-ssm-managed',
        role=self.role.name,
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.ssm_attached_policy)

    # Attach our custom policy to the EC2 instance role.
    self.attached_policy = iam.RolePolicyAttachment(
        f'{resource_prefix}ec2-attached-policy',
        role=self.role.name,
        policy_arn=self.role_policy.arn,
        opts=aws_resource_options,
      )
    self.instance_dependencies.append(self.attached_policy)

    # create an instance profile for our instance that allows it to assume the above role
    self.instance_profile = iam.InstanceProfile(
        f"{resource_prefix}ec2-instance-profile",
        role=self.role.name,
        tags=with_default_tags(Name=f"{resource_prefix}{long_xstack}-ec2-instance"),
        opts=aws_resource_options,
      )

    self.keypair.commit()

    self.ami_name_filter = get_ami_name_filter(self.ami_arch, self.ami_distro, self.ami_os_version)

    # Find the most recent AMI that matches
    self.ami = ec2.get_ami(
        most_recent=True,
        filters=[
            ec2.GetAmiFilterArgs(
                name="name",
                values=[ self.ami_name_filter ],
              ),
            ec2.GetAmiFilterArgs(
                name="virtualization-type",
                values=[ "hvm" ],
              ),
          ],
        owners=[self.ami_owner],
        opts=aws_invoke_options,
      )

    if self.use_elastic_ip:
      # Create an elastic IP address for the instance. This allows the IP address to remain stable even if the instance is
      # shut down and restarted, or even destroyed and recreated. Prevents DNS entries and caches from becoming invalid.
      self.eip = ec2.Eip(
          f'{resource_prefix}ec2-instance-eip',
          vpc=True,
          tags=with_default_tags(Name=self.instance_name),
          opts=aws_resource_options,
        )
      self.instance_dependencies.append(self.eip)

    dns_records: List[route53.Record] = []
    if self.register_dns and len(self.dns_names) > 0:
      cname_target: Optional[str] = None
      for dn in self.dns_names:
        if cname_target is None or dn == self.parent_dns_zone.zone_name:
          dns_record = route53.Record(
              f'{resource_prefix}ec2-instance-dns-record-{dn}',
              # aliases=None,
              # allow_overwrite=None,
              # failover_routing_policies=None,
              # geolocation_routing_policies=None,
              # health_check_id=None,
              # latency_routing_policies=None,
              # multivalue_answer_routing_policy=None,
              name=dn,
              records=[ self.eip.public_ip ],
              # set_identifier=None,
              ttl=TTL_MINUTE * 10,
              type='A',
              # weighted_routing_policies=None,
              zone_id=self.parent_dns_zone.zone_id,
              opts=aws_resource_options,
            )
          if cname_target is None:
            cname_target = dn
        else:
          dns_record = route53.Record(
              f'{resource_prefix}ec2-instance-dns-record-{dn}',
              # opts=None,
              # aliases=None,
              # allow_overwrite=None,
              # failover_routing_policies=None,
              # geolocation_routing_policies=None,
              # health_check_id=None,
              # latency_routing_policies=None,
              # multivalue_answer_routing_policy=None,
              name=dn,
              records=[ cname_target ],
              # set_identifier=None,
              ttl=TTL_MINUTE * 10,
              type='CNAME',
              # weighted_routing_policies=None,
              zone_id=self.parent_dns_zone.zone_id,
              opts=aws_resource_options,
            )
        dns_records.append(dns_record)
        self.instance_dependencies.append(dns_record)
    self.dns_records = dns_records

    # create a security group for the new instance
    self.sg = FrontEndSecurityGroup(
      vpc = self.vpc,
      open_ports=self.open_ports,
      resource_prefix=resource_prefix,
    )

    self.commit_volumes()
    if self.wait_for_volumes_at_boot:
      self.add_wait_for_volumes_boothook()

    rendered_user_data = render_user_data_base64(self.user_data, debug_log=True)

    # Create an EC2 instance
    self.ec2_instance = ec2.Instance(
        f'{resource_prefix}ec2-instance',
        ami=self.ami.id,
        instance_type=self.instance_type,
        iam_instance_profile=self.instance_profile.name,
        key_name=self.keypair.keypair.key_name,
        associate_public_ip_address=self.eip is None,   # deferred until EIP is assigned. Sadly no way to do this atomically
        subnet_id=self.subnet_id,
        vpc_security_group_ids=[ self.sg.sg.id ],
        root_block_device=dict(volume_size=self.sys_volume_size_gb),
        user_data_base64=rendered_user_data,
        tags=with_default_tags(Name=self.instance_name),
        volume_tags=with_default_tags(Name=f"{self.instance_name}-sys"),
        opts=ResourceOptions(provider=aws_provider, depends_on=self.instance_dependencies, delete_before_replace=True)
      )

    # associate the EIP with the instance
    if not self.eip is None:
      self.eip_association = ec2.EipAssociation(
          f"{resource_prefix}ec2-eip-assoc",
          instance_id=self.ec2_instance.id,
          allocation_id=self.eip.id,
          opts=aws_resource_options,
        )

    for ec2_vol in self.data_volumes:
      # attach the ebs data volume volume to the instance. Unfortunately no way to do this at launch time
      ec2_vol.attachment = ec2.VolumeAttachment(
          f"{resource_prefix}ec2-volume-attachment-{ec2_vol.unit_name}",
          device_name=f"/dev/{ec2_vol.unit_name}",
          volume_id = ec2_vol.get_volume_id(),
          instance_id = self.ec2_instance.id,
          stop_instance_before_detaching=True,
          opts=ResourceOptions(provider=aws_provider, delete_before_replace=True)
        )
    self._committed = True

  def stack_export(self, export_prefix: Optional[str]=None) -> None:
    if export_prefix is None:
      export_prefix = ''

    # pulumi.export(f'{export_prefix}ec2_instance_id', self.ec2_instance.id)
    pulumi.export(f'{export_prefix}dns_names', self.dns_names)
    if not self.eip is None:
      pulumi.export(f'{export_prefix}public_ip', self.eip.public_ip)
    pulumi.export(f'{export_prefix}ec2_instance_id', self.ec2_instance.id)
