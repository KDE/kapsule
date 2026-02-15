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
 * @brief Options for container creation.
 *
 * This struct mirrors the Python `ContainerOptions` dataclass and the
 * Kapsule option schema defined in `container_options.py`.  Each field
 * corresponds to a key in the schema; default values match the schema
 * defaults so that a default-constructed `ContainerOptions{}` produces
 * a standard container with all features enabled.
 *
 * The struct is serialised to a D-Bus `a{sv}` (variant dict) by
 * toVariantMap().  Only fields that differ from the schema defaults
 * are included in the dict, keeping messages small and ensuring
 * forward compatibility — the daemon applies defaults for any keys
 * the client omits.
 *
 * Clients can query the full schema programmatically by calling
 * `GetCreateSchema()` on the daemon's Manager interface, which returns
 * a JSON string describing all options, their types, defaults,
 * grouping, and inter-field dependencies.
 *
 * ### CLI mapping
 *
 * | Field        | CLI flag           | Inverted? |
 * |--------------|--------------------|-----------|
 * | sessionMode  | --session          | no        |
 * | dbusMux      | --dbus-mux         | no        |
 * | hostRootfs   | --no-host-rootfs   | yes       |
 * | mountHome    | --no-home          | yes       |
 * | customMounts | --mount \<path\>   | no        |
 * | gpu          | --no-gpu           | yes       |
 * | nvidiaDrivers| --no-nvidia-drivers| yes       |
 *
 * @see ContainerOptions::toVariantMap()
 * @see KapsuleClient::createContainer()
 * @since 0.1
 */
struct KAPSULE_EXPORT ContainerOptions {
    /// Enable session mode with container D-Bus.
    bool sessionMode = false;
    /// Enable D-Bus multiplexer (implies sessionMode).
    bool dbusMux = false;
    /// Mount entire host filesystem at /.kapsule/host.
    bool hostRootfs = true;
    /// Mount the user's home directory in the container.
    bool mountHome = true;
    /// Extra host directories to mount in the container.
    QStringList customMounts;
    /// Pass through GPU devices.
    bool gpu = true;
    /// Inject host NVIDIA userspace drivers on each start.
    bool nvidiaDrivers = true;

    /**
     * @brief Serialize to a D-Bus a{sv} variant map.
     *
     * Only includes options that differ from schema defaults
     * to keep the message small and forward-compatible.
     */
    [[nodiscard]] QVariantMap toVariantMap() const;
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
Q_DECLARE_METATYPE(Kapsule::ContainerOptions)
Q_DECLARE_METATYPE(Kapsule::EnterResult)

#endif // KAPSULE_TYPES_H
