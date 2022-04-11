#! /bin/bash

set -eo pipefail

echo "Please be patient..." >&2
curl https://raw.githubusercontent.com/sammck/vpyapp/v0.2.0/vpyapp.py | python3 - -v run --update git+https://github.com/sammck/xpulumi.git xpulumi --tb init-env
