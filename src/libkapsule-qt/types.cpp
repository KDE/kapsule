/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "types.h"
#include "container.h"
#include <QDBusMetaType>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>

namespace Kapsule {

// =============================================================================
// CreateSchemaOption helpers
// =============================================================================

QString CreateSchemaOption::cliFlag() const
{
    // Underscores → dashes: "mount_home" → "mount-home"
    QString flag = key;
    flag.replace(QLatin1Char('_'), QLatin1Char('-'));
    return flag;
}

bool CreateSchemaOption::defaultsToTrue() const
{
    return defaultValue.isBool() && defaultValue.toBool();
}

// =============================================================================
// CreateSchema helpers
// =============================================================================

QList<CreateSchemaOption> CreateSchema::allOptions() const
{
    QList<CreateSchemaOption> result;
    for (const auto &section : sections) {
        result.append(section.options);
    }
    return result;
}

std::optional<CreateSchemaOption> CreateSchema::option(const QString &key) const
{
    for (const auto &section : sections) {
        for (const auto &opt : section.options) {
            if (opt.key == key) {
                return opt;
            }
        }
    }
    return std::nullopt;
}

// =============================================================================
// Schema parser
// =============================================================================

CreateSchema parseCreateSchema(const QString &json)
{
    CreateSchema schema;

    QJsonParseError parseError;
    QJsonDocument doc = QJsonDocument::fromJson(json.toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !doc.isObject()) {
        return schema;
    }

    QJsonObject root = doc.object();
    schema.version = root.value(QStringLiteral("version")).toInt();

    const QJsonArray sections = root.value(QStringLiteral("sections")).toArray();
    for (const QJsonValue &sectionVal : sections) {
        QJsonObject sectionObj = sectionVal.toObject();

        CreateSchemaSection section;
        section.id = sectionObj.value(QStringLiteral("id")).toString();
        section.title = sectionObj.value(QStringLiteral("title")).toString();

        const QJsonArray options = sectionObj.value(QStringLiteral("options")).toArray();
        for (const QJsonValue &optVal : options) {
            QJsonObject optObj = optVal.toObject();

            CreateSchemaOption opt;
            opt.key = optObj.value(QStringLiteral("key")).toString();
            opt.type = optObj.value(QStringLiteral("type")).toString();
            opt.title = optObj.value(QStringLiteral("title")).toString();
            opt.description = optObj.value(QStringLiteral("description")).toString();
            opt.defaultValue = optObj.value(QStringLiteral("default"));

            // Parse "items.format" hint if present (e.g. "directory-path")
            if (optObj.contains(QStringLiteral("items"))) {
                QJsonObject itemsObj = optObj.value(QStringLiteral("items")).toObject();
                opt.itemFormat = itemsObj.value(QStringLiteral("format")).toString();
            }

            // Parse "requires" dict if present
            if (optObj.contains(QStringLiteral("requires"))) {
                QJsonObject reqObj = optObj.value(QStringLiteral("requires")).toObject();
                for (auto it = reqObj.begin(); it != reqObj.end(); ++it) {
                    opt.dependencies.insert(it.key(), it.value().toVariant());
                }
            }

            section.options.append(opt);
        }

        schema.sections.append(section);
    }

    return schema;
}

// D-Bus argument streaming for EnterResult (bsas)
QDBusArgument &operator<<(QDBusArgument &arg, const EnterResult &result)
{
    arg.beginStructure();
    arg << result.success << result.error << result.execArgs;
    arg.endStructure();
    return arg;
}

const QDBusArgument &operator>>(const QDBusArgument &arg, EnterResult &result)
{
    arg.beginStructure();
    arg >> result.success >> result.error >> result.execArgs;
    arg.endStructure();
    return arg;
}

void registerDBusTypes()
{
    static bool registered = false;
    if (registered) {
        return;
    }
    registered = true;

    qDBusRegisterMetaType<Container>();
    qDBusRegisterMetaType<QList<Container>>();
    qDBusRegisterMetaType<EnterResult>();
    qDBusRegisterMetaType<QMap<QString, QString>>();
}

} // namespace Kapsule

// This file exists to provide the moc-generated staticMetaObject
// for the Kapsule namespace (Q_NAMESPACE_EXPORT).
// The actual moc output is included below.

#include "moc_types.cpp"
