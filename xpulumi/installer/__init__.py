#!/usr/bin/env python3
#
# Copyright (c) 2022 Samuel J. McKelvie
#
# MIT License - See LICENSE file accompanying this package.
#

"""Tools to assist with installation/provisioning of the dev environment"""

from .os_packages import (CalledProcessErrorWithStderrMessage, PackageList,
                          check_version_ge, chown_root, command_exists,
                          create_os_group, download_url_file, file_contents,
                          files_are_identical, find_command_in_path,
                          get_all_os_groups, get_current_architecture,
                          get_current_os_user, get_dpkg_arch,
                          get_file_hash_hex, get_gid_of_group,
                          get_linux_distro_name,
                          get_os_groups_of_current_process,
                          get_os_groups_of_user, get_os_package_version,
                          get_tmp_dir, install_apt_sources_list_if_missing,
                          install_gpg_keyring_if_missing, install_os_packages,
                          invalidate_os_package_list, os_group_exists,
                          os_group_includes_current_process,
                          os_group_includes_user, os_groupadd_user,
                          os_package_is_installed, running_as_root, run_once,
                          searchpath_append, searchpath_contains_dir,
                          searchpath_force_append, searchpath_join,
                          searchpath_normalize, searchpath_parts_append,
                          searchpath_parts_contains_dir,
                          searchpath_parts_force_append,
                          searchpath_parts_prepend,
                          searchpath_parts_prepend_if_missing,
                          searchpath_parts_remove_dir, searchpath_prepend,
                          searchpath_prepend_if_missing, searchpath_remove_dir,
                          searchpath_split, should_run_with_group, sudo_call,
                          sudo_check_call, sudo_check_output,
                          sudo_check_output_stderr_exception, sudo_Popen,
                          uninstall_os_packages, unix_mv,
                          update_and_install_os_packages,
                          update_and_upgrade_os_packages,
                          update_apt_sources_list, update_gpg_keyring,
                          update_os_package_list, upgrade_os_packages)

from .pulumi import (default_pulumi_dir, get_installed_pulumi_dir, get_pulumi,
                     get_pulumi_cmd_version, get_pulumi_dir_in_path,
                     get_pulumi_in_path, get_pulumi_latest_version,
                     get_pulumi_username, get_short_pulumi_cmd, install_pulumi,
                     pulumi_is_installed)
