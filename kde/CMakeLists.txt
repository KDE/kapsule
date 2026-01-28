# SPDX-FileCopyrightText: 2024-2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# KDE Components for Kapsule
# This is a stub CMakeLists.txt that will be called from meson
# when kde_components is enabled.

cmake_minimum_required(VERSION 3.27)

project(KapsuleKDE
    VERSION 0.1.0
    DESCRIPTION "KDE components for Kapsule container management"
    LANGUAGES CXX
)

# ============================================================================
# ECM Setup
# ============================================================================

find_package(ECM 6.0.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

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
# Dependencies
# ============================================================================

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

# ============================================================================
# Subdirectories
# ============================================================================

add_subdirectory(libkapsule-qt)

if(KF6KIO_FOUND)
    # add_subdirectory(kio)  # TODO: Enable when KIO worker is implemented
endif()

if(Plasma_FOUND)
    # add_subdirectory(plasmoid)  # TODO: Enable when plasmoid is implemented
endif()

if(KF6KCMUtils_FOUND)
    # add_subdirectory(kcm)  # TODO: Enable when KCM is implemented
endif()

# ============================================================================
# Summary
# ============================================================================

feature_summary(WHAT ALL FATAL_ON_MISSING_REQUIRED_PACKAGES)
