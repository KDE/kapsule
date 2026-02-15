/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "createschemamodel.h"

#include <QJsonValue>

// =============================================================================
// CreateSchemaModel (sections)
// =============================================================================

CreateSchemaModel::CreateSchemaModel(QObject *parent)
    : QAbstractListModel(parent)
{
}

int CreateSchemaModel::rowCount(const QModelIndex &parent) const
{
    if (parent.isValid()) {
        return 0;
    }
    return static_cast<int>(m_sections.size());
}

QVariant CreateSchemaModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.row() < 0 || index.row() >= m_sections.size()) {
        return {};
    }

    const auto &section = m_sections.at(index.row());

    switch (role) {
    case SectionIdRole:
        return section.id;
    case SectionTitleRole:
        return section.title;
    case OptionsModelRole:
        return QVariant::fromValue(section.optionsModel);
    default:
        return {};
    }
}

QHash<int, QByteArray> CreateSchemaModel::roleNames() const
{
    return {
        {SectionIdRole, "sectionId"},
        {SectionTitleRole, "sectionTitle"},
        {OptionsModelRole, "optionsModel"},
    };
}

void CreateSchemaModel::setSchema(const Kapsule::CreateSchema &schema)
{
    beginResetModel();

    // Clean up old option models
    for (auto &section : m_sections) {
        delete section.optionsModel;
    }
    m_sections.clear();

    for (const auto &schemaSection : schema.sections) {
        SectionData section;
        section.id = schemaSection.id;
        section.title = schemaSection.title;
        section.optionsModel = new SchemaOptionsModel(this);
        section.optionsModel->setOptions(schemaSection.options);
        m_sections.append(section);
    }

    endResetModel();
}

// =============================================================================
// SchemaOptionsModel (options within a section)
// =============================================================================

SchemaOptionsModel::SchemaOptionsModel(QObject *parent)
    : QAbstractListModel(parent)
{
}

int SchemaOptionsModel::rowCount(const QModelIndex &parent) const
{
    if (parent.isValid()) {
        return 0;
    }
    return static_cast<int>(m_options.size());
}

QVariant SchemaOptionsModel::data(const QModelIndex &index, int role) const
{
    if (!index.isValid() || index.row() < 0 || index.row() >= m_options.size()) {
        return {};
    }

    const auto &opt = m_options.at(index.row());

    switch (role) {
    case KeyRole:
        return opt.key;
    case TypeRole:
        return opt.type;
    case TitleRole:
        return opt.title;
    case DescriptionRole:
        return opt.description;
    case DefaultValueRole:
        return opt.defaultValue.toVariant();
    case DependenciesRole:
        return opt.dependencies;
    case ItemFormatRole:
        return opt.itemFormat;
    default:
        return {};
    }
}

QHash<int, QByteArray> SchemaOptionsModel::roleNames() const
{
    return {
        {KeyRole, "key"},
        {TypeRole, "type"},
        {TitleRole, "title"},
        {DescriptionRole, "description"},
        {DefaultValueRole, "defaultValue"},
        {DependenciesRole, "dependencies"},
        {ItemFormatRole, "itemFormat"},
    };
}

void SchemaOptionsModel::setOptions(const QList<Kapsule::CreateSchemaOption> &options)
{
    beginResetModel();
    m_options = options;
    endResetModel();
}
