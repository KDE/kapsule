# Kapsule - Incus-based Distrobox Alternative
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2024-2026 KDE Community

project('kapsule',
    version: '0.1.0',
    license: 'GPL-3.0-or-later',
    meson_version: '>= 1.0.0',
    default_options: [
        'warning_level=2',
    ],
)

# ============================================================================
# Project metadata
# ============================================================================

project_description = 'Incus-based container management with KDE integration'

# ============================================================================
# Find Python
# ============================================================================

pymod = import('python')

python_opt = get_option('python')
if python_opt != ''
    py = pymod.find_installation(python_opt, pure: true)
else
    py = pymod.find_installation('python3', pure: true)
endif

py_version = py.language_version()
if not py_version.version_compare('>= 3.11')
    error('Python >= 3.11 required, found ' + py_version)
endif

message('Found Python ' + py_version + ' at ' + py.full_path())

# ============================================================================
# Install Python package
# ============================================================================

# Install the Python package using pip in editable mode during development,
# or properly install for distribution builds.
# For meson, we use install_sources for proper installation.

python_sources = files(
    'src/kapsule/__init__.py',
    'src/kapsule/cli/__init__.py',
    'src/kapsule/cli/main.py',
)

# Install Python sources
py.install_sources(
    'src/kapsule/__init__.py',
    subdir: 'kapsule',
)

py.install_sources(
    'src/kapsule/cli/__init__.py',
    'src/kapsule/cli/main.py',
    subdir: 'kapsule/cli',
)

# ============================================================================
# Install CLI script
# ============================================================================

# Create a wrapper script for the CLI
# This uses the Python module's console_scripts entry point
configure_file(
    input: 'scripts/kapsule.in',
    output: 'kapsule',
    configuration: {
        'PYTHON': py.full_path(),
    },
    install: true,
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
)

# Also install as 'kap' alias
configure_file(
    input: 'scripts/kapsule.in',
    output: 'kap',
    configuration: {
        'PYTHON': py.full_path(),
    },
    install: true,
    install_dir: get_option('bindir'),
    install_mode: 'rwxr-xr-x',
)

# ============================================================================
# Data files
# ============================================================================

# D-Bus service files
if get_option('install_dbus')
    dbus_system_services_dir = get_option('dbus_system_services_dir')
    if dbus_system_services_dir == ''
        dbus_system_services_dir = get_option('datadir') / 'dbus-1/system-services'
    endif

    # Install D-Bus service file (when we have it)
    # install_data(
    #     'data/dbus/org.kde.kapsule.service',
    #     install_dir: dbus_system_services_dir,
    # )
endif

# Systemd service files
if get_option('install_systemd')
    systemd_dep = dependency('systemd', required: false)

    systemd_system_unit_dir = get_option('systemd_system_unit_dir')
    if systemd_system_unit_dir == ''
        if systemd_dep.found()
            systemd_system_unit_dir = systemd_dep.get_variable(pkgconfig: 'systemdsystemunitdir')
        else
            systemd_system_unit_dir = get_option('prefix') / 'lib/systemd/system'
        endif
    endif

    # Install systemd service files (when we have them)
    # install_data(
    #     'data/systemd/kapsule-daemon.service',
    #     install_dir: systemd_system_unit_dir,
    # )
endif

# ============================================================================
# KDE Components (optional - built with CMake)
# ============================================================================

kde_components_opt = get_option('kde_components')

# Check for Qt6 and KF6 dependencies
qt6_dep = dependency('Qt6Core', required: kde_components_opt)
kf6_coreaddons_dep = dependency('KF6CoreAddons', required: kde_components_opt)

build_kde_components = qt6_dep.found() and kf6_coreaddons_dep.found()

if build_kde_components
    message('Building KDE components (libkapsule-qt, plasmoid, KIO worker, KCM)')

    # KDE components are built using CMake via subproject
    # This integrates with kde-builder's CMake workflow
    subdir('kde')
else
    if kde_components_opt.enabled()
        error('KDE components requested but Qt6/KF6 not found')
    else
        message('KDE components disabled (Qt6/KF6 not found or disabled)')
    endif
endif

# ============================================================================
# Summary
# ============================================================================

summary({
    'Python': py.full_path(),
    'Python version': py_version,
}, section: 'Python')

summary({
    'KDE components': build_kde_components,
}, section: 'Features')

summary({
    'prefix': get_option('prefix'),
    'bindir': get_option('prefix') / get_option('bindir'),
    'datadir': get_option('prefix') / get_option('datadir'),
}, section: 'Directories')
