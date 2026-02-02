/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "kapsuleclient.h"
#include "kapsule_debug.h"
#include "kapsulemanagerinterface.h"
#include "types.h"

#include <QDBusConnection>
#include <QDBusPendingReply>

#include <qcoro/qcorodbuspendingreply.h>
#include <qcoro/qcorotimer.h>

namespace Kapsule {

// ============================================================================
// Private implementation
// ============================================================================

class KapsuleClientPrivate
{
public:
    explicit KapsuleClientPrivate(KapsuleClient *q);

    void connectToDaemon();
    void subscribeToSignals();
    QCoro::Task<OperationResult> waitForOperation(const QString &opId, ProgressHandler progress);

    KapsuleClient *q_ptr;
    std::unique_ptr<OrgKdeKapsuleManagerInterface> interface;
    QString daemonVersion;
    bool connected = false;
};

KapsuleClientPrivate::KapsuleClientPrivate(KapsuleClient *q)
    : q_ptr(q)
{
    // Register D-Bus types before any D-Bus operations
    registerDBusTypes();
    connectToDaemon();
}

void KapsuleClientPrivate::connectToDaemon()
{
    interface = std::make_unique<OrgKdeKapsuleManagerInterface>(
        QStringLiteral("org.kde.kapsule"),
        QStringLiteral("/org/kde/kapsule"),
        QDBusConnection::systemBus()
    );

    if (interface->isValid()) {
        connected = true;
        daemonVersion = interface->version();
        qCDebug(KAPSULE_LOG) << "Connected to kapsule-daemon version" << daemonVersion;
        subscribeToSignals();
    } else {
        qCWarning(KAPSULE_LOG) << "Failed to connect to kapsule-daemon:"
                               << interface->lastError().message();
        connected = false;
    }
}

void KapsuleClientPrivate::subscribeToSignals()
{
    // We subscribe to signals per-operation in waitForOperation()
}

QCoro::Task<OperationResult> KapsuleClientPrivate::waitForOperation(
    const QString &opId,
    ProgressHandler progress)
{
    struct OpState {
        bool completed = false;
        bool success = false;
        QString error;
    };
    auto state = std::make_shared<OpState>();

    // Connect to signals for this operation
    QMetaObject::Connection completedConn;
    QMetaObject::Connection messageConn;

    completedConn = QObject::connect(
        interface.get(),
        &OrgKdeKapsuleManagerInterface::OperationCompleted,
        [state, opId](const QString &id, bool ok, const QString &msg) {
            if (id == opId) {
                state->completed = true;
                state->success = ok;
                state->error = msg;
            }
        });

    if (progress) {
        messageConn = QObject::connect(
            interface.get(),
            &OrgKdeKapsuleManagerInterface::OperationMessage,
            [progress, opId](const QString &id, int type, const QString &msg, int indent) {
                if (id == opId) {
                    progress(static_cast<MessageType>(type), msg, indent);
                }
            });
    }

    // Poll until completed (QCoro doesn't have a TaskCompletionSource equivalent yet)
    // This is a simple approach; could be improved with proper signal-to-coroutine
    while (!state->completed) {
        co_await QCoro::sleepFor(std::chrono::milliseconds(50));
    }

    QObject::disconnect(completedConn);
    if (progress) {
        QObject::disconnect(messageConn);
    }

    co_return OperationResult{state->success, state->error};
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
    ContainerMode mode,
    ProgressHandler progress)
{
    if (!d->connected) {
        co_return {false, QStringLiteral("Not connected to daemon")};
    }

    bool sessionMode = (mode == ContainerMode::Session || mode == ContainerMode::DbusMux);
    bool dbusMux = (mode == ContainerMode::DbusMux);

    auto reply = co_await d->interface->CreateContainer(name, image, sessionMode, dbusMux);
    if (reply.isError()) {
        co_return {false, reply.error().message()};
    }

    // The reply is the operation ID - wait for completion
    QString opId = reply.value();
    co_return co_await d->waitForOperation(opId, progress);
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

    QString opId = reply.value();
    co_return co_await d->waitForOperation(opId, progress);
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

    QString opId = reply.value();
    co_return co_await d->waitForOperation(opId, progress);
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

    QString opId = reply.value();
    co_return co_await d->waitForOperation(opId, progress);
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
