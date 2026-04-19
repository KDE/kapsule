/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "kapsuleclient.h"
#include "kapsule_debug.h"
#include "kapsulemanagerinterface.h"
#include "kapsuleoperationinterface.h"
#include "types.h"

#include <QDBusConnection>
#include <QDBusObjectPath>
#include <QDBusPendingReply>
#include <QDBusServiceWatcher>

#include <qcoro/qcorodbuspendingreply.h>
#include <qcoro/qcorosignal.h>

namespace Kapsule {

// ============================================================================
// Private implementation
// ============================================================================

class KapsuleClientPrivate
{
public:
    explicit KapsuleClientPrivate(KapsuleClient *q);

    void connectToDaemon();
    QCoro::Task<OperationResult> waitForOperation(
        const QString &objectPath,
        OperationCallbacks callbacks);

    void setConnected(bool value);

    KapsuleClient *q_ptr;
    std::unique_ptr<OrgKdeKapsuleManagerInterface> interface;
    QDBusServiceWatcher serviceWatcher;
    QString daemonVersion;
    bool connected = false;
};

KapsuleClientPrivate::KapsuleClientPrivate(KapsuleClient *q)
    : q_ptr(q)
    , serviceWatcher(QStringLiteral("org.kde.kapsule"),
                     QDBusConnection::systemBus(),
                     QDBusServiceWatcher::WatchForRegistration
                         | QDBusServiceWatcher::WatchForUnregistration)
{
    // Register D-Bus types before any D-Bus operations
    registerDBusTypes();

    QObject::connect(&serviceWatcher, &QDBusServiceWatcher::serviceRegistered,
        q, [this](const QString &) { connectToDaemon(); });
    QObject::connect(&serviceWatcher, &QDBusServiceWatcher::serviceUnregistered,
        q, [this](const QString &) {
            qCDebug(KAPSULE_LOG) << "kapsule-daemon disappeared from the bus";
            setConnected(false);
        });

    connectToDaemon();
}

void KapsuleClientPrivate::connectToDaemon()
{
    interface = std::make_unique<OrgKdeKapsuleManagerInterface>(
        QStringLiteral("org.kde.kapsule"),
        QStringLiteral("/org/kde/kapsule"),
        QDBusConnection::systemBus()
    );

    // Forward D-Bus ContainersChanged signal to the Qt signal
    QObject::connect(interface.get(),
        &OrgKdeKapsuleManagerInterface::ContainersChanged,
        q_ptr, &KapsuleClient::containersChanged);

    // Read a property instead of checking isValid() — an actual D-Bus call
    // triggers bus activation so the daemon starts via systemd if needed.
    daemonVersion = interface->version();

    if (interface->lastError().isValid()) {
        qCWarning(KAPSULE_LOG) << "Failed to connect to kapsule-daemon:"
                               << interface->lastError().message();
        setConnected(false);
    } else {
        qCDebug(KAPSULE_LOG) << "Connected to kapsule-daemon version" << daemonVersion;
        setConnected(true);
    }
}

void KapsuleClientPrivate::setConnected(bool value)
{
    if (connected == value) {
        return;
    }
    connected = value;
    Q_EMIT q_ptr->connectedChanged(value);
}

QCoro::Task<OperationResult> KapsuleClientPrivate::waitForOperation(
    const QString &objectPath,
    OperationCallbacks callbacks)
{
    qCDebug(KAPSULE_LOG) << "Waiting for operation at" << objectPath;
    
    // Create a proxy for this specific operation
    auto opProxy = std::make_unique<OrgKdeKapsuleOperationInterface>(
        QStringLiteral("org.kde.kapsule"),
        objectPath,
        QDBusConnection::systemBus()
    );

    if (!opProxy->isValid()) {
        qCWarning(KAPSULE_LOG) << "Operation proxy is invalid:" << opProxy->lastError().message();
        co_return OperationResult{false, QStringLiteral("Failed to connect to operation object")};
    }

    qCDebug(KAPSULE_LOG) << "Operation proxy created successfully";

    // -----------------------------------------------------------------------
    // Race-condition guard: check if the operation already completed before
    // we subscribe to the Completed signal.  The status() call is a
    // synchronous D-Bus property Get — while the daemon handles this request
    // its single-threaded asyncio loop cannot emit Completed, so the window
    // between this check and the co_await below is safe.
    // -----------------------------------------------------------------------
    bool success = false;
    QString error;

    const QString currentStatus = opProxy->status();
    qCDebug(KAPSULE_LOG) << "Current operation status:" << currentStatus;

    if (currentStatus == QLatin1String("running")) {
        // Subscribe to progress messages if handler provided
        if (callbacks.onMessage) {
            QObject::connect(
                opProxy.get(),
                &OrgKdeKapsuleOperationInterface::Message,
                [cb = callbacks.onMessage](int type, const QString &msg, int indent) {
                    qCDebug(KAPSULE_LOG) << "Got Message signal:" << type << msg;
                    cb(static_cast<MessageType>(type), msg, indent);
                });
            qCDebug(KAPSULE_LOG) << "Subscribed to Message signal";
        }

        if (callbacks.onProgressStart) {
            QObject::connect(
                opProxy.get(),
                &OrgKdeKapsuleOperationInterface::ProgressStarted,
                callbacks.onProgressStart);
        }

        if (callbacks.onProgressUpdate) {
            QObject::connect(
                opProxy.get(),
                &OrgKdeKapsuleOperationInterface::ProgressUpdate,
                callbacks.onProgressUpdate);
        }

        if (callbacks.onProgressTextUpdate) {
            QObject::connect(
                opProxy.get(),
                &OrgKdeKapsuleOperationInterface::ProgressTextUpdate,
                callbacks.onProgressTextUpdate);
        }

        if (callbacks.onProgressComplete) {
            QObject::connect(
                opProxy.get(),
                &OrgKdeKapsuleOperationInterface::ProgressCompleted,
                callbacks.onProgressComplete);
        }

        qCDebug(KAPSULE_LOG) << "Waiting for Completed signal...";
        std::tie(success, error) = co_await qCoro(
            opProxy.get(),
            &OrgKdeKapsuleOperationInterface::Completed);
    } else {
        // Operation already finished — read result from properties
        success = (currentStatus == QLatin1String("completed"));
        error = opProxy->errorMessage();
    }

    qCDebug(KAPSULE_LOG) << "Operation finished: success=" << success << "error=" << error;
    co_return OperationResult{success, error};
}

// ============================================================================
// KapsuleClient implementation
// ============================================================================

KapsuleClient::KapsuleClient(QObject *parent)
    : QObject(parent)
    , d(std::make_unique<KapsuleClientPrivate>(this))
{
}

KapsuleClient::~KapsuleClient() = default;

bool KapsuleClient::isConnected() const
{
    return d->connected;
}

QString KapsuleClient::daemonVersion() const
{
    return d->daemonVersion;
}

QCoro::Task<QList<Container>> KapsuleClient::listContainers()
{
    if (!d->connected) {
        co_return {};
    }

    // Call D-Bus method - Container is marshalled directly
    auto reply = co_await d->interface->ListContainers();
    if (reply.isError()) {
        qCWarning(KAPSULE_LOG) << "ListContainers failed:" << reply.error().message();
        co_return {};
    }

    co_return reply.value();
}

QCoro::Task<Container> KapsuleClient::container(const QString &name)
{
    if (!d->connected) {
        co_return Container{};
    }

    auto reply = co_await d->interface->GetContainerInfo(name);
    if (reply.isError()) {
        qCWarning(KAPSULE_LOG) << "GetContainerInfo failed:" << reply.error().message();
        co_return Container{};
    }

    co_return reply.value();
}

QCoro::Task<QString> KapsuleClient::getCreateSchema()
{
    if (!d->connected) {
        co_return {};
    }

    auto reply = co_await d->interface->GetCreateSchema();
    if (reply.isError()) {
        qCWarning(KAPSULE_LOG) << "GetCreateSchema failed:" << reply.error().message();
        co_return {};
    }

    co_return reply.value();
}

QCoro::Task<QVariantMap> KapsuleClient::config()
{
    if (!d->connected) {
        co_return {{QStringLiteral("error"), QStringLiteral("Not connected")}};
    }

    auto reply = co_await d->interface->GetConfig();
    if (reply.isError()) {
        co_return {{QStringLiteral("error"), reply.error().message()}};
    }

    // Convert QMap<QString, QString> to QVariantMap
    QVariantMap result;
    const auto &config = reply.value();
    for (auto it = config.cbegin(); it != config.cend(); ++it) {
        result.insert(it.key(), it.value());
    }
    co_return result;
}

QCoro::Task<OperationResult> KapsuleClient::createContainer(
    const QString &name,
    const QString &image,
    const QVariantMap &options,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->CreateContainer(name, image, options);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    // The reply is the D-Bus object path for the operation - wait for completion
    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<OperationResult> KapsuleClient::deleteContainer(
    const QString &name,
    bool force,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->DeleteContainer(name, force);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<OperationResult> KapsuleClient::startContainer(
    const QString &name,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->StartContainer(name);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<OperationResult> KapsuleClient::stopContainer(
    const QString &name,
    bool force,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->StopContainer(name, force);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<EnterResult> KapsuleClient::prepareEnter(
    const QString &containerName,
    const QStringList &command,
    const QString &workingDirectory)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon"), {}};
    }

    auto reply = co_await d->interface->PrepareEnter(containerName, command,
                                                    workingDirectory);
    if (reply.isError()) {
        co_return {false, reply.error().message(), {}};
    }

    // EnterResult is directly returned from D-Bus now
    co_return reply.value();
}

QCoro::Task<OperationResult> KapsuleClient::refreshImages(
    const QString &image,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->RefreshImages(image);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<OperationResult> KapsuleClient::importImage(
    const QString &path,
    const QString &alias,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->ImportImage(path, alias);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

QCoro::Task<QString> KapsuleClient::listImages()
{
    if (!d->connected) {
        co_return {};
    }

    auto reply = co_await d->interface->ListImages();
    if (reply.isError()) {
        qCWarning(KAPSULE_LOG) << "ListImages failed:" << reply.error().message();
        co_return {};
    }

    co_return reply.value();
}

QCoro::Task<OperationResult> KapsuleClient::deleteImage(
    const QString &identifier,
    OperationCallbacks callbacks)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->DeleteImage(identifier);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), std::move(callbacks));
}

} // namespace Kapsule
