/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "kcm_kapsule.h"
#include "containerlistmodel.h"
#include "createschemamodel.h"

#include <Kapsule/KapsuleClient>
#include <Kapsule/Types>

#include <KLocalizedString>
#include <KPluginFactory>

#include <qcoro/qcorocore.h>

K_PLUGIN_CLASS_WITH_JSON(KCMKapsule, "kcm_kapsule.json")

KCMKapsule::KCMKapsule(QObject *parent, const KPluginMetaData &data)
    : KQuickConfigModule(parent, data)
    , m_client(std::make_unique<Kapsule::KapsuleClient>(this))
    , m_containerModel(new ContainerListModel(this))
    , m_schemaModel(new CreateSchemaModel(this))
{
    setButtons(NoAdditionalButton);

    connect(m_client.get(), &Kapsule::KapsuleClient::connectedChanged, this, &KCMKapsule::connectedChanged);
    connect(m_client.get(), &Kapsule::KapsuleClient::containerStateChanged, this, [this](const QString &) {
        // Re-fetch the full list when any container state changes
        refresh();
    });
    connect(m_client.get(), &Kapsule::KapsuleClient::errorOccurred, this, [this](const QString &msg) {
        setStatusMessage(msg);
        Q_EMIT operationFailed(msg);
    });
}

KCMKapsule::~KCMKapsule() = default;

ContainerListModel *KCMKapsule::containerModel() const
{
    return m_containerModel;
}

CreateSchemaModel *KCMKapsule::schemaModel() const
{
    return m_schemaModel;
}

bool KCMKapsule::isLoading() const
{
    return m_loading;
}

bool KCMKapsule::isConnected() const
{
    return m_client->isConnected();
}

QString KCMKapsule::statusMessage() const
{
    return m_statusMessage;
}

QString KCMKapsule::defaultImage() const
{
    return m_defaultImage;
}

void KCMKapsule::setLoading(bool loading)
{
    if (m_loading == loading) {
        return;
    }
    m_loading = loading;
    Q_EMIT loadingChanged();
}

void KCMKapsule::setStatusMessage(const QString &message)
{
    if (m_statusMessage == message) {
        return;
    }
    m_statusMessage = message;
    Q_EMIT statusMessageChanged();
}

void KCMKapsule::load()
{
    KQuickConfigModule::load();
    refresh();
}

void KCMKapsule::refresh()
{
    if (!m_client->isConnected()) {
        setStatusMessage(i18n("Cannot connect to kapsule-daemon. Is the service running?"));
        return;
    }

    setLoading(true);
    setStatusMessage({});

    // Fetch containers, schema, and config concurrently
    auto fetchAll = [this]() -> QCoro::Task<> {
        // Fetch containers
        auto containers = co_await m_client->listContainers();
        m_containerModel->setContainers(containers);

        // Fetch schema (only once; it doesn't change at runtime)
        static bool schemaLoaded = false;
        if (!schemaLoaded) {
            auto schemaJson = co_await m_client->getCreateSchema();
            if (!schemaJson.isEmpty()) {
                auto schema = Kapsule::parseCreateSchema(schemaJson);
                if (schema.version > 0) {
                    m_schemaModel->setSchema(schema);
                    schemaLoaded = true;
                }
            }
        }

        // Fetch config for default image
        auto config = co_await m_client->config();
        auto newDefaultImage = config.value(QStringLiteral("default_image")).toString();
        if (m_defaultImage != newDefaultImage) {
            m_defaultImage = newDefaultImage;
            Q_EMIT defaultImageChanged();
        }

        setLoading(false);
    };

    // Fire and forget — the coroutine updates model/properties as it completes
    fetchAll();
}

void KCMKapsule::createContainer(const QString &name, const QString &image,
                                 const QVariantMap &options)
{
    if (name.isEmpty()) {
        setStatusMessage(i18n("Container name is required."));
        Q_EMIT operationFailed(m_statusMessage);
        return;
    }

    setLoading(true);
    setStatusMessage(i18n("Creating container %1…", name));

    auto doCreate = [this, name, image, options]() -> QCoro::Task<> {
        auto result = co_await m_client->createContainer(name, image, options);

        if (result.success) {
            setStatusMessage({});
            Q_EMIT containerCreated();
            refresh();
        } else {
            setStatusMessage(result.error);
            setLoading(false);
            Q_EMIT operationFailed(result.error);
        }
    };

    doCreate();
}

void KCMKapsule::deleteContainer(const QString &name)
{
    setLoading(true);
    setStatusMessage(i18n("Deleting container %1…", name));

    auto doDelete = [this, name]() -> QCoro::Task<> {
        auto result = co_await m_client->deleteContainer(name, true);

        if (result.success) {
            setStatusMessage({});
            refresh();
        } else {
            setStatusMessage(result.error);
            setLoading(false);
            Q_EMIT operationFailed(result.error);
        }
    };

    doDelete();
}

void KCMKapsule::startContainer(const QString &name)
{
    setLoading(true);
    setStatusMessage(i18n("Starting container %1…", name));

    auto doStart = [this, name]() -> QCoro::Task<> {
        auto result = co_await m_client->startContainer(name);

        if (result.success) {
            setStatusMessage({});
            refresh();
        } else {
            setStatusMessage(result.error);
            setLoading(false);
            Q_EMIT operationFailed(result.error);
        }
    };

    doStart();
}

void KCMKapsule::stopContainer(const QString &name)
{
    setLoading(true);
    setStatusMessage(i18n("Stopping container %1…", name));

    auto doStop = [this, name]() -> QCoro::Task<> {
        auto result = co_await m_client->stopContainer(name);

        if (result.success) {
            setStatusMessage({});
            refresh();
        } else {
            setStatusMessage(result.error);
            setLoading(false);
            Q_EMIT operationFailed(result.error);
        }
    };

    doStop();
}

#include "kcm_kapsule.moc"
