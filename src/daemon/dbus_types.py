"""D-Bus type aliases and decorator wrappers for type-safe interfaces.

This module provides:
1. Type aliases that combine Python types with D-Bus signatures using Annotated
2. Wrapper decorators that extract D-Bus signatures from Annotated types

Usage:
    from .dbus_types import DBusStr, DBusBool, method, signal, dbus_property

    class MyInterface(ServiceInterface):
        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> DBusStr:
            return "1.0"

        @method()
        async def Echo(self, message: DBusStr, count: DBusInt) -> DBusStr:
            return message * count

This gives you both:
- Static type checking via Pylance/mypy (sees `str`, `int`, etc.)
- Correct D-Bus signatures at runtime (sees "s", "i", etc.)
"""

from __future__ import annotations

from typing import Annotated, get_origin, get_args

from dbus_fast.service import (
    dbus_property as _dbus_property,
    method as _method,
    signal as _signal,
)
from dbus_fast.constants import PropertyAccess


# =============================================================================
# Primitive D-Bus Type Aliases
# =============================================================================

# String types
DBusStr = Annotated[str, "s"]
"""D-Bus string type (signature: s)"""

DBusObjectPath = Annotated[str, "o"]
"""D-Bus object path type (signature: o)"""

DBusSignature = Annotated[str, "g"]
"""D-Bus signature type (signature: g)"""

# Boolean
DBusBool = Annotated[bool, "b"]
"""D-Bus boolean type (signature: b)"""

# Integer types
DBusByte = Annotated[int, "y"]
"""D-Bus byte/uint8 type (signature: y)"""

DBusInt16 = Annotated[int, "n"]
"""D-Bus int16 type (signature: n)"""

DBusUInt16 = Annotated[int, "q"]
"""D-Bus uint16 type (signature: q)"""

DBusInt32 = Annotated[int, "i"]
"""D-Bus int32 type (signature: i)"""

DBusUInt32 = Annotated[int, "u"]
"""D-Bus uint32 type (signature: u)"""

DBusInt64 = Annotated[int, "x"]
"""D-Bus int64 type (signature: x)"""

DBusUInt64 = Annotated[int, "t"]
"""D-Bus uint64 type (signature: t)"""

# Floating point
DBusDouble = Annotated[float, "d"]
"""D-Bus double type (signature: d)"""

# Unix file descriptor
DBusUnixFD = Annotated[int, "h"]
"""D-Bus Unix file descriptor type (signature: h)"""


# =============================================================================
# Common Container Type Aliases
# =============================================================================

DBusStrArray = Annotated[list[str], "as"]
"""D-Bus array of strings (signature: as)"""

DBusStrDict = Annotated[dict[str, str], "a{ss}"]
"""D-Bus dictionary string->string (signature: a{ss})"""


# =============================================================================
# Helper Functions
# =============================================================================

def extract_dbus_signature(annotation):
    """Extract D-Bus signature string from an Annotated type.

    Args:
        annotation: A type annotation, possibly Annotated[T, "sig"]

    Returns:
        The D-Bus signature string if Annotated, otherwise the original annotation
    """
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if len(args) >= 2 and isinstance(args[1], str):
            return args[1]
    return annotation


def _convert_annotations(fn):
    """Convert all Annotated types in function annotations to D-Bus signatures.

    Modifies fn.__annotations__ in place to replace Annotated[T, "sig"] with "sig".
    Handles PEP 563 stringified annotations from `from __future__ import annotations`.
    """
    import typing

    # Get type hints, evaluating any string annotations
    # We need to pass the function's globals so type aliases resolve correctly
    try:
        hints = typing.get_type_hints(fn, globalns=fn.__globals__, include_extras=True)
    except Exception:
        # Fall back to raw annotations if evaluation fails
        hints = fn.__annotations__

    new_annotations = {}
    for name, hint in hints.items():
        new_annotations[name] = extract_dbus_signature(hint)
    fn.__annotations__ = new_annotations
    return fn


# =============================================================================
# Decorator Wrappers
# =============================================================================

def dbus_property(access=PropertyAccess.READ):
    """Wrapper for dbus_fast.dbus_property that supports Annotated types.

    Usage:
        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> DBusStr:
            return "1.0"
    """
    def decorator(fn):
        _convert_annotations(fn)
        return _dbus_property(access=access)(fn)
    return decorator


def method(name: str | None = None):
    """Wrapper for dbus_fast.method that supports Annotated types.

    Usage:
        @method()
        async def Echo(self, message: DBusStr) -> DBusStr:
            return message
    """
    def decorator(fn):
        _convert_annotations(fn)
        return _method(name=name)(fn)
    return decorator


def signal():
    """Wrapper for dbus_fast.signal that supports Annotated types.

    Usage:
        @signal()
        def StatusChanged(self, status: DBusStr, code: DBusInt32) -> "(si)":
            return (status, code)
    """
    def decorator(fn):
        _convert_annotations(fn)
        return _signal()(fn)
    return decorator


# Re-export PropertyAccess for convenience
__all__ = [
    # Primitive types
    "DBusStr",
    "DBusObjectPath",
    "DBusSignature",
    "DBusBool",
    "DBusByte",
    "DBusInt16",
    "DBusUInt16",
    "DBusInt32",
    "DBusUInt32",
    "DBusInt64",
    "DBusUInt64",
    "DBusDouble",
    "DBusUnixFD",
    # Container types
    "DBusStrArray",
    "DBusStrDict",
    # Decorators
    "dbus_property",
    "method",
    "signal",
    # Re-exports
    "PropertyAccess",
]
