#! /bin/bash

set -eo pipefail

sudo apt-get update
sudo apt-get install -y git curl python3-venv python3-grpcio python3-dev python3-pip sqlcipher libsqlcipher0 libsqlcipher-dev
sudo apt-get upgrade -y python3-grpcio

export PATH="$HOME/.local/bin:$PATH"
pip3 install --upgrade --user pip
pip install --upgrade --user wheel
pip install grpcio==1.43

if [ ! -e .venv ]; then
python3 -m venv ./.venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install --upgrade wheel
./.venv/bin/pip install grpcio==1.43

echo "Please be patient..." >&2
# We need to use command substitution rather than a simple pipe here because
# The final script needs to read config answers fro stdin
python3 <(curl https://raw.githubusercontent.com/sammck/vpyapp/v0.2.0/vpyapp.py) \
  -v run --update git+https://github.com/sammck/xpulumi-installer.git \
  xpulumi-installer --tb install --package git+https://github.com/sammck/xpulumi.git
