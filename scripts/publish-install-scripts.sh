#!/bin/bash

set -eo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"
S3DIR="s3://public.mckelvie.org"

aws s3 cp ./xpulumi-init-env-alpha.sh "$S3DIR/xpulumi-init-env-alpha.sh"
