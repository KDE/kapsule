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
#include <QVariantMap>
#include <QJsonValue>
#include <functional>
#include <optional>

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

// =========================================================================
// Schema types — mirror the Python CREATE_SCHEMA format
// =========================================================================

/**
 * @brief A single option in the create-container schema.
 *
 * Parsed from the JSON returned by GetCreateSchema().
 * Carries everything needed to generate a CLI flag or GUI widget.
 */
struct KAPSULE_EXPORT CreateSchemaOption {
    QString key;                       ///< D-Bus a{sv} dict key (e.g. "mount_home")
    QString type;                      ///< "boolean", "string", or "array"
    QString title;                     ///< Short UI label
    QString description;               ///< Longer help text
    QJsonValue defaultValue;           ///< Schema default
    QVariantMap dependencies;          ///< Inter-option dependencies (key → required value)

    /// Convert key to CLI flag name (underscores → dashes).
    [[nodiscard]] QString cliFlag() const;

    /// True when the default is `true` (boolean options that default on).
    [[nodiscard]] bool defaultsToTrue() const;
};

/**
 * @brief A section grouping related options.
 */
struct KAPSULE_EXPORT CreateSchemaSection {
    QString id;
    QString title;
    QList<CreateSchemaOption> options;
};

/**
 * @brief The full create-container schema.
 */
struct KAPSULE_EXPORT CreateSchema {
    int version = 0;
    QList<CreateSchemaSection> sections;

    /// Flat list of every option across all sections.
    [[nodiscard]] QList<CreateSchemaOption> allOptions() const;

    /// Look up an option by key.
    [[nodiscard]] std::optional<CreateSchemaOption> option(const QString &key) const;
};

/**
 * @brief Parse the JSON string returned by GetCreateSchema().
 * @param json The raw JSON string.
 * @return Parsed schema, or empty schema on parse error.
 */
KAPSULE_EXPORT CreateSchema parseCreateSchema(const QString &json);

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
