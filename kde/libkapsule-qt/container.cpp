/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: LGPL-2.1-or-later
*/

#include "container.h"

#include <QSharedData>

namespace Kapsule {

// ============================================================================
// Private data class
// ============================================================================

class ContainerData : public QSharedData
{
public:
    ContainerData() = default;

    explicit ContainerData(const QString &containerName)
        : name(containerName)
    {
    }

    ContainerData(const ContainerData &other) = default;

    QString name;
    Container::State state = Container::State::Unknown;
    QString image;
    QStringList features;
    QDateTime createdAt;
};

// ============================================================================
// Container implementation
// ============================================================================

Container::Container()
    : d(new ContainerData())
{
}

Container::Container(const QString &name)
    : d(new ContainerData(name))
{
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

QStringList Container::features() const
{
    return d->features;
}

QDateTime Container::createdAt() const
{
    return d->createdAt;
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

} // namespace Kapsule
