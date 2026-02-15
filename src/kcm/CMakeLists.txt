# SPDX-FileCopyrightText: 2026 KDE Community
# SPDX-License-Identifier: BSD-3-Clause

# kcm_kapsule - System Settings module for Kapsule container management

find_package(Qt6 ${REQUIRED_QT_VERSION} CONFIG REQUIRED COMPONENTS Quick)

kcmutils_add_qml_kcm(kcm_kapsule)

target_sources(kcm_kapsule PRIVATE
    kcm_kapsule.cpp
    kcm_kapsule.h
    containerlistmodel.cpp
    containerlistmodel.h
    createschemamodel.cpp
    createschemamodel.h
)

# QCoro uses exceptions internally
kde_target_enable_exceptions(kcm_kapsule PRIVATE)

target_link_libraries(kcm_kapsule PRIVATE
    Qt6::Quick
    Qt6::DBus
    KF6::CoreAddons
    KF6::KCMUtils
    KF6::KCMUtilsQuick
    KF6::I18n
    Kapsule::KapsuleQt
    QCoro6::Core
)
