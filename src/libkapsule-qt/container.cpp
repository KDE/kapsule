/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "container.h"

#include <QSharedData>

namespace Kapsule {

// ============================================================================
// ContainerData (implicitly shared)
// ============================================================================

class ContainerData : public QSharedData
{
public:
    ContainerData() = default;

    ContainerData(const QString &name, Container::State state,
                  const QString &image, ContainerMode mode,
                  const QDateTime &created)
        : name(name)
        , state(state)
        , image(image)
        , mode(mode)
        , created(created)
    {
    }

    QString name;
    Container::State state = Container::State::Unknown;
    QString image;
    ContainerMode mode = ContainerMode::Default;
    QDateTime created;
};

// ============================================================================
// Container implementation
// ============================================================================

Container::Container()
    : d(new ContainerData)
{
}

Container::Container(const QString &name)
    : d(new ContainerData)
{
    d->name = name;
}

Container::Container(const Container &other) = default;
Container::Container(Container &&other) noexcept = default;
Container::~Container() = default;
Container &Container::operator=(const Container &other) = default;
Container &Container::operator=(Container &&other) noexcept = default;

bool Container::isValid() const
{
    return !d->name.isEmpty();
}

QString Container::name() const
{
    return d->name;
}

Container::State Container::state() const
{
    return d->state;
}

QString Container::image() const
{
    return d->image;
}

ContainerMode Container::mode() const
{
    return d->mode;
}

QDateTime Container::created() const
{
    return d->created;
}

bool Container::isRunning() const
{
    return d->state == State::Running;
}

bool Container::operator==(const Container &other) const
{
    return d->name == other.d->name;
}

bool Container::operator!=(const Container &other) const
{
    return !(*this == other);
}

// ============================================================================
// D-Bus streaming operators
// ============================================================================

QDBusArgument &operator<<(QDBusArgument &arg, const Container &container)
{
    arg.beginStructure();
    arg << container.d->name
        << QString::fromLatin1(QMetaEnum::fromType<Container::State>().valueToKey(static_cast<int>(container.d->state)))
        << container.d->image
        << container.d->created.toString(Qt::ISODate)
        << containerModeToString(container.d->mode);
    arg.endStructure();
    return arg;
}

const QDBusArgument &operator>>(const QDBusArgument &arg, Container &container)
{
    QString name, status, image, created, mode;
    arg.beginStructure();
    arg >> name >> status >> image >> created >> mode;
    arg.endStructure();

    container.d->name = name;
    container.d->image = image;
    container.d->mode = containerModeFromString(mode);
    container.d->created = QDateTime::fromString(created, Qt::ISODate);

    bool ok = false;
    int value = QMetaEnum::fromType<Container::State>().keyToValue(status.toLatin1().constData(), &ok);
    container.d->state = ok ? static_cast<Container::State>(value) : Container::State::Unknown;

    return arg;
}

} // namespace Kapsule
