#!/bin/bash

set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/bash-helpers.sh"

TMP_DIR="$(get_tmp_dir)"

cd "$TMP_DIR"
rm -fr aws-cli-install
mkdir aws-cli-install
cd aws-cli-install

ARCH="$(get_current_architecture)"
OPTIONS=""

if [ -e "$HOME/.local/aws-cli" ]; then
  echo "Attempting to update aws-cli" >&2
  OPTIONS="--update"
else
  echo "Installing aws-cli" >&2
fi

# TODO: make work for non-linux
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-$ARCH.zip" -o "./awscliv2.zip"
unzip -q ./awscliv2.zip
rm ./awscliv2.zip
mkdir -p "$HOME/.local"
./aws/install -i "$HOME/.local/aws-cli" -b "$HOME/.local/bin" $OPTIONS
cd ..
rm -fr ./aws-cli-install

AWS_CMD="$(command -v aws || true)"
if [ -z "$AWS_CMD" ]; then
    echo "The aws command is still not in PATH after installation; check to make sure" >&2
    echo "$HOME/.local/bin is in your PATH. Add to $HOME/~profile if desired." >&2
    exit 1
fi

if [ "$AWS_CMD" != "$HOME/.local/bin/aws" ]; then
    echo "The aws command in PATH is at $AWS_CMD. That is not the most recently installed" >&2
    echo "version. Check to make sure $HOME/.local/bin is in your PATH. Add to" >&2
    echo "$HOME/~profile if desired." >&2
    exit 1
fi

AWS_VERSION_STR="$($AWS_CMD --version)"
echo "Successfully installed $AWS_VERSION_STR" >&2
