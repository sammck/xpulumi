import json
import shlex

import pulumi
from pulumi import Output
import pulumi_aws as aws
import xpulumi
import xpulumi.runtime
from xpulumi.runtime import (
    VpcEnv,
    DnsZone,
    FrontEndSecurityGroup,
    Ec2KeyPair,
    Ec2Instance,
    require_stack_output,
    CloudWatch,
    UserData,
    UserDataPart,
    enable_debugging,
    xbreakpoint,
    aws_account_id,
    aws_default_region,
  )

enable_debugging()

aws_env_stack_name = 'aws-env:dev'

cw_log_group_id = require_stack_output('cloudwatch_log_group', stack=aws_env_stack_name)
cw = CloudWatch(log_group_id=cw_log_group_id)
cw.stack_export()

vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name)

dns_zone_id = require_stack_output('main_dns_zone_id', stack=aws_env_stack_name)
dns_zone = DnsZone(resource_prefix='main-', zone_id=dns_zone_id)
dns_zone.stack_export(export_prefix='main_')

user_data = UserData()

boothook_text = '''#boothook
#!/bin/bash
echo "It works, instance ID = $INSTANCE_ID!!!" > /var/log/my-boothook.log
'''

user_data.add(boothook_text)

ecr_domain: str = f"{aws_account_id}.dkr.ecr.{aws_default_region}.amazonaws.com"
#front_end_bootstrap_full_repo_name: str = f"{ecr_domain}/{front_end_bootstrap_repo_name}:{front_end_bootstrap_repo_tag}"

docker_config_obj = {
    "credHelpers": {
        "public.ecr.aws": "ecr-login",
        ecr_domain: "ecr-login"
      }
  }
#full_repo_and_tag = f"{ecr_domain}/{bootstrap_repo_name}:{bootstrap_repo_tag}"
docker_config = json.dumps(docker_config_obj, separators=(',', ':'), sort_keys=True)

cloud_config_obj = dict(
    repo_update = True,
    repo_upgrade = "all",
    fqdn = dns_zone.zone_name,
    apt = dict(
        sources = {
          "docker.list": dict(
              source = "deb [arch=amd64] https://download.docker.com/linux/ubuntu $RELEASE stable",
              keyid = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
            ),
          },
      ),

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
      ],

    runcmd = [
        [ "bash", "-c", f"mkdir -p /root/.docker && chmod 700 /root/.docker && echo {shlex.quote(docker_config)} > /root/.docker/config.json && chmod 600 /root/.docker/config.json" ],
        # [ "docker", "pull", full_repo_and_tag ],
        # [ "docker", "run", "--rm", "-v", "/:/host-rootfs", "--privileged", "--net=host", full_repo_and_tag ],
        [ "bash", "-c", 'echo "it works!"' ],
      ],
  )

user_data.add(cloud_config_obj)

ec2_instance = Ec2Instance(
    vpc=vpc,
    resource_prefix="frontend-",
    use_config=True,
    cfg_prefix="fe-",
    parent_dns_zone=dns_zone,
    dns_subnames=[ '', 'www', 'api' ],
    open_ports=[ 22, 80, 443 ],
    public_key_file="~/.ssh/id_rsa.pub",
    instance_type="t3.medium",
    user_data=user_data,
  )
ec2_instance.stack_export()
