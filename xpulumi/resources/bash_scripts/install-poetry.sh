#!/bin/bash

set -e

MIN_POETRY_VERSION=1.1.13
#MIN_POETRY_VERSION=1.2.0   # this is the preview version which supports dotenv plugin


SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
source "$SCRIPT_DIR/bash-helpers.sh"

function get_poetry_version() {
    local version="$(poetry --version | cut -d' ' -f3 | tr -d ')')" || return $?
    echo "$version"
}


force=""
if [ "$1" == "--force" ]; then
    force=1
fi


function do_install() {
    local version
    if [ "$force" == "1" ]; then
        echo "Forcing reinstall of poetry" >&2
        rm -f ~/.local/bin/poetry || return $?
        rm -fr ~/.local/share/pypoetry || return $?
    else
        if command_exists poetry; then
            version="$(get_poetry_version)" || return $?
            if check_version_ge "$version" "$MIN_POETRY_VERSION"; then
                echo "Poetry version $version is already in PATH and meets the minimum version $MIN_POETRY_VERSION. No update is necessary." >&2
                return 0
            else
                echo "Updating poetry from version $version" >&2
            fi
        else
            echo "Poetry command not found... Installing poetry" >&2
        fi
    fi

    pkglist=()

    add_os_packages_if_missing pkglist curl python3.8 python3.8-venv || return $?
    update_and_install_os_packages "${pkglist[@]}" || return $?

    local preview_option=""
    if [ "$USE_POETRY_PREVIEW" == "1" ]; then
        preview_option="-p"
    fi

    curl -sSL https://install.python-poetry.org | python3 - $preview_option || return $?
    if ! command_exists poetry; then
        echo "The poetry command is still not in PATH after installation; check to make sure" >&2
        echo "$HOME/.local/bin is in your PATH. Add to $HOME/~profile if desired." >&2
        exit 1
    fi

    version="$(get_poetry_version)" || return $?
    if ! check_version_ge "$version" "$MIN_POETRY_VERSION"; then
      echo "The poetry command version $version is still below the minimum version $MIN_POETRY_VERSION after update" >&2
    fi
    local poetry_cmd="$(command -v poetry)" || return $?
    echo "Poetry version $version successfully installed and in PATH at $poetry_cmd" >&2
}

RETCODE=0
do_install || RETCODE=$?
if [[ "$RETCODE" -ne 0 ]]; then
  echo "Poetry installation failed." >&2
  exit 1
fi
