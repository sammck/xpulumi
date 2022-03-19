#!/bin/bash

# This file is meant to be sourced within other scripts.

function get_tmp_dir() {
    local tmp_dir="$XDG_RUNTIME_DIR"
    if [ -z "$tmp_dir" ]; then
      tmp_dir="/tmp"
    fi
    echo "$tmp_dir"
}

# splits a version string in the form [<epoch>[<non-numeric>]:]<major>[<non-numeric>][.<minor>[<non-numeric>][.<subminor>][<tail>]
# Non-numeric data is discarded.
# Missing fields are set to 0.
# writes the result as a space-delimited string:
#     <epoch> <major> <minor> <subminor> <tail>
function get_split_version() {
    local version="$1"; shift

    local _epoch_major="$(awk -F. '{print $1}' <<< "$version" | sed -n 's/^\([0-9]\+\(:[0-9]\+\)\?\).*$/\1/p')"
    local _epoch="$(awk -F':' '{print $1}' <<< "$_epoch_major")"
    local _major="$(awk -F':' '{print $2}' <<< "$_epoch_major")"
    if [ -z "$_major" ]; then
      _major="$_epoch"
      _epoch=""
    fi
    local _minor="$(awk -F. '{print $2}' <<< "$version" | sed -n 's/^\([0-9]\+\).*$/\1/p')"
    local _subminor="$(awk -F. '{print $3}' <<< "$version" | sed -n 's/^\([0-9]\+\).*$/\1/p')"
    local _tail="$(sed -n 's/^\([0-9]\+:\)\?\([a-zA-Z0-9]\+\.\)\?\([a-zA-Z0-9]\+\.\)\?\([a-zA-Z]*[0-9]\+\)\(.*\)$/\5/p' <<< "$version")"

    # echo "pre_validate=$epoch:$_major.$_minor.$_subminor"

    if [ -z "$_epoch" ]; then
      _epoch=0
    fi
    if [ -z "$_major" ]; then
      _major=0
    fi
    if [ -z "$_minor" ]; then
      _minor=0
    fi
    if [ -z "$_subminor" ]; then
      _subminor=0
    fi
    _tail="$(printf '%q' "$_tail")"
    echo "$_epoch $_major $_minor $_subminor $_tail"
    # return 0
}

# splits a version string in the form [<epoch>[<non-numeric>]:]<major>[<non-numeric>][.<minor>[<non-numeric>][.<subminor>][<tail>]
# Non-numeric data is discarded.
# Puts results into provided named variables.
function split_version() {
    local version="$1"; shift
    local major_name="$1"; shift
    local minor_name="$1"
    local subminor_name="$2"
    local tail_name="$3"
    local epoch_name="$4"

    local split_str="$(get_split_version "$version")" || return $?
    local split_arr=( $split_str )
    if [ -n "$major_name" ]; then
        eval "$major_name='${split_arr[1]}'"
    fi
    if [ -n "$minor_name" ]; then
        eval "$minor_name='${split_arr[2]}'"
    fi
    if [ -n "$subminor_name" ]; then
      eval "$subminor_name='${split_arr[3]}'"
    fi
    if [ -n "$tail_name" ]; then
      eval "$tail_name='${split_arr[4]}'"
    fi
    if [ -n "$epoch_name" ]; then
      eval "$epoch_name='${split_arr[0]}'"
    fi
    # return 0
}

function check_version_ge() {
    local version1="$1"; shift
    local version2="$1"; shift
    local v1_major
    local v1_minor
    local v1_subminor
    local v1_tail
    local v1_epoch
    local v2_major
    local v2_minor
    local v2_subminor
    local v2_tail
    local v2_epoch

    split_version "$version1" v1_major v1_minor v1_subminor v1_tail v1_epoch || return 2
    split_version "$version2" v2_major v2_minor v2_subminor v2_tail v2_epoch || return 2

    # echo "v1=$v1_epoch:$v1_major.$v1_minor.$v1_subminor($v1_tail)"
    # echo "v2=$v2_epoch:$v2_major.$v2_minor.$v2_subminor($v2_tail)"

    # We only compare epochs if both versions provided an epoch number > 0
    if [[ "$v1_epoch" -gt 0 ]]; then
        if [[ "$v2_epoch" -gt 0 ]]; then
            if [[ "$v1_epoch" -lt "$v2_epoch" ]]; then
                return 1
            fi
            if [[ "$v1_epoch" -gt "$v2_epoch" ]]; then
                return 0
            fi
        fi
    fi

    if [[ "$v1_major" -lt "$v2_major" ]]; then
        return 1
    fi
    if [[ "$v1_major" -gt "$v2_major" ]]; then
        return 0
    fi
    if [[ "$v1_minor" -lt "$v2_minor" ]]; then
        return 1
    fi
    if [[ "$v1_minor" -gt "$v2_minor" ]]; then
        return 0
    fi
    if [[ "$v1_subminor" -lt "$v2_subminor" ]]; then
        return 1
    fi
    if [[ "$v1_subminor" -gt "$v2_subminor" ]]; then
        return 0
    fi

    # versions are equal
    return 0
}

function get_current_architecture {
    local result="$(uname -m 2>/dev/null)"
    if [ -z "$result" ]; then
      return 1
    fi
    echo "$result"
    # return $?
}

function get_gid_of_group() {
    local group="$1"; shift
    local result="$(cut -d: -f3 < <(getent group "$group"))"
    if [ -z "$result" ]; then
      return 1
    fi
    echo "$result"
    # return $?
}

# gets the major version number of currently running bash.
# Returns version 0, and an error, if not running in bash.
function get_bash_major_version() {
    if [ -z "$BASH_VERSION" ]; then
      echo "0"
      return 1
    fi
    major="$(cut -d. -f1 <<< "$BASH_VERSION")"
    if ! [[ "$major" =~ ^[0-9]+$ ]]; then
      echo "0"
      return 1
    fi
    echo "$major"
    # return 0
}

bash_major_version="$(get_bash_major_version || true)"
declare_n_supported=
if [[ "$bash_major_version" -ge 5 ]]; then
  declare_n_supported=1
fi

function get_file_hash() {
    local filename="$1"; shift

    if ! command_exists sha256sum; then
      echo "sha256sum utility is required; please apt-get install coreutils" >&2
      return 1
    fi

    if ! [ -e  "$filename" ]; then
      echo "get_file_hash: file does not exist: $filename" >&2
      return 1
    fi

    local file_hash="$(sha256sum -b "$filename" | cut -d' ' -f1)"

    if [ -z "$file_hash" ]; then
      echo "get_file_hash: sha256sum failed: $filename" >&2
      return 1
    fi
}

function files_are_identical() {
    local file1="$1"; shift
    local file2="$1"; shift

    cmp "$file1" "$file2" >/dev/null
    return $?
}

os_package_metadata_stale=1

# Ensures the next call to update_os_package_list will perform an update
# useful after adding a source repository, etc.
function invalidate_os_package_list() {
    os_package_metadata_stale=1
}

function update_gpg_keyring() {
    local url="$1"; shift
    local dest_file="$1"; shift
    local filter="$1"

    if [ -z "$filter" ]; then
        filter="cat"
    fi

    local tmp_file="$(get_tmp_dir)/tmp_gpg_keyring"
    curl -fsSL "$url" > "$tmp_file.pub" || return $?
    # echo "filter is '$filter'" >&2
    $filter <"$tmp_file.pub" >"$tmp_file.gpg" || return $?
    if [ -e "$dest_file" ]; then
        if files_are_identical "$tmp_file.gpg" "$dest_file"; then
            return 0
        fi
    fi
    echo "Updating GPG keyring at $dest_file (sudo required)" >&2
    sudo chmod 644 "$tmp_file.gpg" || return $?
    invalidate_os_package_list
    sudo mv "$tmp_file.gpg" "$dest_file" || return $?
    return 0
}

function install_gpg_keyring_if_missing() {
    local url="$1"; shift
    local dest_file="$1"; shift
    local filter="$1"

    if [ -e "$dest_file" ]; then
      return 0
    fi

    update_gpg_keyring "$url" "$dest_file" "$filter"
    return $?
}

function get_distro_name() {
  lsb_release -cs || return $?
}

function update_apt_sources_list() {
    local dest_file="$1"; shift
    local signed_by="$1"; shift
    local url="$1"; shift
    # remaining args are added to end of constructed line, in order
    local arch="$(dpkg --print-architecture)"

    local tmp_file="$(get_tmp_dir)/tmp_apt_source.list"
    echo "deb [arch=$arch signed-by=$signed_by] $url" "$@" > "$tmp_file"
    if [ -e "$dest_file" ]; then
        if files_are_identical "$tmp_file" "$dest_file"; then
            return 0
        fi
        echo "Updating apt-get sources list for $dest_file (requires sudo)" >&2
        echo "Old: $(cat "$dest_file")" >&2
    else
        echo "Creating apt-get sources list for $dest_file (requires sudo)" >&2
    fi
    echo "New: $(cat "$tmp_file")" >&2
    sudo chmod 644 "$tmp_file" || return $?
    invalidate_os_package_list
    sudo mv "$tmp_file" "$dest_file" || return $?
    update_os_package_list
    return 0
}

function install_apt_sources_list_if_missing() {
    local dest_file="$1"; shift
    local signed_by="$1"; shift
    local url="$1"; shift
    # remaining args are added to end of constructed line, in order

    if [ -e "$dest_file "]; then
      return 0
    fi

    update_apt_sources_list "$dest_file" "$signed_by" "$url" "$@"
    return $?
}


# Performs "sudo apt-get update" if it has not already been done since last invalidated
#  Optional "--force" args will force update even if not stale.
function update_os_package_list() {
    # TODO support macos and yum-based OS's
    local force="$1"
    if [ "$force" == "--force" ]; then
      os_package_metadata_stale=1
    fi

    if [ "$os_package_metadata_stale" == "1" ]; then
        echo "Updating apt-get package metadata (sudo required)" >&2
        sudo apt-get update || return $?
        os_package_metadata_stale=0
    fi
    # return $?
}

# gets the version string for an installed OS package
# if the package is not installed, returns an empty string with a nonzero exit code.
function get_os_package_version() {
    # TODO support macos
    local package_name="$1"; shift

    PKG_VERSION="$(dpkg-query -W -f='${Version}\n' "$package_name" 2>/dev/null)"
    if [ -z "$PKG_VERSION" ]; then
      return 1
    fi
    echo "$PKG_VERSION"
    return 0
}

# Returns true (0 return code) if the specified OS package is installed, or false (1 exit code) otherwise.
function os_package_is_installed() {
    local package_name="$1"; shift
    get_os_package_version "$package_name" >/dev/null
    return $?
}

# Uninstalls one or more os packages without updating the registry first.
# avoids sudo if there is nothing to uninstall
function uninstall_os_packages() {
    # TODO support macos
    # install or upgrade the listed packages
    local filtered=()
    local package_name
    for package_name; do
      if os_package_is_installed "$package_name"; then
        filtered+=( "$package_name" )
      fi
    done

    if [[ ${#filtered[@]} -eq 0 ]]; then
      return 0
    fi

    echo "Uninstalling OS packages (sudo required): " "${filtered[@]}" >&2
    sudo apt-get install -y "${filtered[@]}"
    # return $?
}

# Installs one or more os packages without updating the registry first, and without upgrading.
function install_os_packages() {
    # TODO support macos
    # install or upgrade the listed packages
    if [[ $# -eq 0 ]]; then
      return 0
    fi
    echo "Installing OS packages (sudo required): " "$@" >&2
    sudo apt-get install -y "$@"
    # return $?
}

# Installs one or more os packages without updating the registry first, if they are not installed with any version.
function install_os_packages_if_missing() {
    # TODO support macos
    # install or upgrade the listed packages
    local filtered=()
    local package_name
    for package_name; do
      if ! os_package_is_installed "$package_name"; then
        filtered+=( "$package_name" )
      fi
    done

    if [[ ${#filtered[@]} -eq 0 ]]; then
      return 0
    fi

    install_os_packages "${filtered[@]}"
}

# Installs one or more os packages, updating the registry first if it
# has not already been updated.
function update_and_install_os_packages() {
    # update registry, then install or upgrade the listed packages
    if [[ $# -eq 0 ]]; then
      return 0
    fi
    update_os_package_list || return $?
    install_os_packages "$@" || return $?
    # return 0
}

# Installs one or more os packages only if they are not installed, updating the registry first if it
# has not already been updated.
function update_and_install_os_packages_if_missing() {
    # update registry, then install or upgrade the listed packages
    local filtered=()
    local package_name
    for package_name; do
      if ! os_package_is_installed "$package_name"; then
        filtered+=( "$package_name" )
      fi
    done
    update_and_install_os_packages "${filtered[@]}" || return $?
    # return 0
}

# Installs/upgrades one or more os packages without updating the registry first.
function upgrade_os_packages() {
    # TODO support macos
    # install or upgrade the listed packages
    if [[ $# -eq 0 ]]; then
      return 0
    fi
    echo "Installing/Upgrading OS packages (sudo required): " "$@" >&2
    sudo apt-get install --upgrade -y "$@"
    # return $?
}

# Installs/upgrades one or more os packages, updating the registry first if it
# has not already been updated.
function update_and_upgrade_os_packages() {
    # update registry, then install or upgrade the listed packages
    if [[ $# -eq 0 ]]; then
      return 0
    fi
    update_os_package_list || return $?
    upgrade_os_packages "$@"
    # return $?
}

# Returns true (return code 0) if a command exists in the PATH, or false (return code 1) otherwise.
function command_exists() {
    local cmd_name="$1"; shift
    command -v "$cmd_name" >/dev/null 2>&1
    # return $?
}

# Check if a bash array contains a value
# The first argument is the value to search for. The remaining arguments are the
#  array items; e.g.,
#
#     ar=("the" "quick" "brown" "fox")
#     contains_value "fox" "${ar[@]}" || echo "There is no fox"
function contains_value() {
    local value="$1"; shift
    local element
    for element; do
      if [ "$element" == "$value" ]; then
        return 0
      fi
    done
    return 1
}

# Adds values to a named bash array if they are not already present.
# 
#  Example:
#    arr=()
#    add_value_to_set arr "quick"
#    add_value_to_set arr "brown"
#    add_value_to_set arr "fox"
#    add_value_to_set arr "brown"   # this one will be ignored
#    echo "${arr[@]}"
function add_to_set() {
    local array_name="$1"; shift
    local tmp="$array_name[@]"  # hacky bash way to expand an array by reference
    local element
    local qelement
    for element; do
      if ! contains_value "$element" "${!tmp}"; then
        qelement="$(printf "%q" "$element")"
        # echo "Evaluating $array_name+=( $qelement )" >&2
        eval "$array_name+=( $qelement )"   # ugly but seems only portable way to append to an array by reference
      fi
    done
    # return 0
}

# Incrementally build an array of os package names to be installed.
# duplicate package names are dropped.
#
#  Example:
#    missing_packages=()
#    add_os_packages missing_packages curl python3
#    add_os_packages missing_packages jq
#    add_os_packages missing_packages curl  # this is dropped
#    update_and_install_os_packages "${missing_packages[@]}"
#
function add_os_packages() {
    add_to_set "$@"
}

# Incrementally build an array of os package names to be installed.
# duplicate package names are dropped.
#
#  Example:
#    missing_packages=()
#    add_os_packages missing_packages curl python3
#    add_os_packages missing_packages jq
#    add_os_packages missing_packages curl  # this is dropped
#    update_and_install_os_packages "${missing_packages[@]}"
#
function add_os_packages_if_missing() {
    local array_name="$1"; shift
    local filtered=()
    local package_name
    for package_name; do
      if ! os_package_is_installed "$package_name"; then
        filtered+=( "$package_name" )
      fi
    done

    add_to_set "$array_name" "${filtered[@]}"
}

# Incrementally build an array of package names for missing commands.
# duplicate packages are dropped.
#
#  Example:
#    missing_packages=()
#    add_os_package_if_command_missing missing_packages curl curl
#    add_os_package_if_command_missing missing_packages jq
#    update_and_install_os_packages "${missing_packages[@]}"
#    
function add_os_package_if_command_missing() {
    local package_array_name="$1"; shift
    local cmd_name="$1"; shift
    local package_name="$1"  # If omitted, the command name will be used as the package name
    local tmp="$package_array_name[@]"  # hacky bash way to expand an array by reference

    if [ -z "$package_name" ]; then
      package_name="$cmd_name"
    fi

    if ! contains_value "$package_name" "${!tmp}"; then
        if ! command_exists "$cmd_name"; then
            echo "Command '$cmd_name not found in PATH; will install OS package '$PACKAGE_NAME'" >&2
            add_os_packages "$package_array_name" "$package_name"
            return $?
        fi
    fi

    # return 0
}

# Incrementally build an array of package names for missing commands.
# duplicate packages are dropped.
#
#  Example:
#    missing_packages=()
#    add_os_package_if_outdated docker 20.0.0
#    update_and_upgrade_os_packages "${missing_packages[@]}"
#    
function add_os_package_if_outdated() {
    local package_array_name="$1"; shift
    local package_name="$1"; shift
    local min_version="$1"; shift

    local version="$(get_os_package_version "$package_name" 2>/dev/null || true)"
    if [ -n "$version" ]; then
      if check_version_ge "$version" "$min_version"; then
        return 0
      fi
    fi

    add_os_packages "$package_array_name" "$package_name"
}

# Install an os package if a required command is missing
function install_os_command() {
    local cmd_name="$1"; shift
    local package_name="$1"   # If omitted, the command name will be used as the package name

    if [ -z "$package_name" ]; then
      package_name="$cmd_name"
    fi

    if ! command_exists "$cmd_name"; then
        echo "Command '$cmd_name not found in PATH; installing OS package '$PACKAGE_NAME'" >&2
        install_os_packages "$package_name" || return $?
        if ! command_exists "$cmd_name"; then
          echo "Command '$cmd_name' still not in PATH after installing package '$PACKAGE_NAME'" >&2
          return 1
        fi
    fi

    return 0
}

function create_os_group() {
  local group="$1"; shift
  local gid="$(get_gid_of_group "$group" || true)"
  if [ -z "$gid" ]; then
    echo "Creating OS group '$group' (requires sudo)" >&2
    sudo groupadd "$group" || return $?
  fi
  return 0
}

function get_current_os_user() {
  echo "$USER"
}

function get_all_os_groups() {
  local group_list=()
  readarray -t group_list < <(cut -d: -f1 /etc/group)
  if [[ ${#group_list[@]} -eq 0 ]]; then
    echo "Unable to fetch full os group list" >&2
    return 1
  fi
  echo "${group_list[@]}"
  # return $?
}

function os_group_exists() {
  local group="$1"; shift

  local gid="$(get_gid_of_group "$group" 2>/dev/null || true)"
  if [ -z "$gid" ]; then
      return 1
  fi
  return 0
}

function get_os_groups_of_user() {
  local user="$1"
  if [ -z "$user" ]; then
    id -nG || return $?
  else
    id -nG "$user" || return $?
  fi
}

function get_os_groups_of_current_process() {
  local psout
  local groups
  if psout="$(ps -o group,supgrp $$)"; then
    local psline="$(echo "$psout" | tail -1 | tr -s ' ')"
    local main_group="$(echo "$psline" | cut -d' ' -f1)"
    if [ -z "$main_group" ]; then
      echo "Malformed effective group list for current process" >&2
      return 1
    fi
    local add_groups="$(echo "$psline" | cut -d' ' -f2 | cut -d',' --output-delimiter=' ' -f1-)"
    if [ -z "$add_groups" ]; then
      groups="$main_group"
    else
      groups="$main_group $add_groups"
    fi
  else
    local ec=$?
    echo "Could not fetch effective group list for current process" >&2
    return $ec
  fi
  echo "$groups"
  return 0
}

function os_group_includes_user() {
  local group="$1"; shift
  local user="$1"

  if [ -z "$user" ]; then
    user="$(get_current_os_user)"
  fi

  local groups="$(get_os_groups_of_user "$user" 2>/dev/null)"
  if [ -z "groups" ]; then
      return 1
  fi
  contains_value "$group" $groups || return $?
  # return 0
}

function os_group_includes_current_process() {
  local group="$1"; shift
  local groups="$(get_os_groups_of_current_process)" || return $?
  if [ -z "groups" ]; then
      return 1
  fi
  contains_value "$group" $groups || return $?
  return 0
}

function os_group_add_user() {
  local group="$1"; shift
  local user="$1"

  if [ -z "$user" ]; then
    user="$(get_current_os_user)"
  fi

  local groups="$(get_os_groups_of_user "$user")"
  if [ -z "groups" ]; then
      echo "Unable to fetch group list of user $user" >&2
      return 1
  fi
  if contains_value "$group" $groups; then
    # user already in group
    return 0
  fi

  if ! os_group_exists "$group"; then
      echo "OS group $group does not exist; cannot add user $user" >&2
      return 1
  fi

  echo "Adding user $user to OS group $group (requires sudo)" >&2
  sudo usermod -a -G "$group" "$user" || return $?

  # return 0
}

# Turn an argument list into a single escaped string suitable for passing to
# "bash -c".
function escape_arglist() {
  printf "%q " "$@"
}

# returns 0 if run_with_group will help, 1 if run_with_group is not necessary, 2 if user is not in the group,
# and 3 for other errors.
function should_run_with_group() {
  local group="$1"; shift
  local user="$(get_current_os_user)" || return 3
  if os_group_includes_current_process "$group"; then
    # process already in group
    return 1
  else
    if os_group_includes_user "$group" "$user"; then
      # user is in group but process is not
      return 0
    else
      # user is not in group
      return 2
    fi
  fi
  return 0
}

function run_with_group() {
    local group="$1"; shift
    local user="$(get_current_os_user)" || return 1
    local should_wrap=0
    should_run_with_group "$group" || should_wrap=$?

    if [[ "$should_wrap" -eq 1 ]]; then
        # already in group; just run the command
        "$@" || return $?
    else
        if [[ "$should_wrap" -eq 0 ]]; then
            echo "NOTE: Command '$1' requires membership in group $group, which is newly added for user $user, and is" >&2
            echo "not effective for the current process. Requires sudo until login session is restarted..." >&2
            sudo -E -u "$user" "$@" || return $?
        else
            if [[ "$should_wrap" -eq 2 ]]; then
                echo "Command '$1' requires membership in group $group, which user $user is not in" >&2
                return 1
            else
                echo "Unable to determine if user/process are in group $group" >&2
                return 1
            fi
        fi
    fi
    return 0
}