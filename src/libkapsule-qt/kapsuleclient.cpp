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
        ProgressHandler progress);

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
    ProgressHandler progress)
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

    // Subscribe to progress messages if handler provided
    QMetaObject::Connection messageConn;
    if (progress) {
        messageConn = QObject::connect(
            opProxy.get(),
            &OrgKdeKapsuleOperationInterface::Message,
            [progress](int type, const QString &msg, int indent) {
                qCDebug(KAPSULE_LOG) << "Got Message signal:" << type << msg;
                progress(static_cast<MessageType>(type), msg, indent);
            });
        qCDebug(KAPSULE_LOG) << "Subscribed to Message signal";
    }

    qCDebug(KAPSULE_LOG) << "Waiting for Completed signal...";
    // Await the Completed signal directly using qCoro - no polling!
    auto [success, error] = co_await qCoro(
        opProxy.get(),
        &OrgKdeKapsuleOperationInterface::Completed);

    qCDebug(KAPSULE_LOG) << "Got Completed signal: success=" << success << "error=" << error;

    // Clean up
    if (progress) {
        QObject::disconnect(messageConn);
    }

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
    ProgressHandler progress)
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
    co_return co_await d->waitForOperation(opPath.path(), progress);
}

QCoro::Task<OperationResult> KapsuleClient::deleteContainer(
    const QString &name,
    bool force,
    ProgressHandler progress)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->DeleteContainer(name, force);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), progress);
}

QCoro::Task<OperationResult> KapsuleClient::startContainer(
    const QString &name,
    ProgressHandler progress)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->StartContainer(name);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), progress);
}

QCoro::Task<OperationResult> KapsuleClient::stopContainer(
    const QString &name,
    bool force,
    ProgressHandler progress)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    auto reply = co_await d->interface->StopContainer(name, force);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    QDBusObjectPath opPath = reply.value();
    co_return co_await d->waitForOperation(opPath.path(), progress);
}

QCoro::Task<EnterResult> KapsuleClient::prepareEnter(
    const QString &containerName,
    const QStringList &command)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon"), {}};
    }

    auto reply = co_await d->interface->PrepareEnter(containerName, command);
    if (reply.isError()) {
        co_return {false, reply.error().message(), {}};
    }

    // EnterResult is directly returned from D-Bus now
    co_return reply.value();
}

} // namespace Kapsule
