/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "kapsuleclient.h"
#include "container.h"

#include <QDBusConnection>
#include <QDBusInterface>
#include <QDBusPendingCall>
#include <QDBusPendingReply>
#include <QDebug>
#include <QFutureInterface>

namespace Kapsule {

// ============================================================================
// Private implementation
// ============================================================================

class KapsuleClientPrivate
{
public:
    explicit KapsuleClientPrivate(KapsuleClient *q);
    ~KapsuleClientPrivate();

    void connectToDaemon();
    void handleContainersChanged();

    KapsuleClient *q_ptr;
    QDBusInterface *dbusInterface = nullptr;
    QList<Container> containerList;
    bool connected = false;
};

KapsuleClientPrivate::KapsuleClientPrivate(KapsuleClient *q)
    : q_ptr(q)
{
    connectToDaemon();
}

KapsuleClientPrivate::~KapsuleClientPrivate()
{
    delete dbusInterface;
}

void KapsuleClientPrivate::connectToDaemon()
{
    // TODO: Connect to org.kde.kapsule D-Bus service
    // For now, this is a stub implementation

    // Try to connect to session bus first, fall back to system bus
    QDBusConnection bus = QDBusConnection::sessionBus();

    dbusInterface = new QDBusInterface(
        QStringLiteral("org.kde.kapsule"),
        QStringLiteral("/org/kde/kapsule"),
        QStringLiteral("org.kde.kapsule.Manager"),
        bus
    );

    if (dbusInterface->isValid()) {
        connected = true;
        Q_EMIT q_ptr->connectedChanged(true);

        // Connect to signals
        bus.connect(
            QStringLiteral("org.kde.kapsule"),
            QStringLiteral("/org/kde/kapsule"),
            QStringLiteral("org.kde.kapsule.Manager"),
            QStringLiteral("ContainersChanged"),
            q_ptr,
            SLOT(refresh())
        );
    } else {
        qDebug() << "KapsuleClient: Could not connect to kapsule-daemon";
        connected = false;
    }
}

void KapsuleClientPrivate::handleContainersChanged()
{
    Q_EMIT q_ptr->containersChanged();
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

QList<Container> KapsuleClient::containers() const
{
    return d->containerList;
}

Container KapsuleClient::container(const QString &name) const
{
    for (const auto &c : d->containerList) {
        if (c.name() == name) {
            return c;
        }
    }
    return Container();
}

void KapsuleClient::refresh()
{
    if (!d->connected) {
        return;
    }

    // TODO: Call D-Bus method to get container list
    // For now, this is a stub
    qDebug() << "KapsuleClient::refresh() - stub implementation";

    d->handleContainersChanged();
}

QFuture<bool> KapsuleClient::createContainer(const QString &name,
                                              const QString &image,
                                              const QStringList &features)
{
    QFutureInterface<bool> futureInterface;
    futureInterface.reportStarted();

    if (!d->connected) {
        Q_EMIT errorOccurred(tr("Not connected to kapsule-daemon"));
        futureInterface.reportResult(false);
        futureInterface.reportFinished();
        return futureInterface.future();
    }

    // TODO: Call D-Bus method to create container
    qDebug() << "KapsuleClient::createContainer() - stub implementation"
             << name << image << features;

    futureInterface.reportResult(false);
    futureInterface.reportFinished();
    return futureInterface.future();
}

QFuture<bool> KapsuleClient::startContainer(const QString &name)
{
    QFutureInterface<bool> futureInterface;
    futureInterface.reportStarted();

    if (!d->connected) {
        Q_EMIT errorOccurred(tr("Not connected to kapsule-daemon"));
        futureInterface.reportResult(false);
        futureInterface.reportFinished();
        return futureInterface.future();
    }

    // TODO: Call D-Bus method to start container
    qDebug() << "KapsuleClient::startContainer() - stub implementation" << name;

    futureInterface.reportResult(false);
    futureInterface.reportFinished();
    return futureInterface.future();
}

QFuture<bool> KapsuleClient::stopContainer(const QString &name)
{
    QFutureInterface<bool> futureInterface;
    futureInterface.reportStarted();

    if (!d->connected) {
        Q_EMIT errorOccurred(tr("Not connected to kapsule-daemon"));
        futureInterface.reportResult(false);
        futureInterface.reportFinished();
        return futureInterface.future();
    }

    // TODO: Call D-Bus method to stop container
    qDebug() << "KapsuleClient::stopContainer() - stub implementation" << name;

    futureInterface.reportResult(false);
    futureInterface.reportFinished();
    return futureInterface.future();
}

QFuture<bool> KapsuleClient::removeContainer(const QString &name, bool force)
{
    QFutureInterface<bool> futureInterface;
    futureInterface.reportStarted();

    if (!d->connected) {
        Q_EMIT errorOccurred(tr("Not connected to kapsule-daemon"));
        futureInterface.reportResult(false);
        futureInterface.reportFinished();
        return futureInterface.future();
    }

    // TODO: Call D-Bus method to remove container
    qDebug() << "KapsuleClient::removeContainer() - stub implementation"
             << name << force;

    futureInterface.reportResult(false);
    futureInterface.reportFinished();
    return futureInterface.future();
}

void KapsuleClient::enterContainer(const QString &name, const QString &command)
{
    if (!d->connected) {
        Q_EMIT errorOccurred(tr("Not connected to kapsule-daemon"));
        return;
    }

    // TODO: Call D-Bus method to enter container
    // This would typically open a terminal with the container shell
    qDebug() << "KapsuleClient::enterContainer() - stub implementation"
             << name << command;
}

} // namespace Kapsule

#include "moc_kapsuleclient.cpp"
