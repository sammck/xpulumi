import pulumi
from pulumi import Output, log as plog
import pulumi_aws as aws
import xpulumi
from xpulumi.runtime import StackOutputs, VpcEnv, aws_resource_options, pconfig

instance_type: str = pconfig.require('instance_type')

secret_input = pconfig.require_secret('secret_input')

backend_outputs = StackOutputs('s3-backend-bucket:global')

vpc = VpcEnv.load()

ami = aws.ec2.get_ami(
    most_recent=True,
    owners=["137112412989"],
    filters=[
        aws.GetAmiFilterArgs(name="name", values=["amzn-ami-hvm-*"])
      ],
    opts=aws_resource_options,
  )

sg = aws.ec2.SecurityGroup(
    'frontend-sg',
    description='HTTP only',
    ingress=[
        aws.ec2.SecurityGroupIngressArgs(
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

server = aws.ec2.Instance(
    'frontend-ec2-instance',
    instance_type=instance_type,
    vpc_security_group_ids=[sg.id],
    user_data=user_data,
    ami=ami.id,
    subnet_id=vpc.public_subnet_ids[0],
    opts=aws_resource_options,
  )

Output.all(backend_outputs.get_outputs()).apply(lambda args: plog.info(f"made it {args[0]}"))


pulumi.export('public_ip', server.public_ip)
pulumi.export('url', Output.concat("http://", server.public_ip))
pulumi.export("secret_output", Output.secret("John is the Walrus"))
pulumi.export("secret_input", secret_input)
pulumi.export("exposed_input", Output.unsecret(secret_input))

pulumi.export("backend_bucket", backend_outputs.get_output('backend_bucket'))
