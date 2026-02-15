/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#ifndef KCM_CREATESCHEMAMODEL_H
#define KCM_CREATESCHEMAMODEL_H

#include <QAbstractListModel>
#include <Kapsule/Types>

class SchemaOptionsModel;

/**
 * @class CreateSchemaModel
 * @brief Top-level model exposing schema sections for a QML Repeater.
 *
 * Each row represents a schema section. The OptionsModelRole provides
 * a nested SchemaOptionsModel for iterating the section's options.
 */
class CreateSchemaModel : public QAbstractListModel
{
    Q_OBJECT

public:
    enum Roles {
        SectionIdRole = Qt::UserRole + 1,
        SectionTitleRole,
        OptionsModelRole,
    };

    explicit CreateSchemaModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    /**
     * @brief Populate the model from a parsed CreateSchema.
     */
    void setSchema(const Kapsule::CreateSchema &schema);

private:
    struct SectionData {
        QString id;
        QString title;
        SchemaOptionsModel *optionsModel = nullptr;
    };

    QList<SectionData> m_sections;
};

/**
 * @class SchemaOptionsModel
 * @brief Model for options within a single schema section.
 *
 * Provides all fields needed to render a dynamic form widget:
 * type, title, description, default value, dependencies, and
 * item format hint.
 */
class SchemaOptionsModel : public QAbstractListModel
{
    Q_OBJECT

public:
    enum Roles {
        KeyRole = Qt::UserRole + 1,
        TypeRole,
        TitleRole,
        DescriptionRole,
        DefaultValueRole,
        DependenciesRole,
        ItemFormatRole,
    };

    explicit SchemaOptionsModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = {}) const override;
    QVariant data(const QModelIndex &index, int role) const override;
    QHash<int, QByteArray> roleNames() const override;

    void setOptions(const QList<Kapsule::CreateSchemaOption> &options);

private:
    QList<Kapsule::CreateSchemaOption> m_options;
};

#endif // KCM_CREATESCHEMAMODEL_H
