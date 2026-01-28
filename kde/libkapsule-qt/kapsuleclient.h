/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#ifndef KAPSULECLIENT_H
#define KAPSULECLIENT_H

#include <QObject>
#include <QString>
#include <QList>
#include <QFuture>
#include <memory>

#include "kapsule_export.h"
#include "container.h"

namespace Kapsule {

class KapsuleClientPrivate;

/**
 * @class KapsuleClient
 * @brief Qt client for communicating with the kapsule-daemon via D-Bus.
 *
 * This class provides a Qt-friendly API for managing Incus containers
 * through the kapsule-daemon service.
 *
 * @code
 * auto client = new Kapsule::KapsuleClient(this);
 * connect(client, &KapsuleClient::containersChanged, this, &MyClass::updateContainerList);
 *
 * // List containers
 * auto containers = client->containers();
 *
 * // Create a new container
 * client->createContainer("dev-ubuntu", "ubuntu:24.04");
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
     * @property containers
     * @brief List of available containers.
     */
    Q_PROPERTY(QList<Container> containers READ containers NOTIFY containersChanged)

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
     * @brief Returns the list of available containers.
     * @return List of Container objects.
     */
    [[nodiscard]] QList<Container> containers() const;

    /**
     * @brief Finds a container by name.
     * @param name The container name to search for.
     * @return The Container if found, or an invalid Container otherwise.
     */
    [[nodiscard]] Container container(const QString &name) const;

public Q_SLOTS:
    /**
     * @brief Refreshes the list of containers from the daemon.
     */
    void refresh();

    /**
     * @brief Creates a new container.
     * @param name The name for the new container.
     * @param image The base image to use (e.g., "ubuntu:24.04").
     * @param features Optional list of features to enable.
     * @return A future that resolves when the container is created.
     */
    QFuture<bool> createContainer(const QString &name,
                                   const QString &image,
                                   const QStringList &features = {});

    /**
     * @brief Starts a container.
     * @param name The name of the container to start.
     * @return A future that resolves when the container is started.
     */
    QFuture<bool> startContainer(const QString &name);

    /**
     * @brief Stops a container.
     * @param name The name of the container to stop.
     * @return A future that resolves when the container is stopped.
     */
    QFuture<bool> stopContainer(const QString &name);

    /**
     * @brief Removes a container.
     * @param name The name of the container to remove.
     * @param force If true, force removal even if running.
     * @return A future that resolves when the container is removed.
     */
    QFuture<bool> removeContainer(const QString &name, bool force = false);

    /**
     * @brief Enters a container (opens a terminal session).
     * @param name The name of the container to enter.
     * @param command Optional command to run instead of default shell.
     */
    void enterContainer(const QString &name, const QString &command = {});

Q_SIGNALS:
    /**
     * @brief Emitted when the connection state changes.
     * @param connected The new connection state.
     */
    void connectedChanged(bool connected);

    /**
     * @brief Emitted when the container list changes.
     */
    void containersChanged();

    /**
     * @brief Emitted when a container's state changes.
     * @param name The name of the container that changed.
     */
    void containerStateChanged(const QString &name);

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
