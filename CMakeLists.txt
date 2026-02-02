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
option(INSTALL_PYTHON_DAEMON "Install Python daemon and vendored dependencies" ON)
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

if(INSTALL_PYTHON_DAEMON)
    find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)
    message(STATUS "Found Python ${Python3_VERSION} at ${Python3_EXECUTABLE}")

    # Define installation directories for Python components
    set(KAPSULE_PYTHON_DIR "${KDE_INSTALL_DATADIR}/kapsule/python")
    set(KAPSULE_VENDOR_DIR "${KDE_INSTALL_DATADIR}/kapsule/vendor")
endif()

# ============================================================================
# Python Daemon Installation
# ============================================================================

if(INSTALL_PYTHON_DAEMON)
    # Install kapsule daemon package
    install(FILES
        src/daemon/__init__.py
        src/daemon/__main__.py
        src/daemon/config.py
        src/daemon/container_service.py
        src/daemon/dbus_types.py
        src/daemon/incus_client.py
        src/daemon/models_generated.py
        src/daemon/operations.py
        src/daemon/profile.py
        src/daemon/service.py
        DESTINATION "${KAPSULE_PYTHON_DIR}/kapsule/daemon"
    )
endif()

# ============================================================================
# Vendor Python Dependencies
# ============================================================================

if(INSTALL_PYTHON_DAEMON AND VENDOR_PYTHON_DEPS)
    # List of dependencies to vendor (from pyproject.toml)
    set(KAPSULE_PYTHON_DEPENDENCIES
        "httpx>=0.25.0"
        "dbus-fast>=2.0.0"
        "pyyaml>=6.0"
        "pydantic>=2.0"
        # Transitive dependencies that need to be included
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
# D-Bus Introspection XML Generation
# ============================================================================

if(INSTALL_PYTHON_DAEMON)
    # Generate D-Bus introspection XML from Python service at build time
    set(DBUS_INTROSPECTION_XML "${CMAKE_CURRENT_BINARY_DIR}/org.kde.kapsule.xml")
    
    add_custom_command(
        OUTPUT "${DBUS_INTROSPECTION_XML}"
        COMMAND ${CMAKE_COMMAND} -E env
            PYTHONPATH="${CMAKE_CURRENT_SOURCE_DIR}/src"
            ${Python3_EXECUTABLE}
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_dbus_introspection.py"
            --output "${DBUS_INTROSPECTION_XML}"
        DEPENDS
            "${CMAKE_CURRENT_SOURCE_DIR}/scripts/generate_dbus_introspection.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/src/daemon/service.py"
            "${CMAKE_CURRENT_SOURCE_DIR}/src/daemon/dbus_types.py"
        COMMENT "Generating D-Bus introspection XML from Python service"
        VERBATIM
    )
    
    add_custom_target(generate_dbus_xml ALL
        DEPENDS "${DBUS_INTROSPECTION_XML}"
    )
    
    # Install the introspection XML for documentation/development purposes
    install(FILES "${DBUS_INTROSPECTION_XML}"
        DESTINATION "${CMAKE_INSTALL_PREFIX}/share/dbus-1/interfaces"
    )
endif()

# ============================================================================
# Systemd and D-Bus Service Files
# ============================================================================

if(INSTALL_PYTHON_DAEMON)
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

    # Kapsule init script
    install(PROGRAMS
        scripts/kapsule-init.sh
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

    # QCoro for C++20 coroutines with Qt
    find_package(QCoro6 REQUIRED COMPONENTS Core DBus)
    qcoro_enable_coroutines()

    # Ensure C++20 is used (required for QCoro)
    set(CMAKE_CXX_STANDARD 20)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)

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

    # Build C++ CLI
    add_subdirectory(src/cli)

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
message(STATUS "  Python daemon:     ${INSTALL_PYTHON_DAEMON}")
if(INSTALL_PYTHON_DAEMON)
    message(STATUS "    Python:          ${Python3_EXECUTABLE} (${Python3_VERSION})")
    message(STATUS "    Vendor deps:     ${VENDOR_PYTHON_DEPS}")
    message(STATUS "    Install path:    ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/python")
    message(STATUS "    Vendor path:     ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_DATADIR}/kapsule/vendor")
    message(STATUS "    Systemd service: ${CMAKE_INSTALL_PREFIX}/${KDE_INSTALL_LIBDIR}/systemd/system/")
endif()
message(STATUS "  KDE components:    ${BUILD_KDE_COMPONENTS}")
message(STATUS "")
