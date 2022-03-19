#!/bin/bash

# Installs the github commandline tool gh

set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/bash-helpers.sh"

min_gh_version="2.5.0"

force=""
if [ "$1" == "--force" ]; then
  force=1
fi



function do_install() {
    update_gpg_keyring https://cli.github.com/packages/githubcli-archive-keyring.gpg "/etc/apt/trusted.gpg.d/githubcli-archive-keyring.gpg" || return $?
    update_apt_sources_list "/etc/apt/sources.list.d/github-cli.list" "/etc/apt/trusted.gpg.d/githubcli-archive-keyring.gpg" \
            "https://cli.github.com/packages" stable main || return $?
    

    pkglist=()
    if [ "$force" == "1" ]; then
        add_os_packages pkglist gh || return $?
    else
        add_os_package_if_outdated pkglist gh "$min_gh_version" || return $?
    fi
    update_and_upgrade_os_packages "${pkglist[@]}" || return $?

    if ! command_exists gh; then
        echo "ERROR: 'gh' command still not found in PATH after install/upgrade." >&2
        return 1
    fi
}

RETCODE=0
do_install || RETCODE=$?
if [[ "$RETCODE" -ne 0 ]]; then
  echo "gh installation failed." >&2
  exit 1
fi

echo "gh installation successful!" >&2
