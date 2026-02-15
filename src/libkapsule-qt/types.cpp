/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "types.h"
#include "container.h"
#include <QDBusMetaType>
#include <QDBusVariant>

namespace Kapsule {

QVariantMap ContainerOptions::toVariantMap() const
{
    // Only include options that differ from schema defaults
    // to stay forward-compatible (daemon fills missing keys).
    QVariantMap map;

    if (sessionMode)
        map.insert(QStringLiteral("session_mode"), QVariant::fromValue(QDBusVariant(sessionMode)));
    if (dbusMux)
        map.insert(QStringLiteral("dbus_mux"), QVariant::fromValue(QDBusVariant(dbusMux)));
    if (!hostRootfs)
        map.insert(QStringLiteral("host_rootfs"), QVariant::fromValue(QDBusVariant(hostRootfs)));
    if (!mountHome)
        map.insert(QStringLiteral("mount_home"), QVariant::fromValue(QDBusVariant(mountHome)));
    if (!customMounts.isEmpty())
        map.insert(QStringLiteral("custom_mounts"), QVariant::fromValue(QDBusVariant(QVariant(customMounts))));
    if (!gpu)
        map.insert(QStringLiteral("gpu"), QVariant::fromValue(QDBusVariant(gpu)));
    if (!nvidiaDrivers)
        map.insert(QStringLiteral("nvidia_drivers"), QVariant::fromValue(QDBusVariant(nvidiaDrivers)));

    return map;
}

// D-Bus argument streaming for EnterResult (bsas)
QDBusArgument &operator<<(QDBusArgument &arg, const EnterResult &result)
{
    arg.beginStructure();
    arg << result.success << result.error << result.execArgs;
    arg.endStructure();
    return arg;
}

const QDBusArgument &operator>>(const QDBusArgument &arg, EnterResult &result)
{
    arg.beginStructure();
    arg >> result.success >> result.error >> result.execArgs;
    arg.endStructure();
    return arg;
}

void registerDBusTypes()
{
    static bool registered = false;
    if (registered) {
        return;
    }
    registered = true;

    qDBusRegisterMetaType<Container>();
    qDBusRegisterMetaType<QList<Container>>();
    qDBusRegisterMetaType<EnterResult>();
    qDBusRegisterMetaType<QMap<QString, QString>>();
}

} // namespace Kapsule

// This file exists to provide the moc-generated staticMetaObject
// for the Kapsule namespace (Q_NAMESPACE_EXPORT).
// The actual moc output is included below.

#include "moc_types.cpp"
