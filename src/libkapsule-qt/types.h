/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#ifndef KAPSULE_TYPES_H
#define KAPSULE_TYPES_H

#include <QString>
#include <QStringList>
#include <QMetaType>
#include <QMetaEnum>
#include <QDBusArgument>
#include <functional>

#include "kapsule_export.h"

namespace Kapsule {
Q_NAMESPACE_EXPORT(KAPSULE_EXPORT)

/**
 * @brief Result of prepareEnter() - D-Bus signature (bsas)
 */
struct KAPSULE_EXPORT EnterResult {
    bool success = false;
    QString error;
    QStringList execArgs;
};

/**
 * @brief Register D-Bus metatypes. Call once at startup.
 */
KAPSULE_EXPORT void registerDBusTypes();

/**
 * @enum ContainerMode
 * @brief The D-Bus integration mode for a container.
 */
enum class ContainerMode {
    Default,    ///< Host D-Bus session shared with container
    Session,    ///< Container has its own D-Bus session bus
    DbusMux     ///< D-Bus multiplexer for hybrid host/container access
};
Q_ENUM_NS(ContainerMode)

/**
 * @enum MessageType
 * @brief Message types for daemon operation progress.
 *
 * These match the Python MessageType enum used by the daemon.
 */
enum class MessageType {
    Info = 0,
    Success = 1,
    Warning = 2,
    Error = 3,
    Dim = 4,
    Hint = 5
};
Q_ENUM_NS(MessageType)

/**
 * @brief Result of an async operation.
 */
struct KAPSULE_EXPORT OperationResult {
    bool success = false;
    QString error;
};

/**
 * @brief Progress callback for long-running operations.
 *
 * @param type The message type
 * @param message The message text
 * @param indentLevel Indentation level for hierarchical display
 */
using ProgressHandler = std::function<void(MessageType type, const QString &message, int indentLevel)>;

/**
 * @brief Convert ContainerMode to string using Qt meta-enum.
 */
inline QString containerModeToString(ContainerMode mode)
{
    return QString::fromLatin1(QMetaEnum::fromType<ContainerMode>().valueToKey(static_cast<int>(mode)));
}

/**
 * @brief Convert string to ContainerMode using Qt meta-enum.
 */
inline ContainerMode containerModeFromString(const QString &str)
{
    bool ok = false;
    int value = QMetaEnum::fromType<ContainerMode>().keyToValue(str.toLatin1().constData(), &ok);
    return ok ? static_cast<ContainerMode>(value) : ContainerMode::Default;
}

// D-Bus argument streaming operators
KAPSULE_EXPORT QDBusArgument &operator<<(QDBusArgument &arg, const EnterResult &result);
KAPSULE_EXPORT const QDBusArgument &operator>>(const QDBusArgument &arg, EnterResult &result);

} // namespace Kapsule

Q_DECLARE_METATYPE(Kapsule::ContainerMode)
Q_DECLARE_METATYPE(Kapsule::MessageType)
Q_DECLARE_METATYPE(Kapsule::OperationResult)
Q_DECLARE_METATYPE(Kapsule::EnterResult)

#endif // KAPSULE_TYPES_H
