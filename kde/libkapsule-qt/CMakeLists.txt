# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# libkapsule-qt - Qt wrapper for kapsule D-Bus API

# ============================================================================
# Target definition
# ============================================================================

set(kapsule_SRCS
    kapsuleclient.cpp
    container.cpp
)

set(kapsule_HEADERS
    kapsuleclient.h
    container.h
)

add_library(KapsuleQt ${kapsule_SRCS})
add_library(Kapsule::KapsuleQt ALIAS KapsuleQt)

# Generate export header
ecm_generate_export_header(KapsuleQt
    BASE_NAME kapsule
    EXPORT_FILE_NAME kapsule_export.h
    GROUP_BASE_NAME KDE
    VERSION ${KAPSULE_VERSION}
    USE_VERSION_HEADER "${CMAKE_CURRENT_BINARY_DIR}/../kapsule_version.h"
    DEPRECATED_BASE_VERSION 0
    DEPRECATION_VERSIONS
    EXCLUDE_DEPRECATED_BEFORE_AND_AT ${EXCLUDE_DEPRECATED_BEFORE_AND_AT}
)

# Generate public headers
ecm_generate_headers(KapsuleQt_CamelCase_HEADERS
    HEADER_NAMES
        KapsuleClient
        Container
    PREFIX Kapsule
    REQUIRED_HEADERS kapsule_HEADERS
)

# ============================================================================
# Target properties
# ============================================================================

set_target_properties(KapsuleQt PROPERTIES
    VERSION ${KAPSULE_VERSION}
    SOVERSION ${KAPSULE_SOVERSION}
    EXPORT_NAME KapsuleQt
)

target_include_directories(KapsuleQt
    PUBLIC
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}>"
        "$<BUILD_INTERFACE:${CMAKE_CURRENT_BINARY_DIR}>"
    INTERFACE
        "$<INSTALL_INTERFACE:${KDE_INSTALL_INCLUDEDIR}/Kapsule>"
)

target_link_libraries(KapsuleQt
    PUBLIC
        Qt6::Core
    PRIVATE
        Qt6::DBus
        Qt6::Concurrent
        KF6::CoreAddons
        KF6::I18n
)

# ============================================================================
# Logging category
# ============================================================================

ecm_qt_declare_logging_category(KapsuleQt
    HEADER kapsule_debug.h
    IDENTIFIER KAPSULE_LOG
    CATEGORY_NAME org.kde.kapsule
    DESCRIPTION "Kapsule"
    EXPORT KAPSULE
)

# ============================================================================
# Installation
# ============================================================================

install(TARGETS KapsuleQt EXPORT KapsuleTargets ${KDE_INSTALL_TARGETS_DEFAULT_ARGS})

install(FILES
    ${kapsule_HEADERS}
    ${CMAKE_CURRENT_BINARY_DIR}/kapsule_export.h
    DESTINATION ${KDE_INSTALL_INCLUDEDIR}/Kapsule/kapsule
    COMPONENT Devel
)

install(FILES
    ${KapsuleQt_CamelCase_HEADERS}
    DESTINATION ${KDE_INSTALL_INCLUDEDIR}/Kapsule/Kapsule
    COMPONENT Devel
)

# ============================================================================
# CMake Config files
# ============================================================================

set(CMAKECONFIG_INSTALL_DIR "${KDE_INSTALL_CMAKEPACKAGEDIR}/Kapsule")

configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/KapsuleConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/KapsuleConfig.cmake"
    INSTALL_DESTINATION ${CMAKECONFIG_INSTALL_DIR}
)

install(FILES
    "${CMAKE_CURRENT_BINARY_DIR}/KapsuleConfig.cmake"
    "${CMAKE_CURRENT_BINARY_DIR}/../KapsuleConfigVersion.cmake"
    DESTINATION ${CMAKECONFIG_INSTALL_DIR}
    COMPONENT Devel
)

install(EXPORT KapsuleTargets
    DESTINATION ${CMAKECONFIG_INSTALL_DIR}
    FILE KapsuleTargets.cmake
    NAMESPACE Kapsule::
    COMPONENT Devel
)

# ============================================================================
# pkg-config file
# ============================================================================

ecm_generate_pkgconfig_file(
    BASE_NAME kapsule-qt
    LIB_NAME KapsuleQt
    INCLUDE_INSTALL_DIR ${KDE_INSTALL_INCLUDEDIR}/Kapsule
    DEPS "Qt6Core"
    DESCRIPTION "Qt wrapper for Kapsule D-Bus API"
    INSTALL
)
