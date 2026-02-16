/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#ifndef KAPSULE_CONTAINER_H
#define KAPSULE_CONTAINER_H

#include <QObject>
#include <QString>
#include <QStringList>
#include <QDateTime>
#include <QSharedDataPointer>

#include "kapsule_export.h"
#include "types.h"

class QDBusArgument;

namespace Kapsule {

class ContainerData;

/**
 * @class Container
 * @brief Represents a kapsule container.
 *
 * This class provides information about a single container managed by kapsule.
 * Container objects are implicitly shared.
 *
 * @since 0.1
 */
class KAPSULE_EXPORT Container
{
    Q_GADGET

    Q_PROPERTY(QString name READ name)
    Q_PROPERTY(State state READ state)
    Q_PROPERTY(QString image READ image)
    Q_PROPERTY(ContainerMode mode READ mode)
    Q_PROPERTY(QDateTime created READ created)

public:
    /**
     * @enum State
     * @brief The current state of the container.
     */
    enum class State {
        Unknown,    ///< State could not be determined
        Stopped,    ///< Container is stopped
        Starting,   ///< Container is starting up
        Running,    ///< Container is running
        Stopping,   ///< Container is shutting down
        Error       ///< Container is in an error state
    };
    Q_ENUM(State)

    /**
     * @brief Constructs an invalid container.
     */
    Container();

    /**
     * @brief Constructs a container with the given name.
     * @param name The container name.
     */
    explicit Container(const QString &name);

    /**
     * @brief Copy constructor.
     */
    Container(const Container &other);

    /**
     * @brief Move constructor.
     */
    Container(Container &&other) noexcept;

    /**
     * @brief Destructor.
     */
    ~Container();

    /**
     * @brief Copy assignment operator.
     */
    Container &operator=(const Container &other);

    /**
     * @brief Move assignment operator.
     */
    Container &operator=(Container &&other) noexcept;

    /**
     * @brief Returns whether this container object is valid.
     * @return true if valid, false otherwise.
     */
    [[nodiscard]] bool isValid() const;

    /**
     * @brief Returns the container name.
     * @return The container name.
     */
    [[nodiscard]] QString name() const;

    /**
     * @brief Returns the current state of the container.
     * @return The container state.
     */
    [[nodiscard]] State state() const;

    /**
     * @brief Returns the base image used for this container.
     * @return The image name (e.g., "ubuntu:24.04").
     */
    [[nodiscard]] QString image() const;

    /**
     * @brief Returns the D-Bus integration mode for this container.
     * @return The container mode.
     */
    [[nodiscard]] ContainerMode mode() const;

    /**
     * @brief Returns when the container was created.
     * @return The creation timestamp.
     */
    [[nodiscard]] QDateTime created() const;

    /**
     * @brief Returns whether the container is running.
     * @return true if running, false otherwise.
     */
    [[nodiscard]] bool isRunning() const;

    /**
     * @brief Comparison operator.
     */
    bool operator==(const Container &other) const;

    /**
     * @brief Inequality operator.
     */
    bool operator!=(const Container &other) const;

private:
    QSharedDataPointer<ContainerData> d;

    friend class KapsuleClient;
    friend class KapsuleClientPrivate;
    friend KAPSULE_EXPORT QDBusArgument &operator<<(QDBusArgument &arg, const Container &container);
    friend KAPSULE_EXPORT const QDBusArgument &operator>>(const QDBusArgument &arg, Container &container);
};

// D-Bus argument streaming operators for Container (sssss)
KAPSULE_EXPORT QDBusArgument &operator<<(QDBusArgument &arg, const Container &container);
KAPSULE_EXPORT const QDBusArgument &operator>>(const QDBusArgument &arg, Container &container);

} // namespace Kapsule

Q_DECLARE_METATYPE(Kapsule::Container)
Q_DECLARE_METATYPE(Kapsule::Container::State)

#endif // KAPSULE_CONTAINER_H
