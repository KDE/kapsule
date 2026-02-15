/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "containerlistmodel.h"

#include <KLocalizedString>

using namespace Kapsule;

ContainerListModel::ContainerListModel(QObject *parent)
    : QAbstractListModel(parent)
{
}

int ContainerListModel::rowCount(const QModelIndex &parent) const
{
    if (parent.isValid()) {
        return 0;
    }
    return static_cast<int>(m_containers.size());
}

QVariant ContainerListModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.row() < 0 || index.row() >= m_containers.size()) {
        return {};
    }

    const auto &c = m_containers.at(index.row());

    switch (role) {
    case NameRole:
        return c.name();
    case StateRole:
        return static_cast<int>(c.state());
    case StateStringRole:
        return stateToString(c.state());
    case ImageRole:
        return c.image();
    case ModeRole:
        return containerModeToString(c.mode());
    case CreatedRole:
        return c.created();
    default:
        return {};
    }
}

QHash<int, QByteArray> ContainerListModel::roleNames() const
{
    return {
        {NameRole, "name"},
        {StateRole, "containerState"},
        {StateStringRole, "stateString"},
        {ImageRole, "image"},
        {ModeRole, "mode"},
        {CreatedRole, "created"},
    };
}

int ContainerListModel::count() const
{
    return static_cast<int>(m_containers.size());
}

void ContainerListModel::setContainers(const QList<Container> &containers)
{
    beginResetModel();
    m_containers = containers;
    endResetModel();
    Q_EMIT countChanged();
}

QString ContainerListModel::stateToString(Container::State state)
{
    switch (state) {
    case Container::State::Running:
        return i18n("Running");
    case Container::State::Stopped:
        return i18n("Stopped");
    case Container::State::Starting:
        return i18n("Starting");
    case Container::State::Stopping:
        return i18n("Stopping");
    case Container::State::Error:
        return i18n("Error");
    default:
        return i18n("Unknown");
    }
}
