/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#ifndef KCM_KAPSULE_H
#define KCM_KAPSULE_H

#include <KQuickConfigModule>
#include <memory>

namespace Kapsule {
class KapsuleClient;
}

class ContainerListModel;
class CreateSchemaModel;

/**
 * @class KCMKapsule
 * @brief KDE System Settings module for managing Kapsule containers.
 *
 * Provides a container list view and a schema-driven container creation
 * form.  All state comes from the kapsule-daemon over D-Bus — there is
 * no KConfigXT backing store.
 */
class KCMKapsule : public KQuickConfigModule
{
    Q_OBJECT

    Q_PROPERTY(ContainerListModel *containerModel READ containerModel CONSTANT)
    Q_PROPERTY(CreateSchemaModel *schemaModel READ schemaModel CONSTANT)
    Q_PROPERTY(bool loading READ isLoading NOTIFY loadingChanged)
    Q_PROPERTY(bool connected READ isConnected NOTIFY connectedChanged)
    Q_PROPERTY(QString statusMessage READ statusMessage NOTIFY statusMessageChanged)
    Q_PROPERTY(QString defaultImage READ defaultImage NOTIFY defaultImageChanged)

public:
    explicit KCMKapsule(QObject *parent, const KPluginMetaData &data);
    ~KCMKapsule() override;

    ContainerListModel *containerModel() const;
    CreateSchemaModel *schemaModel() const;
    bool isLoading() const;
    bool isConnected() const;
    QString statusMessage() const;
    QString defaultImage() const;

    /**
     * @brief Refresh the container list and schema from the daemon.
     */
    Q_INVOKABLE void refresh();

    /**
     * @brief Create a new container with schema-driven options.
     * @param name Container name.
     * @param image Base image (empty for default).
     * @param options QVariantMap of non-default option values.
     */
    Q_INVOKABLE void createContainer(const QString &name, const QString &image,
                                     const QVariantMap &options);

    /**
     * @brief Delete a container.
     * @param name Container name.
     */
    Q_INVOKABLE void deleteContainer(const QString &name);

    /**
     * @brief Start a stopped container.
     * @param name Container name.
     */
    Q_INVOKABLE void startContainer(const QString &name);

    /**
     * @brief Stop a running container.
     * @param name Container name.
     */
    Q_INVOKABLE void stopContainer(const QString &name);

public Q_SLOTS:
    void load() override;

Q_SIGNALS:
    void loadingChanged();
    void connectedChanged();
    void statusMessageChanged();
    void defaultImageChanged();
    void containerCreated();
    void operationFailed(const QString &message);

private:
    void setLoading(bool loading);
    void setStatusMessage(const QString &message);

    std::unique_ptr<Kapsule::KapsuleClient> m_client;
    ContainerListModel *m_containerModel = nullptr;
    CreateSchemaModel *m_schemaModel = nullptr;
    bool m_loading = false;
    QString m_statusMessage;
    QString m_defaultImage;
};

#endif // KCM_KAPSULE_H
