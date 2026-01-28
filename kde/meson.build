# KDE Components subdirectory
# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: GPL-3.0-or-later

# This file is called from the top-level meson.build when kde_components is enabled.
# KDE components are built using CMake because they require:
# - ECM (Extra CMake Modules) for KDE-specific macros
# - Integration with kde-builder tooling
# - KF6 CMake target exports

# For kde-builder integration, users should configure with:
#   meson setup build -Dkde_components=enabled
#
# Or use the CMake build directly for KDE components:
#   cd kde && cmake -B build && cmake --build build

message('KDE components will be built via CMake')
message('To build KDE components, run: cmake -B build -S kde && cmake --build build')
message('Or use kde-builder which handles this automatically')

# Note: Meson doesn't have native CMake subproject support for building,
# but kde-builder handles this by running CMake separately for the kde/ directory.
#
# The meson build installs the Python CLI and data files.
# The CMake build (in kde/) installs:
#   - libkapsule-qt shared library
#   - Header files
#   - CMake config files for find_package(Kapsule)
#   - Future: KIO worker, plasmoid, KCM module
