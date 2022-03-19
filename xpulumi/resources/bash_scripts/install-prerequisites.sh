#!/bin/bash

#set -x
set -e

BIN_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_DIR="$( dirname "$BIN_DIR" )"

cd "$PROJECT_DIR"

source "$BIN_DIR/bash-helpers.sh"

pkglist=()

add_os_packages_if_missing pkglist build-essential meson ninja-build python3.8 python3.8-venv sqlcipher

add_os_package_if_command_missing pkglist sha256sum coreutils
add_os_package_if_command_missing pkglist curl
#add_os_package_if_command_missing pkglist python3 python3.8
#add_os_package_if_command_missing pkglist python3.8 python3.8
add_os_package_if_command_missing pkglist git

update_and_install_os_packages "${pkglist[@]}"

"$BIN_DIR/install-docker.sh"
"$BIN_DIR/install-poetry.sh"
"$BIN_DIR/install-aws-cli.sh"
