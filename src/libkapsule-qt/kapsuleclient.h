/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#ifndef KAPSULECLIENT_H
#define KAPSULECLIENT_H

#include <QObject>
#include <QString>
#include <QList>
#include <QVariantMap>
#include <memory>

#include <qcoro/qcorotask.h>

#include "kapsule_export.h"
#include "container.h"
#include "types.h"

namespace Kapsule {

class KapsuleClientPrivate;

/**
 * @class KapsuleClient
 * @brief Qt client for communicating with the kapsule-daemon via D-Bus.
 *
 * This class provides a Qt-friendly async API using C++20 coroutines (QCoro)
 * for managing Incus containers through the kapsule-daemon service.
 *
 * @code
 * KapsuleClient client;
 * 
 * // List containers (coroutine)
 * auto containers = co_await client.listContainers();
 *
 * // Create a new container with progress
 * auto result = co_await client.createContainer("dev-ubuntu", "ubuntu:24.04",
 *     ContainerMode::Default,
 *     [](MessageType type, const QString &msg, int) {
 *         qDebug() << msg;
 *     });
 * @endcode
 *
 * @since 0.1
 */
class KAPSULE_EXPORT KapsuleClient : public QObject
{
    Q_OBJECT

    /**
     * @property connected
     * @brief Whether the client is connected to the kapsule-daemon.
     */
    Q_PROPERTY(bool connected READ isConnected NOTIFY connectedChanged)

    /**
     * @property daemonVersion
     * @brief The version of the connected daemon.
     */
    Q_PROPERTY(QString daemonVersion READ daemonVersion NOTIFY connectedChanged)

public:
    /**
     * @brief Creates a new KapsuleClient instance.
     * @param parent The parent QObject.
     */
    explicit KapsuleClient(QObject *parent = nullptr);

    /**
     * @brief Destructor.
     */
    ~KapsuleClient() override;

    /**
     * @brief Returns whether the client is connected to the daemon.
     * @return true if connected, false otherwise.
     */
    [[nodiscard]] bool isConnected() const;

    /**
     * @brief Returns the daemon version string.
     * @return The version, or empty string if not connected.
     */
    [[nodiscard]] QString daemonVersion() const;

    // =========================================================================
    // Coroutine-based API
    // =========================================================================

    /**
     * @brief List all containers.
     * @return List of Container objects.
     */
    QCoro::Task<QList<Container>> listContainers();

    /**
     * @brief Get a specific container by name.
     * @param name The container name.
     * @return The Container, or invalid container if not found.
     */
    QCoro::Task<Container> container(const QString &name);

    /**
     * @brief Get user configuration from daemon.
     * @return Map of config keys to values.
     */
    QCoro::Task<QVariantMap> config();

    /**
     * @brief Create a new container.
     * @param name The name for the new container.
     * @param image The base image to use (e.g., "ubuntu:24.04"), empty for default.
     * @param options Container creation options (see ContainerOptions).
     * @param progress Optional callback for progress messages.
     * @return Operation result with success/error info.
     */
    QCoro::Task<OperationResult> createContainer(
        const QString &name,
        const QString &image,
        const ContainerOptions &options = {},
        ProgressHandler progress = {});

    /**
     * @brief Delete a container.
     * @param name The container name.
     * @param force Force removal even if running.
     * @param progress Optional callback for progress messages.
     * @return Operation result with success/error info.
     */
    QCoro::Task<OperationResult> deleteContainer(
        const QString &name,
        bool force = false,
        ProgressHandler progress = {});

    /**
     * @brief Start a stopped container.
     * @param name The container name.
     * @param progress Optional callback for progress messages.
     * @return Operation result with success/error info.
     */
    QCoro::Task<OperationResult> startContainer(
        const QString &name,
        ProgressHandler progress = {});

    /**
     * @brief Stop a running container.
     * @param name The container name.
     * @param force Force stop the container.
     * @param progress Optional callback for progress messages.
     * @return Operation result with success/error info.
     */
    QCoro::Task<OperationResult> stopContainer(
        const QString &name,
        bool force = false,
        ProgressHandler progress = {});

    /**
     * @brief Prepare to enter a container.
     *
     * This handles all setup: container creation, user setup, symlinks.
     * Returns exec args for the caller to execvp().
     *
     * @param containerName Container to enter (empty for default).
     * @param command Command to run inside (empty for shell).
     * @return Enter result with success/error and exec args.
     */
    QCoro::Task<EnterResult> prepareEnter(
        const QString &containerName = {},
        const QStringList &command = {});

Q_SIGNALS:
    /**
     * @brief Emitted when the connection state changes.
     * @param connected The new connection state.
     */
    void connectedChanged(bool connected);

    /**
     * @brief Emitted when a container's state changes.
     * @param name The name of the container.
     * @param state The new state.
     */
    void containerStateChanged(const QString &name, Container::State state);

    /**
     * @brief Emitted when an error occurs.
     * @param message The error message.
     */
    void errorOccurred(const QString &message);

private:
    std::unique_ptr<KapsuleClientPrivate> d;
};

} // namespace Kapsule

#endif // KAPSULECLIENT_H
