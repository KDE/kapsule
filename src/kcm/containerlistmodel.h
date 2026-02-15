/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#ifndef KCM_CONTAINERLISTMODEL_H
#define KCM_CONTAINERLISTMODEL_H

#include <QAbstractListModel>
#include <Kapsule/Container>

/**
 * @class ContainerListModel
 * @brief List model exposing Kapsule containers to QML.
 */
class ContainerListModel : public QAbstractListModel
{
    Q_OBJECT
    Q_PROPERTY(int count READ count NOTIFY countChanged)

public:
    enum Roles {
        NameRole = Qt::UserRole + 1,
        StateRole,
        StateStringRole,
        ImageRole,
        ModeRole,
        CreatedRole,
    };

    explicit ContainerListModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    int count() const;

    /**
     * @brief Replace the model contents with a new container list.
     */
    void setContainers(const QList<Kapsule::Container> &containers);

Q_SIGNALS:
    void countChanged();

private:
    static QString stateToString(Kapsule::Container::State state);

    QList<Kapsule::Container> m_containers;
};

#endif // KCM_CONTAINERLISTMODEL_H
