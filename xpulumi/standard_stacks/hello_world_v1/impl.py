# Copyright (c) 2022 Sam McKelvie
#
# See LICENSE file accompanying this package.
#

def load_stack(
      resource_prefix: str = '',
      cfg_prefix: str = '',
      export_prefix: str = '',
      aws_env_project_name: str = 'awsenv',
      aws_env_import_prefix: str = ''
    ):
  from typing import cast

  import pulumi
  import pulumi_aws
  from pulumi import Output
  from xpulumi.runtime import (
      VpcEnv,
    )
  from xpulumi.runtime.common import (
      stack_name,
      pconfig,
      aws_resource_options,
      aws_invoke_options,
    )

  # The xpulumi project name and stack name from which we will
  # import our AWS VPC network, subnets, availability zones, cloudwatch group,
  # main DNS zone, and other shared resources that can be used by multiple projects
  # We will use our own stack name, so that dev will pick up from dev, prod from prod etc.
  aws_env_stack_name = f"{aws_env_project_name}:{stack_name}"

  instance_type = pconfig.get(f"{cfg_prefix}instance_type")
  if instance_type is None:
    instance_type = 't3.micro'

  # Import our network configuration from the shared stack
  vpc = VpcEnv.stack_import(stack_name=aws_env_stack_name, import_prefix=aws_env_import_prefix)
  vpc.stack_export(export_prefix=export_prefix)

  ami = pulumi_aws.ec2.get_ami(
      most_recent=True,
      owners=["137112412989"],  # Amazon's own account, for Amazon Linux AMIs
      filters=[
          pulumi_aws.ec2.GetAmiFilterArgs(name="name", values=["amzn-ami-hvm-*"])
        ],
      opts=aws_invoke_options,
    )

  sg = pulumi_aws.ec2.SecurityGroup(
      f'{resource_prefix}frontend-sg',
      description='HTTP only',
      ingress=[
          pulumi_aws.ec2.SecurityGroupIngressArgs(
              protocol='tcp',
              from_port=80,
              to_port=80,
              cidr_blocks=['0.0.0.0/0'],
            ),
        ],
      vpc_id=vpc.vpc_id,
      opts=aws_resource_options,
    )

  user_data: str = """
  #!/bin/bash
  echo "It works!!" > index.html
  nohup python -m SimpleHTTPServer 80 &
  """

  server = pulumi_aws.ec2.Instance(
      f'{resource_prefix}frontend-ec2-instance',
      instance_type=instance_type,
      vpc_security_group_ids=[sg.id],
      user_data=user_data,
      ami=ami.id,
      subnet_id=vpc.public_subnet_ids[0],
      opts=aws_resource_options,
    )

  pulumi.export(f'{export_prefix}public_ip', server.public_ip)
  pulumi.export(f'{export_prefix}url', Output.concat("http://", server.public_ip))
