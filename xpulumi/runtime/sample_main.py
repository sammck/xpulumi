
# type: ignore

'''
#!/usr/bin/env python3

# type: ignore

import subprocess
import os
import json
import ipaddress

import pulumi
from pulumi import (
  ResourceOptions,
  Output,
)

import pulumi_aws as aws
from pulumi_aws import (
  ec2,
  route53,
  acm,
  cognito,
  ecs,
  ecr,
  elasticloadbalancingv2 as elbv2,
  iam,
  cloudwatch,
  rds,
  kms,
  secretsmanager,
)

from pulumi_util.util import (
  TTL_SECOND,
  TTL_MINUTE,
  TTL_HOUR,
  TTL_DAY,
  jsonify_promise,
  list_of_promises,
  default_val,
)

#-----------------------------------------------------
DEFAULT_CIDR: str = '10.77.0.0/16'
DEFAULT_N_AZS: int = 3
DEFAULT_N_POTENTIAL_SUBNETS: int = 16
#-----------------------------------------------------

config = pulumi.Config()

stack_ref = pulumi.StackReference(f"sammck/my-first-app/dev")


#num_auth_instances = default_val(config.get_int('num_auth_instances'), 1) # simpler and cheaper to debug with 1 container. Scale up for prod. During update, temporarily there will be 2x
long_stack = "%s-%s" % (pulumi.get_project(), pulumi.get_stack())
stack_short_prefix = pulumi.get_stack()[:5] + '-'

global_region = 'us-east-1'
region = default_val('aws_region', aws.get_region())

# We create a seperate AWS pulumi provider bound to us-east-1 because certain AWS resources must be provisioned in that region (e.g., cloudfront
# certificates)
global_aws = aws.Provider('aws-%s' % global_region, region=global_region)
resource_options_global_aws = ResourceOptions(provider=global_aws)

owner_tag = default_val(config.get('owner'), 'sammck@gmail.com')

default_tags = dict(Owner=owner_tag, PulumiStack=long_stack, Project="apitest")
def with_default_tags(*args, **kwargs):
  result = dict(default_tags)
  result.update(*args, **kwargs)
  return result


n_azs = default_val(config.get_int('n_azs'), DEFAULT_N_AZS) # The number of AZs that we will provision our vpc in

azs = sorted(aws.get_availability_zones().names)[:n_azs]

vpc_cidr = default_val(config.get('vpc_cidr'), DEFAULT_CIDR)
vpc_ip_network = ipaddress.ip_network(vpc_cidr)
max_n_subnet_id_bits = 32 - vpc_ip_network.prefixlen
n_potential_subnets = default_val(config.get('n_potential_subnets'), DEFAULT_N_POTENTIAL_SUBNETS)
if n_potential_subnets < 8 or n_potential_subnets > (1 << 31) or (n_potential_subnets & (n_potential_subnets - 1)) != 0:
  raise RuntimeError("Config value n_potential_subnets must be a power of 2 >= 8: %d" % n_potential_subnets)
n_subnet_id_bits = 0
x = n_potential_subnets
while x > 1:
  x //= 2
  n_subnet_id_bits += 1
if n_subnet_id_bits > max_n_subnet_id_bits:
  raise RuntimeError("Config value n_potential_subnets is greater than maximum allowed (%d) by vpc CIDR %s: %d" % (1 << max_n_subnet_id_bits, vpc_cidr, n_potential_subnets))

vpc_potential_subnet_ip_networks = list(vpc_ip_network.subnets(prefixlen_diff=n_subnet_id_bits))
public_subnet_ip_networks = vpc_potential_subnet_ip_networks[:n_azs]
private_subnet_ip_networks = vpc_potential_subnet_ip_networks[n_potential_subnets//2:n_potential_subnets//2+n_azs]

public_subnet_cidrs = [str(x) for x in public_subnet_ip_networks]
private_subnet_cidrs = [str(x) for x in private_subnet_ip_networks]

zone_name = config.get('zone_name')
if zone_name is None:
  parent_zone_name = default_val(config.get('parent_zone_name'), 'mckelvie.org')
  zone_prefix = default_val(config.get('zone_prefix'), pulumi.get_stack())
  zone_name = '%s.%s' % (zone_prefix, parent_zone_name)
else:
  zone_prefix, parent_zone_name = zone_name.split('.', 1)


auth_image_src_dir = os.path.abspath('web-app/app')

db_password = default_val(config.get('db_password'), 'ikhGGZQ4f8scdA9TXfy6Uhj7')
num_db_instances = default_val(config.get_int('num_db_instances'), 1)
db_engine_version = default_val(config.get('db_engine_version'), '5.7.mysql_aurora.2.07.1')  # Pulumi requires a fully qualified name
db_instance_type = default_val(config.get('db_instance_type'), 'db.t3.small')
db_database_name = 'apitest_dev_main'
#----------------------------------------------------------------------------------------------

# create a master AWS KMS key for general user
master_key = kms.Key(
  'master-key',
  # opts=None,
  # deletion_window_in_days=None,
  description="Master key for Pulumi stack %s" % long_stack,
  # enable_key_rotation=None,
  # is_enabled=None,
  # key_usage=None,
  # policy=None,
  tags=with_default_tags(Name='%s-master' % long_stack)
)

# create an Alias for our master key so it is readable in the console and has a friendly name
master_key_alias = kms.Alias(
  'master-key-alias',
  # opts=None,
  name='alias/%s/master' % long_stack,
  # name_prefix=None,
  target_key_id=master_key.arn
)

pulumi.export('master_key_alias', master_key_alias.name)

# Create a Secret that will contain our DB password
db_password_secret = secretsmanager.Secret(
  'db-password-secret',
  # opts=None,
  description='MySQL admin password for RDS DB in Pulumi stack %s' % long_stack,
  kms_key_id=master_key.arn,
  name='%s-db-password' % long_stack,
  # name_prefix=None,
  # policy=None,
  # recovery_window_in_days=None,
  # rotation_lambda_arn=None,
  # rotation_rules=None,
  tags=with_default_tags(Name='%s-db-password' % long_stack),
)

pulumi.export('db_password_secret', db_password_secret.name)

# Create a SecretVersion that contains the current DB password
db_password_secret_version = secretsmanager.SecretVersion(
  'db-password-secret-version',
   # opts=None,
   # secret_binary=None,
   secret_id=db_password_secret.arn,
   secret_string=db_password,
   # version_stages=None
)

# create a CloudWatch log group for all our logging needs (currently, from task containers)
cloudwatch_log_group = cloudwatch.LogGroup(
  'cloudwatch_log_group',
  # opts=None,
  # kms_key_id=None,
  name="%s-log-group" % long_stack, 
  # name_prefix=None, 
  retention_in_days=30, 
  tags=default_tags
)

# create a VPC that our whole stack and dependent services will run in
vpc = ec2.Vpc(
  'vpc',
  cidr_block=vpc_cidr,
  enable_dns_hostnames=True,
  enable_dns_support=True,
  tags=default_tags
)

pulumi.export('vpc-id', vpc.id)

# create public subnets in separate AZs
public_subnets = []
for i, cidr in enumerate(public_subnet_cidrs):
  public_subnets.append(
    ec2.Subnet(
      'public-subnet-%d' % i,
      availability_zone=azs[i],
      vpc_id=vpc.id,
      cidr_block=cidr,
      map_public_ip_on_launch=True,
      tags=default_tags
    )
  )

public_subnet_ids = [  x.id for x in public_subnets ]

# create private subnets in separate AZs.
# TODO: currently these are the same as public subnets. We can change
# that with a NAT gateway, no-assign public IP, and network ACLs.
private_subnets = []
for i, cidr in enumerate(private_subnet_cidrs):
  private_subnets.append(
    ec2.Subnet(
      'private-subnet-%d' % i,
      availability_zone=azs[i],
      vpc_id=vpc.id,
      cidr_block=cidr,
      map_public_ip_on_launch=True,   # review: probably want to use NAT gateway for private subnets...?
      tags=default_tags
    )
  )

private_subnet_ids = [ x.id for x in private_subnets ]

# convenient list of all subnets, public and private
subnets = public_subnets + private_subnets
subnet_ids = [ x.id for x in subnets ]

# Create an internet gateway to route internet traffic to/from public IPs attached to the VPC
internet_gateway = ec2.InternetGateway('vpc-gateway', tags=default_tags, vpc_id=vpc.id)

# Create a default route table for the VPC that routes everything inside the VPC CIDR locally,
# and everything else to the internet through the internet gateway
# TODO: provide direct VPC routing to AWS services
route_table = ec2.DefaultRouteTable(
  "route-table",
  default_route_table_id=vpc.default_route_table_id,
  routes=[
    dict(cidr_block="0.0.0.0/0", gateway_id=internet_gateway.id)
  ],
  tags=default_tags
)

# Attach all subnets to our default route table
route_table_associations = []
for i, subnet in enumerate(subnets):
  route_table_associations.append(
    ec2.RouteTableAssociation(
      "default-route-table-association-%d" % i,
      route_table_id=route_table.id,
      subnet_id=subnet.id
    )
  )


# Get the parent DNS zone under which our subzone will be created. The parent zone is
# not managed by this stack, but must be a Route53 zone.  E.g., "mckelvie.org". We will
# create An NS record in this parent zone that points to our subzone.
public_parent_zone = route53.get_zone(name=parent_zone_name, private_zone=False)

# Create a public DNS zone for us to use; e.g., apitest.mckelvie.org
public_zone = route53.Zone(
  'public-zone',
  # opts=,
  comment='Public zone for pulumi stack %s' % long_stack,
  delegation_set_id=None,
  force_destroy=True,
  name=zone_name,
  tags=default_tags,
  # vpcs=None,
)
pulumi.export('zone-name', zone_name)
pulumi.export('public-zone-id', public_zone.zone_id)

# Create an NS record in the parent zone that points to our zone's name servers.
public_parent_zone_ns_record = route53.Record(
  'public-parent-zone-ns-record',
  # opts=None,
  # aliases=None, 
  # allow_overwrite=None, 
  # failover_routing_policies=None,
  # geolocation_routing_policies=None, 
  # health_check_id=None, 
  # latency_routing_policies=None, 
  # multivalue_answer_routing_policy=None, 
  name=zone_name, 
  records=public_zone.name_servers,
  # set_identifier=None, 
  ttl=TTL_MINUTE * 10,
  type='NS',
  # weighted_routing_policies=None,
  zone_id=public_parent_zone.zone_id
)


# Create a security group for the front-end EC2 instance, that allows it to
# listen on port 22 (SSH), 80 (HTTP), and 443 (HTTPS), and send anywhere.
front_end_security_group = ec2.SecurityGroup(
  'front-end-security-group',
  opts=None,
  description='%s front-end security group. Public SSH, HTTP, and HTTPS' % long_stack,
  egress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 ANY",
      protocol='tcp',
      from_port=1,
      to_port=65534
    ),
  ],
  ingress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 SSH",
      protocol='tcp',
      from_port=22,
      to_port=22
    ),
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 HTTP",
      protocol='tcp',
      from_port=80,
      to_port=80
    ),
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 HTTPS",
      protocol='tcp',
      from_port=443,
      to_port=443
    )
  ],
  # name=None,
  name_prefix=long_stack + '-',
  # revoke_rules_on_delete=None,
  tags=default_tags,
  vpc_id=vpc.id
)


# define a policy that allows EC2 to assume our roles for the purposes of creating EC2 instances
ec2_assume_role_policy_obj = {
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


# Create an IAM role for ourFront end EC2 instance to run in. Allow it to read the db password secret.
front_end_role = iam.Role(
  'front-end-role',
  # opts=None,
  assume_role_policy=json.dumps(ec2_assume_role_policy_obj, sort_keys=True),
  description="Front-end EC2 instance role in Pulumi stack %s" % long_stack,
  # force_detach_policies=None,
  max_session_duration=12*TTL_HOUR,
  name='%s-front-end-role' % long_stack,
  # name_prefix=None,
  path='/pstack=%s/' % long_stack,
  # permissions_boundary=None,
  tags=default_tags
)


# create an instance profile for our front-end that allows it to assume our front-end role
front_end_instance_profile = aws.iam.InstanceProfile(
    "front-end-instance-profile",
    role=front_end_role.name,
    tags=default_tags,
  )

# select an EC2 instance type for the front end
ec2_front_end_instance_type = default_val(config.get('front_end_instance_type'), 't3.medium')
front_end_volume_size_gb = default_val(config.get_int('front_end_volume_size_gb'), 40)

# create an EC2 keypair to allow SSH login to the front-end
front_end_shh_public_key = config.get('front_end_ssh_public_key')
if front_end_shh_public_key is None:
  front_end_shh_public_key_file: str = default_val(config.get('front_end_ssh_public_key_file'), '~/.ssh/id_rsa.pub')
  front_end_shh_public_key_file = os.path.expanduser(front_end_shh_public_key_file)
  with open(front_end_shh_public_key_file) as f:
    front_end_shh_public_key = f.read().rstrip()

front_end_keypair = aws.ec2.KeyPair(
    'front-end-keypair',
    key_name_prefix='front-end-',
    public_key=front_end_shh_public_key,
    tags=default_tags,
  )


AMI_OWNER_CANONICAL = "099720109477"

# Find the most recent AMI for Ubuntu 20.04
ubuntu = aws.ec2.get_ami(
    most_recent=True,
    filters=[
        aws.ec2.GetAmiFilterArgs(
            name="name",
            values=[ "ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-*" ],
          ),
        aws.ec2.GetAmiFilterArgs(
            name="virtualization-type",
            values=[ "hvm" ],
          ),
      ],
    owners=[AMI_OWNER_CANONICAL],
  )

# Create an elastic IP address for the front end. This allows the IP address to remain stable even if the instance is
# destroyed and recreated
front_end_eip = aws.ec2.Eip(
    'front-end-eip',
    vpc=True,
    tags=with_default_tags(Name=zone_name),
  )


# Create an EC2 instance for our front-end
front_end_ec2_instance = aws.ec2.Instance(
    'ec2-front-end',
    ami=ubuntu.id,
    instance_type=ec2_front_end_instance_type,
    iam_instance_profile=front_end_instance_profile.name,
    key_name=front_end_keypair.key_name,
    associate_public_ip_address=False,  # deferred until EIP is assigned
    subnet_id=public_subnet_ids[0],
    vpc_security_group_ids=front_end_security_group.id,
    root_block_device=dict(volume_size=front_end_volume_size_gb),
    tags=with_default_tags(Name=zone_name),
    volume_tags=with_default_tags(Name=zone_name),
  )


# associate the EIP with the instance

front_end_eip_assoc = aws.ec2.EipAssociation(
    "front-end-eip-assoc",
    instance_id=front_end_ec2_instance.id,
    allocation_id=front_end_eip.id
  )

'''

'''
# Create a wildcard SSL certificate for "*.<zone-name>" as well as bare "<zone-name";
# e.g., "*.apitest.mckelvie.org" and "apitest.mckelvie.org". This certificate is created in our own region,
# where it is used by ALB. Note that CloudFront requires certificates in us-east-1, so if
# necessary we will create the same certificate there, too.
# We let ACM create and manage the private key, as we will only use it with ALB or CloudFront
region_wildcard_cert = acm.Certificate(
  'region-wildcard-cert',
  # opts=,
  # certificate_authority_arn=None,
  # certificate_body=None,
  # certificate_chain=None,
  domain_name='*.%s' % zone_name,
  # options=None, 
  # private_key=None,
  subject_alternative_names=[
    zone_name # allow a bare zone name in addition to wildcard prefix
  ],
  tags=default_tags,
  validation_method='DNS'  # our ownership of the domains will be validated by creating a special DNS entry in the zone
)

# Create a special validation record requested by ACM in our DNS zone, to prove that we own the zone and requested the cert.
region_vopts = region_wildcard_cert.domain_validation_options[0]
region_wildcard_cert_validation_record = route53.Record(
  'region-wildcard-cert-validation-record',
  name=region_vopts['resourceRecordName'],
  zone_id=public_zone.id,
  type=region_vopts['resourceRecordType'],
  records=[region_vopts['resourceRecordValue']],
  ttl=10*TTL_MINUTE
)

# Wait for ACM to see our special DNS record and finish creating the Certificate
region_wildcard_cert_validation = acm.CertificateValidation(
  'region-wildcard-cert-validation',
  # opts=None,
  certificate_arn=region_wildcard_cert.arn,
  validation_record_fqdns=[ region_wildcard_cert_validation_record.fqdn ]
)

# if we are not in us-east-1, then we also have to create a certificate there, because CloudFront
# always uses us-east-1.
if region == global_region:
  # Our service is in us-east-1, so the CloudFrontCertificate is the same as the regional one.
  global_wildcard_cert = region_wildcard_cert
  global_vopts = region_vopts
  global_wildcard_cert_validation_record = region_wildcard_cert_validation_record
  global_wildcard_cert_validation = region_wildcard_cert_validation
else:
  # Our service is not in us-east-one, so recreate the certificate in us-east-1 by
  # specifying a different aws "provider"
  global_wildcard_cert = acm.Certificate(
    'global-wildcard-cert',
    opts=resource_options_global_aws,   # Run in us-east-1
    # certificate_authority_arn=None,
    # certificate_body=None,
    # certificate_chain=None,
    domain_name='*.%s' % zone_name,
    # options=None, 
    # private_key=None,
    subject_alternative_names=[
      zone_name # allow a bare zone name in addition to wildcard prefix
    ],
    tags=default_tags,
    validation_method='DNS'
  )

  global_vopts = global_wildcard_cert.domain_validation_options[0]

  # it turns out that regardless of region, cert validation asks you to create the same validation DNS record, so we can
  # just leave the regional one in place that we created above, and use it for global validation.
  global_wildcard_cert_validation_record = region_wildcard_cert_validation_record

  # Wait for ACM to see our special DNS record and finish creating the Certificate
  global_wildcard_cert_validation = acm.CertificateValidation(
    'global-wildcard-cert-validation',
    opts=resource_options_global_aws,
    certificate_arn=global_wildcard_cert.arn,
    validation_record_fqdns=[ global_wildcard_cert_validation_record.fqdn ]
  )

# Create a security group for the load balancer frond-end, that allows it to
# listen on port 80 and 443 (and send anywhere)
lb_security_group = ec2.SecurityGroup(
  'lb-security-group',
  opts=None,
  description='%s frontend security group. Public HTTP and HTTPS' % long_stack,
  egress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 ANY",
      protocol='tcp',
      from_port=1,
      to_port=65534
    ),
  ],
  ingress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 HTTP",
      protocol='tcp',
      from_port=80,
      to_port=80
    ),
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 HTTPS",
      protocol='tcp',
      from_port=443,
      to_port=443
    )
  ],
  # name=None,
  name_prefix=long_stack + '-',
  # revoke_rules_on_delete=None,
  tags=default_tags,
  vpc_id=vpc.id
)

# Create a target group that the load balancer can forward requests to. Instances inhe group
# will be identified by internal IP address, which is a requirement for FARGATE services.
# The group will listen on port 5000 for HTTP requests forwarded by the load balancer. A health
# check at "http://<internal-ip>:5000/ping" will be used to detect failing instances. 
lb_target_group = elbv2.TargetGroup(
  'lb-target-group',
  # opts=None,
  deregistration_delay=15*TTL_SECOND,  # default is 5 minuteswhich delays ecs update stabilization.
                                       # shortening for debug cluster. Too short causes live HTTP
                                       # requests to die rather than drain.
  health_check=dict(
    enabled=True,
    interval=30.0,
    path='/ping',
  ),
  # lambda_multi_value_headers_enabled=None,
  # name=None,
  # name_prefix=long_stack,
  port=5000,
  protocol='HTTP',
  # proxy_protocol_v2=None,
  # slow_start=None,
  # stickiness=None,
  tags=default_tags,
  target_type='ip',
  vpc_id=vpc.id
)

# Create an application load balancer (ALB)
load_balancer = elbv2.LoadBalancer(
  'load-balancer',
  # opts=None,
  # access_logs=None,
  # enable_cross_zone_load_balancing=None,
  # enable_deletion_protection=None,
  # enable_http2=None,
  # idle_timeout=None,
  # internal=None,
  ip_address_type='ipv4',
  load_balancer_type='application',
  # name=None,
  name_prefix=stack_short_prefix,
  security_groups=[ lb_security_group.id ],
  # subnet_mappings=None,
  subnets=public_subnet_ids,
  tags=default_tags,
)

# Create an HTTPS listener for the ALB on port 443 that uses our wildcard cert
# and forwards as HTTP to the target group
https_listener = elbv2.Listener(
  'https-listener',
  # opts=None,
  certificate_arn=region_wildcard_cert.arn,
  default_actions=[
    dict(
      order=1.0,
      target_group_arn=lb_target_group.arn,
      type='forward'
    )
  ],
  load_balancer_arn=load_balancer.arn,
  port=443,
  protocol='HTTPS',
  ssl_policy="ELBSecurityPolicy-2016-08",
)

# Add a special rule to the HTTPS listener that requires OpenID Connect (OIDC)
# authentication managed by the ALB, and if the client is not authenticated, redirects
# the client to a OneLogin flow bound to a specially provisioned onelogin App.
# With this rule, no requests through HTTPS will be allowed through to the target group
# without valid authentication.
# When authentication is successful, the ALB places a signed JWT in the X-Amzn-Oidc-Data
# header and forwards to the target group as HTTP.
https_listener_rule = elbv2.ListenerRule(
  'https-oidc-auth-listener-rule',
  # opts=None,
  actions=[
    {
      # 'order': 1.0,
      'type': 'authenticate-oidc',
      'authenticateOidc': dict(
        # authenticationRequestExtraParams=dict(),
        authorizationEndpoint="https://openid-connect.onelogin.com/oidc/auth",
        clientId="9837a6b0-25ca-0138-6f6c-028d8bbb779a100147",  # from OneLogin app config
        client_secret="423b9f03e92d10acf3baeeb63771b8b0075c46dca35f68b8f64e9f2a5051c373",  # # TODO: From OneLogin App Config; encrypt this (not super sensitive but...)
        issuer="https://openid-connect.onelogin.com/oidc",
        onUnauthenticatedRequest="authenticate",  # deny, allow are other options
        scope='openid',
        sessionCookieName="ApiTestDevAlbAuthCookie",
        sessionTimeout=1.0*TTL_DAY*7,               # 7 days is the maximum allowed
        tokenEndpoint="https://openid-connect.onelogin.com/oidc/token",
        userInfoEndpoint="https://openid-connect.onelogin.com/oidc/me",
      )
    },
    {
      # 'order': 2.0,
      'type': 'forward',
      'target_group_arn': lb_target_group.arn,
    },
  ],
  conditions=[
    dict(
      hostHeader=dict(
        values=[ 'auth.%s' % zone_name ]
      )
    )
  ],
  listener_arn=https_listener.arn,
  priority=None
)

# Create an HTTP listener for the ALB on port 80 that by default simply redirects all traffic to HTTPS.
http_listener = elbv2.Listener(
  'http-listener',
  # opts=None,
  default_actions=[
    dict(
      order=1.0,
      type='redirect',
      redirect=dict(
        # host=None,
        # path=None,
        port=443,
        protocol="HTTPS",
        # query=None,
        status_code='HTTP_301', # Permanently moved
      )
    )
  ],
  load_balancer_arn=load_balancer.arn,
  port=80,
  protocol='HTTP',
)

# create a "www.<zone>" DNS entry that points at out load balancer
public_www_dns_entry = route53.Record(
  'public-www-dns-record',
  # opts=None,
  # aliases=None, 
  # allow_overwrite=None, 
  # failover_routing_policies=None, 
  # geolocation_routing_policies=None, 
  # health_check_id=None, 
  # latency_routing_policies=None, 
  # multivalue_answer_routing_policy=None, 
  name='www.%s' % zone_name, 
  records=[ load_balancer.dns_name ],
  # set_identifier=None, 
  ttl=TTL_MINUTE * 10,
  type='CNAME',
  # weighted_routing_policies=None,
  zone_id=public_zone.zone_id
)

# Create an "auth.<zone>" DNS entry that points at our load balancer
public_auth_dns_entry = route53.Record(
  'public-auth-dns-record',
  # opts=None,
  # aliases=None, 
  # allow_overwrite=None, 
  # failover_routing_policies=None, 
  # geolocation_routing_policies=None, 
  # health_check_id=None, 
  # latency_routing_policies=None, 
  # multivalue_answer_routing_policy=None, 
  name='auth.%s' % zone_name, 
  records=[ load_balancer.dns_name ],
  # set_identifier=None, 
  ttl=TTL_MINUTE * 10,
  type='CNAME',
  # weighted_routing_policies=None,
  zone_id=public_zone.zone_id
)

# Create a special route53 "alias" DNS entry for "<zone>" that
# makes the bare zone DNS name work and route to the LB as well.
public_bare_dns_entry = route53.Record(
  'public-bare-dns-record',
  # opts=None,
  aliases=[
    dict(
      evaluate_target_health=False,
      name=load_balancer.dns_name,
      zone_id=load_balancer.zone_id
    )
  ], 
  # allow_overwrite=None, 
  # failover_routing_policies=None, 
  # geolocation_routing_policies=None, 
  # health_check_id=None, 
  # latency_routing_policies=None, 
  # multivalue_answer_routing_policy=None, 
  name=zone_name, 
  # records=[ load_balancer.dns_name ],
  # set_identifier=None, 
  # ttl=TTL_MINUTE * 10,
  type='A',
  # weighted_routing_policies=None,
  zone_id=public_zone.zone_id
)

# Create a security group for our DB to run in
# Create a security group in which our auth ECS task runs. It can receive
# on port 5000 from the load balancer, and can send anywhere.
db_security_group = ec2.SecurityGroup(
  'db-security-group',
  # opts=None,
  description='%s Database security group. VPC ingress on 3306' % long_stack,
  egress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 ANY",
      protocol='tcp',
      from_port=1,
      to_port=65534
    ),
  ],
  ingress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],  # TODO: Turn this off after debugging
      # cidr_blocks=[ vpc_cidr ],
      description="IPV4 MySQL",
      protocol='tcp',
      from_port=3306,
      to_port=3306
    )
  ],
  name='%s-db-sg' % long_stack,
  # name_prefix=None,
  # revoke_rules_on_delete=None,
  tags=default_tags,
  vpc_id=vpc.id
)

# create an RDS subnet group for our DB to run in
db_subnet_group = rds.SubnetGroup(
  'db-subnet-group',
  # opts=None,
  description="Subnets for Aurora cluster in pulumi %s stack" % long_stack,
  name='%s-db-subnets' % long_stack,
  # name_prefix=None,
  subnet_ids=public_subnet_ids,
  tags=default_tags,
)

# Create an Aurora database cluster for general use
db_cluster = rds.Cluster(
  'db-cluster',
  opts=None,
  apply_immediately=True,
  availability_zones=azs,
  # backtrack_window=None,
  # backup_retention_period=None,
  cluster_identifier='db-%s' % long_stack,
  # cluster_identifier_prefix=None,
  # cluster_members=None,
  copy_tags_to_snapshot=None,
  database_name=db_database_name,
  # db_cluster_parameter_group_name=None,
  db_subnet_group_name=db_subnet_group.name,
  # deletion_protection=None,
  # enable_http_endpoint=None,
  # enabled_cloudwatch_logs_exports=None,
  engine='aurora-mysql',
  # engine_mode=None,
  engine_version=db_engine_version,
  # final_snapshot_identifier='%s-final-snapshot' % long_stack,
  # global_cluster_identifier=None,
  # iam_database_authentication_enabled=None,
  # iam_roles=None,
  # kms_key_id=None,
  master_password=db_password,
  master_username='dbadmin',
  # port=None,
  # preferred_backup_window=None,
  # preferred_maintenance_window=None,
  # replication_source_identifier=None,
  # s3_import=None,
  # scaling_configuration=None,
  # skip_final_snapshot=None,
  # snapshot_identifier=None,
  # source_region=None,
  # storage_encrypted=None,
  tags=default_tags,
  vpc_security_group_ids=[ db_security_group.id ],
)

# Create Aurora DB instances in the cluster
db_instances = []
for i in range(num_db_instances):
  azIndex = i % len(azs)
  az = azs[azIndex]
  db_instance = rds.ClusterInstance(
    'db-instance-%d' % i,
    # opts=None,
    apply_immediately=True,
    auto_minor_version_upgrade=True,
    availability_zone=az,
    # ca_cert_identifier=None,
    cluster_identifier=db_cluster.id,
    copy_tags_to_snapshot=True,
    # db_parameter_group_name=None,
    db_subnet_group_name=db_subnet_group.name,
    engine='aurora-mysql',
    engine_version=db_engine_version,
    identifier='db-%s-instance-%d' % (long_stack, i),
    # identifier_prefix=None,
    instance_class=db_instance_type,
    # monitoring_interval=None,
    # monitoring_role_arn=None,
    # performance_insights_enabled=None,
    # performance_insights_kms_key_id=None,
    # preferred_backup_window=None,
    # preferred_maintenance_window=None,
    # promotion_tier=None,
    publicly_accessible=True, # TODO: Turn this off after debugging!
    tags=default_tags,
  )

# create a "db.<zone>" DNS entry that points at our RDS database cluster
public_db_dns_entry = route53.Record(
  'public-db-dns-record',
  # opts=None,
  # aliases=None, 
  # allow_overwrite=None, 
  # failover_routing_policies=None, 
  # geolocation_routing_policies=None, 
  # health_check_id=None, 
  # latency_routing_policies=None, 
  # multivalue_answer_routing_policy=None, 
  name='db.%s' % zone_name, 
  records=[ db_cluster.endpoint ],
  # set_identifier=None, 
  ttl=TTL_MINUTE * 10,
  type='CNAME',
  # weighted_routing_policies=None,
  zone_id=public_zone.zone_id              # TODO: if we implement a private internal zone, this could go there
)

db_dns_name = public_db_dns_entry.name
pulumi.export('db_dns_name', db_dns_name)

# Create an ECS cluster in which we can deploy ECS services and tasks
ecs_cluster = ecs.Cluster(
  'ecs-cluster',
  # opts=None,
  # capacity_providers=None,
  # default_capacity_provider_strategies=None,
  name="%s-ecs-cluster" % long_stack,
  settings=[
    dict(
      name='containerInsights',
      value='enabled'
    )
  ],
  tags=default_tags
)

pulumi.export('ecs-cluster', ecs_cluster.name)

# Create an IAM policy that allows ECS to assume roles we will create
# For our service tasks to run in.
ecs_assume_role_policy_obj = {
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "",
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}


# Create an IAM role for our auth task to run in. Allow it to read the db password secret.
auth_task_role = iam.Role(
  'auth-task-role',
  # opts=None,
  assume_role_policy=json.dumps(ecs_assume_role_policy_obj, sort_keys=True),
  description="auth-task role in Pulumi stack %s" % long_stack,
  # force_detach_policies=None,
  max_session_duration=12*TTL_HOUR,
  name='%s-auth-task-role' % long_stack,
  # name_prefix=None,
  path='/pstack=%s/' % long_stack,
  # permissions_boundary=None,
  tags=default_tags
)
'''
"""
# Create a RolePolicy for our service task that allows it to read the DB password
auth_task_role_policy_obj = {
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
      ],
      "Resource": [
      ]
    }
  ]
}

auth_task_role_policy = iam.RolePolicy(
  'auth-task-role-policy',
  # opts=None,
  name="%s-auth-role-policy" % long_stack,
  # name_prefix=None,
  policy=jsonify_promise(auth_task_role_policy_obj),
  role=auth_task_role.name
)
"""

'''

# create an ECR repository to hold docker images for our auth service container
auth_ecr_repository_name = "auth-%s" % long_stack
auth_ecr_repository = ecr.Repository(
  'auth-ecr-repository',
  # opts=None,
  # image_scanning_configuration=None,
  image_tag_mutability='MUTABLE',
  name=auth_ecr_repository_name,
  tags=default_tags)

pulumi.export('auth-ecr-repository-name', auth_ecr_repository.name)
pulumi.export('auth-ecr-repository-url', auth_ecr_repository.repository_url)

# the fully qualified repository name including the aws endpoint name is an output
# from auth_ecr_repository. We at :latest on to it as a future
latest_image_url = Output.concat(auth_ecr_repository.repository_url, ':latest')
pulumi.export('auth-ecr-latest-image-url', latest_image_url)

# Build the local docker image, tag it, push it to the ECR repository, and
# compute the fully hashed image name. This function returns a future that builds
# and pushes the image after the fully qualified repository name is known.
def get_hashed_auth_image_url():
  def gen_hashed_auth_image_url(repository_name, repository_url, region):
    region='us-west-2'  # some reason not working
    local_image = "%s:latest" % repository_name
    image_url = "%s:latest" % repository_url

    # subprocess.check_call(['docker', 'build', '-t', 'auth-apitest-dev', '-t', repository_name, auth_image_src_dir])
    subprocess.check_call(['docker', 'tag', 'auth-apitest-dev:latest', local_image])
    subprocess.check_call(['docker', 'tag', local_image, image_url])
    subprocess.check_call('$(aws --region %s ecr get-login --region %s --no-include-email) >/dev/null 2>&1' % (region, region), shell=True)
    subprocess.check_call(['docker', 'push', image_url])
    hashed_image_url = subprocess.check_output(['docker', 'inspect', '--format={{index .RepoDigests 0}}', image_url]).decode('utf-8').strip()

    return hashed_image_url

  result = Output.all(auth_ecr_repository.name, auth_ecr_repository.repository_url, region).apply(lambda args: gen_hashed_auth_image_url(*args))
  return result

hashed_auth_image_url = get_hashed_auth_image_url()

# Create a security group in which our auth ECS task runs. It can receive
# on port 5000 from the load balancer, and can send anywhere.
auth_task_security_group = ec2.SecurityGroup(
  'auth-task-security-group',
  opts=None,
  description='%s backend ECS task security group. Public HTTP on 5000' % long_stack,
  egress=[
    dict(
      cidr_blocks=[ '0.0.0.0/0' ],
      description="IPV4 ANY",
      protocol='tcp',
      from_port=1,
      to_port=65534
    ),
  ],
  ingress=[
    dict(
      cidr_blocks=[ vpc_cidr ],   # TODO lock down to load balancer
      description="IPV4 HTTP",
      protocol='tcp',
      from_port=5000,
      to_port=5000
    )
  ],
  # name=None,
  name_prefix=long_stack + '-',
  # revoke_rules_on_delete=None,
  tags=default_tags,
  vpc_id=vpc.id
)

# Create the IAM role that ECS uses to perform actions on behalf of the auth task; e.g., to pull
# docker images from ECR, and to fetch and decryptthe database password secret for
# injection into the container.
auth_task_execution_role = iam.Role(
  'ecs-task-execution-role',
  # opts=None,
  assume_role_policy=json.dumps(ecs_assume_role_policy_obj, sort_keys=True),
  description="ECS auth task execution role in Pulumi stack %s" % long_stack,
  # force_detach_policies=None,
  max_session_duration=12*TTL_HOUR,
  name='%s-auth-task-execution-role' % long_stack,
  # name_prefix=None,
  path='/pstack=%s/' % long_stack,
  # permissions_boundary=None,
  tags=default_tags
)

# Create a RolePolicy for our auth service that allows the ECS docker agent to:
#    - Read docker image from ECR
#    - Publish task logs to cloudwatch
#    - Read the DB password secret from secretsmanager
auth_task_execution_role_policy_obj = {
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters",
        "secretsmanager:GetSecretValue",
        "kms:Decrypt",
      ],
      "Resource": [
        db_password_secret.arn,
        master_key.arn,            # required to decrypt secrets in secretsmanager
      ]
    }
  ]
}

auth_task_execution_role_policy = iam.RolePolicy(
  'auth-task-execution-role-policy',
  # opts=None,
  name="%s-auth-task-execution-role-policy" % long_stack,
  # name_prefix=None,
  policy=jsonify_promise(auth_task_execution_role_policy_obj),
  role=auth_task_execution_role.name
)


# Create a container definition for our auth container, as a json-serializable future
# Note that because we are using the "latest" image, pulumi will not notice pushed changes
# to the repository since the image name does not change. This is a trade-off
# with making it easy to directly upgrade the ECS service without using pulumi, which
# is nice for iterating on a service.
def get_auth_container_definition_obj():
  def gen_result(
      db_password_secret_arn,
      image_url,
      region,
      log_group_name,
      db_dns_name,
      db_database_name):
    return dict(
      essential=True,
      image=image_url, # could use hashed_image_url but then could not redeploy without replacing task definition
      name='auth-app',
      PortMappings=[
        dict(
          ContainerPort=5000,
          # HostPort=5000,
          Protocol='tcp'
        )
      ],
      logConfiguration=dict(
        logDriver='awslogs',
        options={
          'awslogs-region': 'us-west-2',   # TODO: dynamic
          'awslogs-group': log_group_name,  # TODO: dynamic
          'awslogs-stream-prefix': 'auth-service'
        },
      ),
      environment= [
        dict(name='DB_DNS_NAME', value=db_dns_name),
        dict(name='DB_PORT', value='3306'),
        dict(name='DB_DATABASE_NAME', value=db_database_name),
      ],
      secrets=[
        dict(
          name='DB_PASSWORD', # The environment variable that will receive the secret inside the container
          valueFrom=db_password_secret_arn
        ),
      ],
      stopTimeout=10,        # if flask does not shut down this is how long ECS waits before killing it
    )
  return Output.all(
      db_password_secret.arn,
      latest_image_url,
      region,
      cloudwatch_log_group.name,
      db_dns_name,
      db_database_name,
    ).apply(lambda args: gen_result(*args))


# create a list of container definitions for our auth task, as a json-serializable future

auth_task_container_definitions = list_of_promises(
  [
    get_auth_container_definition_obj()
  ]
)

# create a list of container definitins for our auth task, as a future string containing serialized json.
auth_task_container_definitions_json = jsonify_promise(auth_task_container_definitions)

# Create an ECS  task definition for our auth service.
auth_task_definition = ecs.TaskDefinition(
  'auth-ecs-task-definition',
  # opts=None,
  container_definitions=auth_task_container_definitions_json,
  cpu=256,
  execution_role_arn=auth_task_execution_role.arn,
  family="%s-auth-service" % long_stack,
  # ipc_mode=None,
  memory=512,
  network_mode='awsvpc',
  # pid_mode=None,
  # placement_constraints=None,
  # proxy_configuration=None,
  requires_compatibilities=[ 'FARGATE' ],
  tags=default_tags,
  task_role_arn=auth_task_role.arn,
  # volumes=None,
)

# Create an ECS fargate service based on our task definition and bound to the
# load balancer's target group. When this service starts, our site will be live.
auth_ecs_service = ecs.Service(
  'auth-ecs-service',
  # opts=None,
  # capacity_provider_strategies=None,
  cluster=ecs_cluster.arn,
  #deployment_controller=None,
  #deployment_maximum_percent=None,
  #deployment_minimum_healthy_percent=None,
  desired_count=num_auth_instances,
  enable_ecs_managed_tags=True,
  health_check_grace_period_seconds=20,
  # iam_role=None,
  launch_type='FARGATE',
  load_balancers=[
    dict(
      containerName='auth-app',
      containerPort=5000,
      # elbName=,
      target_group_arn = lb_target_group.arn
    ),
  ],
  name="auth-service-%s" % long_stack,
  network_configuration=dict(
    assignPublicIp=True,
    security_groups=[ auth_task_security_group.id  ],
    subnets=public_subnet_ids,
  ),
  # ordered_placement_strategies=None,
  # placement_constraints=None,
  # platform_version=None,
  # propagate_tags=None,
  # scheduling_strategy=None,
  service_registries=None,
  tags=default_tags,
  task_definition=auth_task_definition.arn,
  wait_for_steady_state=False,
)
pulumi.export('ecs-auth-service', auth_ecs_service.name)

pulumi.export('auth-ecr-hashed-image-url', hashed_auth_image_url)
'''

pulumi.export('vpc-cidr', vpc_cidr)
pulumi.export('azs', azs)
pulumi.export('public_subnet_cidrs', public_subnet_cidrs)
pulumi.export('private_subnet_cidrs', private_subnet_cidrs)
pulumi.export('website_url', 'https://www.%s' % zone_name)

'''
# pulumi.export("auth-task-container-definitions", auth_task_container_definitions)
# pulumi.export("auth-task-definition", auth_task_definition)
'''
