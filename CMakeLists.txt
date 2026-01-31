# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# Kapsule - Incus-based Distrobox Alternative
# Unified CMake build for Python CLI and KDE components

cmake_minimum_required(VERSION 3.27)

project(Kapsule
    VERSION 0.1.0
    DESCRIPTION "Incus-based container management with KDE integration"
    LANGUAGES CXX
)

# ============================================================================
# Options
# ============================================================================

option(BUILD_KDE_COMPONENTS "Build KDE components (libkapsule-qt, plasmoid, KIO, KCM)" ON)
option(INSTALL_PYTHON_CLI "Install Python CLI and vendored dependencies" ON)
option(VENDOR_PYTHON_DEPS "Download and vendor Python dependencies" ON)

# ============================================================================
# ECM Setup
# ============================================================================

find_package(ECM 6.0.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH} ${CMAKE_SOURCE_DIR}/cmake)

include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)
include(ECMSetupVersion)
include(ECMGenerateHeaders)
include(ECMGenerateExportHeader)
include(ECMQtDeclareLoggingCategory)
include(ECMGeneratePkgConfigFile)
include(FeatureSummary)

# ============================================================================
# Version
# ============================================================================

set(KAPSULE_VERSION ${PROJECT_VERSION})

ecm_setup_version(${KAPSULE_VERSION}
    VARIABLE_PREFIX KAPSULE
    VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/kapsule_version.h"
    PACKAGE_VERSION_FILE "${CMAKE_CURRENT_BINARY_DIR}/KapsuleConfigVersion.cmake"
    SOVERSION 0
)

# ============================================================================
# Find Python
# ============================================================================

if(INSTALL_PYTHON_CLI)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    message(STATUS "Found Python ${Python3_VERSION} at ${Python3_EXECUTABLE}")

    # Define installation directories for Python components
    set(KAPSULE_PYTHON_DIR "${KDE_INSTALL_DATADIR}/kapsule/python")
    set(KAPSULE_VENDOR_DIR "${KDE_INSTALL_DATADIR}/kapsule/vendor")
endif()

# ============================================================================
# Python CLI Installation
# ============================================================================

if(INSTALL_PYTHON_CLI)
    # Install kapsule CLI package
    install(FILES
        src/cli/__init__.py
        src/cli/async_typer.py
        src/cli/config.py
        src/cli/decorators.py
        src/cli/incus_client.py
        src/cli/main.py
        src/cli/models_generated.py
        src/cli/output.py
        src/cli/profile.py
        DESTINATION "${KAPSULE_PYTHON_DIR}/kapsule"
    )

    # Install kapsule daemon package
    install(FILES
        src/daemon/__init__.py
        src/daemon/__main__.py
        src/daemon/incus_client.py
        src/daemon/models_generated.py
        src/daemon/service.py
        DESTINATION "${KAPSULE_PYTHON_DIR}/kapsule/daemon"
    )

    # Configure and install launcher scripts
    set(PYTHON_EXECUTABLE "${Python3_EXECUTABLE}")

    # CLI launcher
    configure_file(
        scripts/kapsule.in
        "${CMAKE_CURRENT_BINARY_DIR}/kapsule"
        @ONLY
    )

    configure_file(
        scripts/kapsule.in
        "${CMAKE_CURRENT_BINARY_DIR}/kap"
        @ONLY
    )

    install(PROGRAMS
        "${CMAKE_CURRENT_BINARY_DIR}/kapsule"
        "${CMAKE_CURRENT_BINARY_DIR}/kap"
        DESTINATION "${KDE_INSTALL_BINDIR}"
    )
endif()

# ============================================================================
# Vendor Python Dependencies
# ============================================================================

if(INSTALL_PYTHON_CLI AND VENDOR_PYTHON_DEPS)
    # List of dependencies to vendor (from pyproject.toml)
    set(KAPSULE_PYTHON_DEPENDENCIES
        "httpx>=0.25.0"
        "dbus-fast>=2.0.0"
        "typer>=0.9.0"
        "rich>=13.0.0"
        "pyyaml>=6.0"
        "pydantic>=2.0"
        # Transitive dependencies that need to be included
        "click"
        "shellingham"
        "markdown-it-py"
        "pygments"
        "httpcore"
        "anyio"
        "sniffio"
        "certifi"
        "idna"
        "h11"
        "typing_extensions"
        "annotated-types"
        "pydantic_core"
    )

    # Create vendor directory in build tree
    set(VENDOR_BUILD_DIR "${CMAKE_CURRENT_BINARY_DIR}/vendor")
    file(MAKE_DIRECTORY "${VENDOR_BUILD_DIR}")

    # Custom target to download dependencies
    add_custom_target(vendor_python_deps
        COMMAND ${Python3_EXECUTABLE} -m pip install
            --target "${VENDOR_BUILD_DIR}"
            --upgrade
            --no-user
            --no-cache-dir
            ${KAPSULE_PYTHON_DEPENDENCIES}
        COMMENT "Downloading Python dependencies to vendor directory"
        VERBATIM
    )

    # Install vendored dependencies
    # We use install(DIRECTORY) with a CODE block to handle the timing
    install(CODE "
        message(STATUS \"Installing vendored Python dependencies...\")
        execute_process(
            COMMAND ${Python3_EXECUTABLE} -m pip install
                --target \"${VENDOR_BUILD_DIR}\"
                --upgrade
                --no-user
                --no-cache-dir
                ${KAPSULE_PYTHON_DEPENDENCIES}
            RESULT_VARIABLE pip_result
            OUTPUT_VARIABLE pip_output
            ERROR_VARIABLE pip_error
        )
        if(NOT pip_result EQUAL 0)
            message(WARNING \"Failed to vendor Python dependencies: \${pip_error}\")
        endif()
    ")

    install(DIRECTORY "${VENDOR_BUILD_DIR}/"
        DESTINATION "${KAPSULE_VENDOR_DIR}"
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.dist-info" EXCLUDE
        PATTERN "*.egg-info" EXCLUDE
        PATTERN "bin" EXCLUDE
    )
endif()

# ============================================================================
# Systemd and D-Bus Service Files
# ============================================================================

if(INSTALL_PYTHON_CLI)
    # Full paths for service file variable substitution
    set(PYTHON_EXECUTABLE "${Python3_EXECUTABLE}")
    set(KAPSULE_PYTHON_DIR "${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/python")
    set(KAPSULE_VENDOR_DIR "${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/vendor")

    # Systemd unit directory (relative to CMAKE_INSTALL_PREFIX)
    set(KAPSULE_SYSTEMD_SYSTEM_UNIT_DIR "${KDE_INSTALL_LIBDIR}/systemd/system")
    set(KAPSULE_SYSTEMD_PRESET_DIR "${KDE_INSTALL_LIBDIR}/systemd/system-preset")

    # D-Bus directories
    set(KAPSULE_DBUS_SYSTEM_SERVICE_DIR "share/dbus-1/system-services")
    set(KAPSULE_DBUS_SYSTEM_POLICY_DIR "share/dbus-1/system.d")

    # Configure systemd system service
    configure_file(
        data/systemd/system/kapsule-daemon.service
        "${CMAKE_CURRENT_BINARY_DIR}/systemd/system/kapsule-daemon.service"
        @ONLY
    )
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/systemd/system/kapsule-daemon.service"
        DESTINATION "${KAPSULE_SYSTEMD_SYSTEM_UNIT_DIR}"
    )

    # Install drop-in for incus-user.socket to allow all users access
    install(FILES
        data/systemd/system/incus-user.socket.d/kapsule-socket-mode.conf
        DESTINATION "${KAPSULE_SYSTEMD_SYSTEM_UNIT_DIR}/incus-user.socket.d"
    )

    # Install drop-in for incus.socket to use wheel group
    install(FILES
        data/systemd/system/incus.socket.d/kapsule-socket-group.conf
        DESTINATION "${KAPSULE_SYSTEMD_SYSTEM_UNIT_DIR}/incus.socket.d"
    )

    # Install drop-in for incus.service (log dir + wheel group)
    install(FILES
        data/systemd/system/incus.service.d/kapsule-log-dir.conf
        DESTINATION "${KAPSULE_SYSTEMD_SYSTEM_UNIT_DIR}/incus.service.d"
    )

    # Install systemd preset to enable incus sockets by default
    install(FILES
        data/systemd/system-preset/50-kapsule.preset
        DESTINATION "${KAPSULE_SYSTEMD_PRESET_DIR}"
    )

    # Install modules-load.d config for nested container support (Docker/Podman inside containers)
    install(FILES
        data/modules-load.d/kapsule.conf
        DESTINATION "${KDE_INSTALL_LIBDIR}/modules-load.d"
    )

    # Configure D-Bus system service (auto-activation)
    configure_file(
        data/dbus/system/org.kde.kapsule.service
        "${CMAKE_CURRENT_BINARY_DIR}/dbus/system/org.kde.kapsule.service"
        @ONLY
    )
    install(FILES
        "${CMAKE_CURRENT_BINARY_DIR}/dbus/system/org.kde.kapsule.service"
        DESTINATION "${KAPSULE_DBUS_SYSTEM_SERVICE_DIR}"
    )

    # D-Bus system bus policy
    install(FILES
        data/dbus/system/org.kde.kapsule.conf
        DESTINATION "${KAPSULE_DBUS_SYSTEM_POLICY_DIR}"
    )

    # Kapsule default configuration
    install(FILES
        data/kapsule.conf
        DESTINATION "${CMAKE_INSTALL_PREFIX}/lib/kapsule"
    )
endif()

# ============================================================================
# KDE Components
# ============================================================================

if(BUILD_KDE_COMPONENTS)
    # Dependencies for KDE components
    set(REQUIRED_QT_VERSION "6.6.0")
    set(REQUIRED_KF_VERSION "6.0.0")

    find_package(Qt6 ${REQUIRED_QT_VERSION} CONFIG REQUIRED COMPONENTS
        Core
        DBus
        Concurrent
    )

    find_package(KF6 ${REQUIRED_KF_VERSION} REQUIRED COMPONENTS
        CoreAddons
        I18n
        Config
    )

    # Optional dependencies for additional components
    find_package(KF6KIO ${REQUIRED_KF_VERSION})
    set_package_properties(KF6KIO PROPERTIES
        TYPE OPTIONAL
        PURPOSE "Required for KIO worker"
    )

    find_package(Plasma ${REQUIRED_KF_VERSION})
    set_package_properties(Plasma PROPERTIES
        TYPE OPTIONAL
        PURPOSE "Required for Plasma widget"
    )

    find_package(KF6KCMUtils ${REQUIRED_KF_VERSION})
    set_package_properties(KF6KCMUtils PROPERTIES
        TYPE OPTIONAL
        PURPOSE "Required for System Settings module"
    )

    # Build libkapsule-qt
    add_subdirectory(src/libkapsule-qt)

    if(KF6KIO_FOUND)
        # add_subdirectory(src/kio)  # TODO: Enable when KIO worker is implemented
    endif()

    if(Plasma_FOUND)
        # add_subdirectory(src/plasmoid)  # TODO: Enable when plasmoid is implemented
    endif()

    if(KF6KCMUtils_FOUND)
        # add_subdirectory(src/kcm)  # TODO: Enable when KCM is implemented
    endif()
endif()

# ============================================================================
# Summary
# ============================================================================

feature_summary(WHAT ALL FATAL_ON_MISSING_REQUIRED_PACKAGES)

message(STATUS "")
message(STATUS "Kapsule ${PROJECT_VERSION} configuration:")
message(STATUS "  Python CLI:        ${INSTALL_PYTHON_CLI}")
if(INSTALL_PYTHON_CLI)
    message(STATUS "    Python:          ${Python3_EXECUTABLE} (${Python3_VERSION})")
    message(STATUS "    Vendor deps:     ${VENDOR_PYTHON_DEPS}")
    message(STATUS "    Install path:    ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/python")
    message(STATUS "    Vendor path:     ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/vendor")
    message(STATUS "    Systemd service: ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_LIBDIR}/systemd/system/")
endif()
message(STATUS "  KDE components:    ${BUILD_KDE_COMPONENTS}")
message(STATUS "")
