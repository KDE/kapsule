# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Custom D-Bus type aliases for Kapsule.

This module provides Kapsule-specific D-Bus types with C++ metadata for
introspection XML generation. Standard D-Bus types should be imported
directly from dbus_fast.annotations.

Usage:
    from dbus_fast.annotations import DBusStr, DBusBool, DBusSignature
    from dbus_fast.service import dbus_method, dbus_signal, dbus_property
    from .dbus_types import DBusContainer, DBusContainerList, CppType
"""

from __future__ import annotations

from typing import Annotated

from dbus_fast.annotations import DBusSignature


class CppType:
    """Marker class for specifying the corresponding C++ type name.
    
    Use as additional metadata in Annotated types to specify what C++ type
    should be used in the D-Bus introspection XML annotations.
    
    Example:
        DBusContainer = Annotated[
            tuple[str, str, str, str, str], 
            DBusSignature("(sssss)"), 
            CppType("Kapsule::Container")
        ]
    """
    def __init__(self, cpp_type: str) -> None:
        self.cpp_type = cpp_type
    
    def __repr__(self) -> str:
        return f"CppType({self.cpp_type!r})"


# =============================================================================
# Convenience Type Aliases (not in dbus-fast upstream)
# =============================================================================

DBusStrArray = Annotated[list[str], DBusSignature("as")]
"""D-Bus array of strings (signature: as)"""

DBusStrDict = Annotated[dict[str, str], DBusSignature("a{ss}")]
"""D-Bus dictionary string->string (signature: a{ss})"""

DBusVariantDict = Annotated[dict[str, object], DBusSignature("a{sv}")]
"""D-Bus variant dictionary (signature: a{sv})"""


# =============================================================================
# Kapsule Composite Types
# =============================================================================
# These types map to custom C++ classes in libkapsule-qt that have proper
# QDBusArgument streaming operators defined.

DBusContainer = Annotated[
    tuple[str, str, str, str, str], DBusSignature("(sssss)"), CppType("Kapsule::Container")
]
"""Container info tuple: (name, status, image, created, mode)"""

DBusContainerList = Annotated[
    list[tuple[str, str, str, str, str]], DBusSignature("a(sssss)"), CppType("QList<Kapsule::Container>")
]
"""List of container info tuples"""

DBusEnterResult = Annotated[
    tuple[bool, str, list[str]], DBusSignature("(bsas)"), CppType("Kapsule::EnterResult")
]
"""PrepareEnter result: (success, error_message, command_array)"""


__all__ = [
    # Convenience types
    "DBusStrArray",
    "DBusStrDict",
    "DBusVariantDict",
    # Kapsule composite types
    "DBusContainer",
    "DBusContainerList",
    "DBusEnterResult",
    # Metadata
    "CppType",
]
