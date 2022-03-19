# !/usr/bin/env bash

# intended to be included by other scripts

# returns the name of the current shell
current_shell() {
  echo -n "$(ps -cp "$$" -o command="")"
}

SF_SCRIPT_SHELL="$(current_shell)"

# Strips leading and trailing colons, and reduces duplicate interior colons to a single colon each
#
#  $1 = string to strip
strip_colons() {
  local result1="$(echo "$1" | awk '{ gsub(/^:+|:+$/, ""); print }')"
  local result="$(echo "$result1" | awk '{ gsub(/::+/, ":"); print }')"
  echo -n "$result"
}

# Strips trailing slashes from a directory name. Leaves bare "/" alone
#
#  $1 = directory name to strip
strip_trailing_slashes() {
  local result1="$1"
  local result="$result1"
  if [ -n "$result1" ]; then
    result="$(echo "$result1" | awk '{ gsub(/\/+$/, ""); print }')"
    if [ -z "$result" ]; then
      result="/"
    fi
  fi
  echo -n "$result"
}

# Returns true if supplied directory name is in a search path; false otherwise
#  $1 = directory name to check
#  $2 = search path; if not supplied, $PATH is used.
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
dir_is_in_path() {
  local dirname="$(strip_trailing_slashes "$1")"
  local spath="$2"  
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local escaped_dirname="$(echo "$dirname" | sed -e 's/[]\/$*.^[]/\\&/g')"  # if [[ ":$spath:" == *":$dirname"*(/)":"* ]]; then
  if ( echo "$spath" | grep -q ":$escaped_dirname/*:" ); then
    return 0
  else
    return 1
  fi
}

# Removes a directory from a search path if it is there
#  $1 = directory name to remove
#  $2 = search path; if not supplied, $PATH is used
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
pathrm() {
  local spath="$2"
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local dirname="$(strip_trailing_slashes "$1")"
  local escaped_dirname="$(echo "$dirname" | sed -e 's/[]\/$*.^[]/\\&/g')"
  local result1="$(echo ":$spath:" | awk '{ gsub(/':"$escaped_dirname"'\/*:/, ":"); print }')"
  local result="$(strip_colons "$result1")"
  echo -n "$result"
}

# Prepends a directory to a search path, or moves it to the front if it
#    is already there
#  $1 = directory name to prepend to search path
#  $2 = search path; if not supplied, $PATH is used. 
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
path_prepend() {
  local spath="$2"
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local dirname="$(strip_trailing_slashes "$1")"
  local preresult="$dirname:$(pathrm "$dirname" "$spath" 1)"
  local result="$(strip_colons "$preresult")"
  echo -n "$result"
}

# Prepends a directory to a search path if it is not already in the search path
#  $1 = directory name to prepend to search path
#  $2 = search path; if not supplied, $PATH is used
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
path_prepend_if_missing() {
  local spath="$2"
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local dirname="$(strip_trailing_slashes "$1")"
  local result="$spath"
  if ! dir_is_in_path "$dirname" "$spath" 1; then
    result="$(strip_colons "$dirname:$spath")"
  fi
  echo -n "$result"
}

# Appends a directory to the end of a search path, or moves it to the end if it
#    is already there
#  $1 = directory name to append to search path
#  $2 = search path; if not supplied, $PATH is used
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
path_force_append() {
  local spath="$2"
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local dirname="$(strip_trailing_slashes "$1")"
  local result="$(pathrm "$dirname" "$spath" 1):$dirname"
  result="$(strip_colons "$result")"
  echo -n "$result"
}

# Appends a directory to the end of a search path if it is not already in the search path
#  $1 = directory name to append to search path
#  $2 = search path; if not supplied, $PATH is used
#  $3 = 1 if empy string should be interpreted literally rather than using $PATH; empty otherwise
path_append() {
  local spath="$2"
  if [ -z "$3$spath" ]; then
    spath="$PATH"
  fi
  local dirname="$(strip_trailing_slashes "$1")"
  local result="$spath"
  if ! dir_is_in_path "$dirname" "$spath" 1; then
    result="$(strip_colons "$spath:$dirname")"
  fi
  echo -n "$result"
}

# Prepends a directory to global exported $PATH, or moves it to the front if it
#    is already there
#  $1 = directory name to prepend to $PATH
xpath_prepend() {
  export PATH="$(path_prepend "$1")"
}

# Prepends a directory to global exported $PATH if it is not already in $PATH
#  $1 = directory name to prepend to $PATH
xpath_prepend_if_missing() {
  export PATH="$(path_prepend_if_missing "$1")"
}

# Appends a directory to global exported $PATH, or moves it to the end if it
#    is already there
#  $1 = directory name to append to $PATH
xpath_force_append() {
  export PATH="$(path_force_append "$1")"
}

# Appends a directory to global exported $PATH if it is not already in $PATH
#  $1 = directory name to append to $PATH
xpath_append() {
  export PATH="$(path_append "$1")"
}

unset SF_SCRIPT_SHELL

