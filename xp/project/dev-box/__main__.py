
import json
import shlex
import os

import pulumi
from pulumi import Output, Input
from xpulumi.exceptions import XPulumiError
from xpulumi.internal_types import Jsonable, JsonableDict
from xpulumi.runtime import (
    VpcEnv,
    DnsZone,
    Ec2Instance,
    require_stack_output,
    CloudWatch,
    enable_debugging,
    aws_account_id,
    pulumi_project_name,
    stack_name,
    pconfig,
    default_val,
    HashedPassword,
    jsonify_promise,
    long_stack,
  )

# If environment variable XPULUMI_DEBUGGER is defined, this
# will cause the program to stall waiting for vscode to
# connect to port 5678 for debugging. Useful if Pulumi logging isn't
# cutting it.
enable_debugging()

# The name of the user account to create in the EC2 instance. By default
# we will use our own username (this is risky if multiple people manage
# this stack, since they will cause the EC2 instance to be recreated). If
# the username is changed, the old home directory will remain, since the /home
# is mounted on a reusable EBS volume, but the new account will have the
# same UID/GID as the old account and will have access to both home directories.
ec2_instance_username: str = default_val(pconfig.get("ec2_instance_username"), os.getlogin())
pulumi.export("ec2_username", ec2_instance_username)

# The sudo password for our EC2 user. This must be set as a secret config value on this stack with
#       pulumi -s dev config set --secret ec2_user_password <password>
# NOTE: A strong password should be used because the /etc/shadow SHA512 hash of the password
#       will appear in the EC2 instance's UserData, which is readable by anyone with EC2
#       metadata query privileges on this AWS account, and IF leaked could be used in a
#       dictionary attack.
try:
  ec2_user_password: Output[str] = pconfig.require_secret('ec2_user_password')
except Exception as e:
  raise XPulumiError(
      "You must set an EC2 User sudo password with \"pulumi -s dev config set --secret ec2_user_password <password>\""
    ) from e

# HashedPassword is a dynamic pulumi provider that computes an SHA512 hash
# of the EC2 user password as it will appear in the instances /etc/shadow file.
# THis allows us to set a sudo password for the EC2 user without passing the
# password in the clear (the hashed password will appear in the EC2 instance's
# UserData, which is readable by anyone with AWS EC2 privileges on this AWS
# account. This is not ideal but much better than passing a password in the clear.)
hashed_password = HashedPassword('ec2_user_hashed_password', ec2_user_password)
hashed_password_str = hashed_password.hashed_password

#Output.all(hashed_password_str).apply(lambda args: pulumi.log.info(f"hashed_password={args[0]}"))

# The xpulumi project name and stack name from which we will
# import our AWS VPC network, subnets, availability zones, cloudwatch group,
# main DNS zone, and other shared resources that can be used by multiple projects
# We will use our own stack name, so that dev will pick up from dev, prod from prod etc.
aws_env_stack_name = f"aws-env:{stack_name}"

# Import our network configuration from the shared stack
vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name)
vpc.stack_export()

# Create our resources in the same region as the inherited VPC
aws_region = vpc.aws_region

# import our cloudwatch group from the shared stack
cw_log_group_id = require_stack_output('cloudwatch_log_group', stack=aws_env_stack_name)
cw = CloudWatch(log_group_id=cw_log_group_id)
cw.stack_export()

# import our shared S3 bucket/root key from the shared stack
shared_s3_uri = require_stack_output('shared_s3_uri', stack=aws_env_stack_name)
stack_s3_uri = shared_s3_uri + f"/g/{pulumi_project_name}/{stack_name}"
pulumi.export("stack_s3_uri", stack_s3_uri)

# Import our main DNS zone from the shared stack. This may be a
# Route53 subzone created by the shared stack, or a top-level
# Route53 zone for a registered, paid public domain such fas "mycompany.com".
# In the latter case, DNS services for the domain must be provided by
# AWS Route53, so this stack can create DNS records in the zone.
dns_zone_id = require_stack_output('main_dns_zone_id', stack=aws_env_stack_name)
dns_zone = DnsZone(resource_prefix='main-', zone_id=dns_zone_id)
dns_zone.stack_export(export_prefix='main_')

# Begin configuring an EC2 instance along with all of its associated
# resources (security group, role, role policy, attached volumes, elastic IP,
# DNS records, etc.). Because commit==False, this object won't actually create any resources until
# we explicitly commit.  That allows us to programmatically add to the configuration
# (for example, adding cloud-init sections and attached volumes).
ec2_instance = Ec2Instance(
    vpc=vpc,
    resource_prefix="frontend-",
    use_config=True,
    cfg_prefix="fe-",

    # The DNS zone in which we will add our DNS records
    parent_dns_zone=dns_zone,  

    # These DNS prefixes to the parent zone which will point at our
    # EC2 instance. An elastic IP is required. An empty string ('') causes
    # the bare parent domain to route to our EC2 instance--obviously on
    # one project can do this for a given zone.
    dns_subnames=[ '', 'www', 'api' ],

    # The TCP port numbers that should be open to the internet
    open_ports=[ 22, 80, 443 ],

    # The pathname to your SSH public key--The corresponding private key
    # will be used to SSH into the instance.
    public_key_file="~/.ssh/id_rsa.pub",

    # The EC2 instance type. May be either x86_64 or Arm64 architecture.
    instance_type="t3.medium",

    # Number of gigabytes to allot for the system boot volume. Note that
    # we will be mounting a separate volume for home directories and to
    # hold docker volumes, so thios drive does not need to account for that
    # space.
    sys_volume_size_gb=40,

    # Afer constructing the Ec2Instance object, wait for an explicit
    # commit before asking Pulumi to create resources. That allows
    # further programmating configuration.
    commit=False
  )


# Add a separate data EBS volume to the instance. Unlike the built-in boot volume,
# this volume is *NOT* destroyed when the EC2 instance is terminated/recreated due
# to a configuration change (e.g., a change in the EC2 instance type or a change
# to the cloud-init settings). This volume will be used for /home as well as
# /var/lib/docker/volumes. So our home directories and
# all docker volumes will be preserved across instance replacement...
#
# This would normally be a quite tricky maneuver, because the AWS EC2 API is stupid and
# neither allows you to attach volumes at EC2 instance creation time, nor allows you
# to create an EC2 instance without starting it immediately. This means there is
# a race between the instance booting and initializing for the first time, and attaching
# the desired volumes (their attachment will look to the instance like a drive was hot-plugged
# some time after boot).
#
# Furthermore, on modern "nitro" EC2 instance types, the volume's device name specified
# at instance launch or volume attach time (e.g., "/dev/sdf") is different than the device name seen
# inside the instance (e.g., "/dev/nvme1p1"), and there is no deterministic mapping
# between the names.
#
# Thankfully, the elaborate dance that is required to make cloud-init work with these dynamic
# volumes is automatically handled for us by the xpulumi Ec2Instance class. It adds a high-priority
# boothook to the cloud-init user-data that waits for all the expected volumes to appear
# before booting proceeds. And, the Ec2Volume object returned here from add_data_volume() provides
# a get_internal_device_name() method we can use to build our mountpoints in the cloud-config
# docuument below...
data_vol = ec2_instance.add_data_volume(volume_size_gb=40)


# Configuration document for the EC2 instance's cloudwatch agent
cloudwatch_cfg: Input[Jsonable] = dict(
  agent = dict(
    metrics_collection_interval = 60,
    run_as_user = "root"
  ),
  logs = dict(
    logs_collected = dict(
      files = dict(
        collect_list = [
          dict(
            file_path = "/var/log/cloud-init-output.log",
            log_group_name = cw.log_group.name,
            log_stream_name = f"{long_stack}-cloud-init-output",
            retention_in_days = 30
          ),
          dict(
            file_path = "/var/log/cloud-init.log",
            log_group_name = cw.log_group.name,
            log_stream_name = f"{long_stack}-cloud-init",
            retention_in_days = 30
          )
        ]
      )
    )
  ),
  metrics = dict(
    aggregation_dimensions = [
      [
        "InstanceId"
      ]
    ],
    append_dimensions = dict(
      AutoScalingGroupName = "${aws:AutoScalingGroupName}",
      ImageId = "${aws:ImageId}",
      InstanceId = "${env:INSTANCE_ID}",
      InstanceType = ec2_instance.instance_type,
    ),
    metrics_collected = dict(
      collectd = dict(
        metrics_aggregation_interval = 60
      ),
      disk = dict(
        measurement = [
          "used_percent"
        ],
        metrics_collection_interval = 60,
        resources = [
          "*"
        ]
      ),
      mem = dict(
        measurement = [
          "mem_used_percent"
        ],
        metrics_collection_interval = 60
      ),
      statsd = dict(
        metrics_aggregation_interval = 60,
        metrics_collection_interval = 10,
        service_address = ":8125"
      )
    )
  )
)


# For bind mounts, the cloud-init "mounts" module requires that mountpoints pre-exist
# before mounting. So we create the docker volumes mountpoint in a boothook, long
# before docker is installed. We also take this opportunity to:
#   - Create the docker group (we do it here instead of in cloud-config
#     so we can set the GID to a stable value).
#   - Install, and configure the AWS cloudwatch agent
#   - On every boot, start the AWS cloudwatch agent
#
# NOTE: The single quotes around 'CWCFGEOF' are essential, since they suppres "${var}"
#       expansion in the HERE document, which would mess up ${aws:...} in
#       the cloudwatch config file...
ec2_instance.add_user_data(Output.concat('''#boothook
#!/bin/bash
mkdir -p -m 710 /var/lib/docker
mkdir -p -m 755 /var/lib/docker/volumes
groupadd -g 998 docker
CWA=/opt/aws/amazon-cloudwatch-agent
CWC=$CWA/etc/config.json
if [ ! -f $CWC ]; then
wget https://s3.''', aws_region, '''.amazonaws.com/amazoncloudwatch-agent-''', aws_region, '''/ubuntu/''', ec2_instance.ami_arch, '''/latest/amazon-cloudwatch-agent.deb
dpkg -i -E ./amazon-cloudwatch-agent.deb
cat >$CWC <<'CWCFGEOF'
''', jsonify_promise(cloudwatch_cfg, separators=(',', ':')), '''
CWCFGEOF
fi
$CWA/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:$CWC -s
'''))

# ECR is AWS's equivalent of Dockerhub. There is a distinct endpoint in each
# region, and for each AWS account. Also, there is a customized authentication
# plugin for docker that allows you to access the repository using your AWS
# credentials.
ecr_domain: str = f"{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com"

docker_config_obj = {
    "credHelpers": {
        "public.ecr.aws": "ecr-login",
        ecr_domain: "ecr-login"
      }
  }
docker_config = json.dumps(docker_config_obj, separators=(',', ':'), sort_keys=True)

# create the main cloud-init document as structured, JSON-able data. xpulumi
# will automatically render this as YAML and properly embed it in the user-data
# block for us. See https://cloudinit.readthedocs.io/en/latest/topics/examples.html.
#
cloud_config_obj = dict(
    docversion=3,    # for debugging, a way to force redeployment

    # Define linux user accounts. Note that having ANY entries in this list will
    # disable implicit creation of the default "ubuntu" account. Note that
    # we do not use cloud-config to create groups, because it does not support
    # setting GID which is important for consistency with mounted volume. We add the
    # docker group in a boothook above...
    users = [
        {
            'name': ec2_instance_username,
            'ssh-authorized-keys': ec2_instance.keypair.public_key,
            'uid': 1001,
            'gid': 1001,
            'shell': '/bin/bash',
            # 'sudo': 'ALL=(ALL) NOPASSWD:ALL',
            'groups': [ 'sudo', 'adm', 'docker', ],
            'hashed_passwd': hashed_password_str,
            'lock_passwd': False,
          },
      ],
    device_aliases = dict(
        # an alias for the /dev/... device name as seen inside the instance
        datavol = data_vol.get_internal_device_name(),
      ),
    disk_setup = dict(
        # This will create a partition table on the disk if not yet partitioned
        datavol = dict(
            table_type = 'gpt',
            layout = True,
            overwrite = False,
          )
      ),
    fs_setup = [
        # This will format the disk if not already formatted
        dict(
            label="DATA",
            filesystem="ext4",
            device="datavol",
            partition="auto",
        ),
      ],
    # Automatically grow partitions if EBS volumes are resized
    growpart = dict(
        devices = ['/', '/data'],
      ),

    # Mount entries correspond exactly to entries in /etc/fstab:
    mounts = [
        # We mount our data volume as /data
        [ 'datavol', '/data', 'auto', 'defaults,discard', '0', '0' ],

        # A bind mount onto the data volume for docker's volumes directory; this will
        # make docker volumes survive replacement of the EC2 instance. Note that this means if
        # you put exeutable binaries in the volumes and you switch the EC2 instance architecture
        # between X86_64 and ARM64, then you may have to rebuild those binaries.
        # Note that this volumes directory is only for explicit docker volumes, not for pulled or
        # build docker images, or for container state. The idea here is that if you want something
        # durable, you will either push it to a repo if it is an image, or create a docker volume if
        # it is runtime state (e.g., you would put a database on a docker volume). Containers are
        # assumed to be disposable.
        [ '/data/docker-volumes', '/var/lib/docker/volumes', 'none', 'x-systemd.requires=/data,x-systemd.automount,bind', '0', '0' ],

        # A bind mount for /home. This ensures that all user home directories (including /home/ubuntu)
        # will survive replacement of the EC2 instance. Not that doing this means it is important to keep UIDs
        # and GIDs stable across configuration changes. Also, if you change EC2 instance architecture
        # between x86_64 and arm64, any binaries you have under your home directory will have to be rebuilt.
        [ '/data/home', '/home', 'none', 'x-systemd.requires=/data,x-systemd.automount,bind', '0', '0' ],
      ],
    fqdn = dns_zone.zone_name,  # Our host's fully qualified name
    repo_update = True,
    repo_upgrade = "all",
    package_update = True,
    package_upgrade = True,
    package_reboot_if_required = True,
    apt = dict(
        sources = {
          # Add docker's dpkg repository to apt-get search list, so we can install latest stable docker
          "docker.list": dict(
              source = "deb [arch=amd64] https://download.docker.com/linux/ubuntu $RELEASE stable",
              keyid = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
            ),
          },
      ),

    # Any packages listed here will get installed with apt-get install
    packages = [
        "jq",
        "awscli",
        "collectd",
        "ca-certificates",
        "curl",
        "gnupg",
        "lsb-release",
        "docker-ce",
        "docker-ce-cli",
        "amazon-ecr-credential-helper",
        "python3-pip",
      ],

    # After all packages are installed, the following commands are run in order
    runcmd = [
        # Install a recent version of aws-cli/boto3/botocore systemwide that supports configuration of
        # EC2 metadata endpoint (the version provided by Ubuntu is quite old).
        [ "pip3", "install", "--upgrade", "boto3", "botocore", "awscli" ],

        # This command sets up docker on the root user to authenticate against ECR using AWS
        # credentials inherited by this EC2 instance through its associated IAM Role (created for
        # us by EC2 instance above). A similar thing could be done for any user.
        [ "bash", "-c", f"mkdir -p /root/.docker && chmod 700 /root/.docker && echo {shlex.quote(docker_config)} > /root/.docker/config.json && chmod 600 /root/.docker/config.json" ],

        # This command adds an iptables rule that will block all docker containers (unless they are on the host network) from
        # accessing the EC2 instance's metadata service. This is an important secuurity precaution, since
        # access to the metadata service allows the caller to impersonate the EC2 instance's IAM Role on AWS, and
        # read any secrets passed to the instance through UserData (e.g., the hashed sudo password).
        # If there are trusted containers, we can create special rules for them...
        # TODO: This must be done on every boot, not just the first boot
        [ "iptables", "--insert", "DOCKER-USER", "--destination", "169.254.169.254", "--jump", "REJECT" ],

        # All done
        [ "bash", "-c", 'echo "All Done!"' ],
      ],
  )

ec2_instance.add_user_data(cloud_config_obj)

# We are done configuring the EC2 instance and associated resources.
# Commit the configuration and let Pulumi create the infrastructure.
ec2_instance.commit()

# Create output variables for our pulumi stack, so that other stacks
# and tools can find the resources we created. For example,
# you can find the publid ip address with the pulumi CLI:
#
#      $ pulumi stack output public_ip
#      52.11.0.68
#
# Or you can get all outputs as a json document:
#
#      $ pulumi stack output -j
#      {
#        "cloudwatch_log_group": "aws-env-dev-log-group",
#        "dns_names": [
#          "xhub.mckelvie.org",
#          "www.xhub.mckelvie.org",
#          "api.xhub.mckelvie.org"
#        ],
#        "main_dns_zone": "xhub.mckelvie.org",
#        "main_dns_zone_id": "Z06463322HJRJCRFUEX3L",
#        "public_ip": "52.11.0.68"
#      }
ec2_instance.stack_export()
