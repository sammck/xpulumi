#!/bin/bash

set -eo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PULUMI="$SCRIPT_DIR/pulumi"
curl "$("$PULUMI" stack -s dev output url)"
