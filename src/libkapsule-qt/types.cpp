/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "types.h"
#include "container.h"
#include <QDBusMetaType>

namespace Kapsule {

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
}

} // namespace Kapsule

// This file exists to provide the moc-generated staticMetaObject
// for the Kapsule namespace (Q_NAMESPACE_EXPORT).
// The actual moc output is included below.

#include "moc_types.cpp"
