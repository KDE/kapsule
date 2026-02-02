#!/usr/bin/env python3
"""Generate D-Bus introspection XML from the Kapsule service interface.

This script creates the introspection XML by parsing the Python source files
using AST, without importing them (avoiding dbus-fast dependency at build time).

Usage:
    python generate_dbus_introspection.py > org.kde.kapsule.xml
    python generate_dbus_introspection.py --output org.kde.kapsule.xml
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from dataclasses import dataclass


@dataclass
class DBusMethod:
    """Represents a D-Bus method."""
    name: str
    args: list[tuple[str, str]]  # (name, dbus_type)
    return_type: str | None
    doc: str | None = None


@dataclass  
class DBusSignal:
    """Represents a D-Bus signal."""
    name: str
    args: list[tuple[str, str]]  # (name, dbus_type)
    doc: str | None = None


@dataclass
class DBusProperty:
    """Represents a D-Bus property."""
    name: str
    type: str
    access: str  # "read", "write", "readwrite"
    doc: str | None = None


# Mapping of type aliases to D-Bus signatures (from dbus_types.py)
TYPE_ALIASES = {
    "DBusStr": "s",
    "DBusObjectPath": "o",
    "DBusSignature": "g",
    "DBusBool": "b",
    "DBusByte": "y",
    "DBusInt16": "n",
    "DBusUInt16": "q",
    "DBusInt32": "i",
    "DBusUInt32": "u",
    "DBusInt64": "x",
    "DBusUInt64": "t",
    "DBusDouble": "d",
    "DBusUnixFD": "h",
    "DBusStrArray": "as",
    "DBusStrDict": "a{ss}",
    # Basic Python types
    "str": "s",
    "bool": "b",
    "int": "i",
    "float": "d",
}


def extract_annotated_signature(annotation: ast.expr) -> str | None:
    """Extract D-Bus signature from an Annotated[...] type expression."""
    if isinstance(annotation, ast.Subscript):
        # Check if it's Annotated[type, "signature"]
        if isinstance(annotation.value, ast.Name) and annotation.value.id == "Annotated":
            if isinstance(annotation.slice, ast.Tuple):
                elts = annotation.slice.elts
                if len(elts) >= 2:
                    # The second element should be the signature string
                    sig_elt = elts[1]
                    if isinstance(sig_elt, ast.Constant) and isinstance(sig_elt.value, str):
                        return sig_elt.value
    return None


def resolve_type(annotation: ast.expr | None) -> str:
    """Resolve an AST type annotation to a D-Bus signature."""
    if annotation is None:
        return "v"
    
    # Check for Annotated types first
    sig = extract_annotated_signature(annotation)
    if sig:
        return sig
    
    # Handle Name types (simple type references)
    if isinstance(annotation, ast.Name):
        type_name = annotation.id
        return TYPE_ALIASES.get(type_name, "v")
    
    # Handle Attribute types (e.g., typing.List)
    if isinstance(annotation, ast.Attribute):
        return "v"
    
    # Handle Subscript types (e.g., list[str], dict[str, str])
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            container = annotation.value.id
            
            if container in ("list", "List"):
                if isinstance(annotation.slice, ast.Name):
                    elem_type = TYPE_ALIASES.get(annotation.slice.id, "v")
                    return f"a{elem_type}"
                return "av"
            
            elif container in ("dict", "Dict"):
                if isinstance(annotation.slice, ast.Tuple) and len(annotation.slice.elts) >= 2:
                    k = annotation.slice.elts[0]
                    v = annotation.slice.elts[1]
                    key_type = resolve_type(k)
                    val_type = resolve_type(v)
                    return f"a{{{key_type}{val_type}}}"
                return "a{sv}"
            
            elif container in ("tuple", "Tuple"):
                if isinstance(annotation.slice, ast.Tuple):
                    types = [resolve_type(e) for e in annotation.slice.elts]
                    return "(" + "".join(types) + ")"
    
    # Handle Constant (string literal type hints)
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        # Could be a forward reference or a signature
        if annotation.value in TYPE_ALIASES:
            return TYPE_ALIASES[annotation.value]
        # Check if it looks like a D-Bus signature
        if re.match(r'^[sybnqiuxtdoghvas{}\(\)]+$', annotation.value):
            return annotation.value
    
    return "v"


def parse_service_interface(source_path: Path) -> tuple[list[DBusMethod], list[DBusSignal], list[DBusProperty]]:
    """Parse the service.py file to extract D-Bus interface definitions."""
    source = source_path.read_text()
    tree = ast.parse(source)
    
    methods: list[DBusMethod] = []
    signals: list[DBusSignal] = []
    properties: list[DBusProperty] = []
    
    # Find the KapsuleManagerInterface class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "KapsuleManagerInterface":
            # Process class body
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    # Check decorators
                    decorator_names = []
                    for dec in item.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Name):
                                decorator_names.append(dec.func.id)
                        elif isinstance(dec, ast.Name):
                            decorator_names.append(dec.id)
                    
                    # Get docstring
                    doc = ast.get_docstring(item)
                    
                    if "method" in decorator_names:
                        # Extract method info
                        args: list[tuple[str, str]] = []
                        for arg in item.args.args[1:]:  # Skip self
                            arg_type = resolve_type(arg.annotation)
                            args.append((arg.arg, arg_type))
                        
                        return_type = resolve_type(item.returns) if item.returns else None
                        # Filter out None returns
                        if return_type == "v":
                            return_type = None
                            
                        methods.append(DBusMethod(
                            name=item.name,
                            args=args,
                            return_type=return_type,
                            doc=doc,
                        ))
                    
                    elif "signal" in decorator_names:
                        # Extract signal info - signals use return annotation for signature
                        args: list[tuple[str, str]] = []
                        for arg in item.args.args[1:]:  # Skip self
                            arg_type = resolve_type(arg.annotation)
                            args.append((arg.arg, arg_type))
                        
                        signals.append(DBusSignal(
                            name=item.name,
                            args=args,
                            doc=doc,
                        ))
                    
                    elif "dbus_property" in decorator_names:
                        # Extract property info
                        prop_type = resolve_type(item.returns) if item.returns else "v"
                        properties.append(DBusProperty(
                            name=item.name,
                            type=prop_type,
                            access="read",  # Currently all properties are read-only
                            doc=doc,
                        ))
    
    return methods, signals, properties


# Mapping from D-Bus basic types to C++ types for tuple generation
DBUS_TO_CPP_TYPE = {
    "s": "QString",
    "b": "bool",
    "y": "uchar",
    "n": "qint16",
    "q": "quint16",
    "i": "qint32",
    "u": "quint32",
    "x": "qint64",
    "t": "quint64",
    "d": "double",
    "o": "QDBusObjectPath",
    "g": "QDBusSignature",
    "v": "QDBusVariant",
    "h": "QDBusUnixFileDescriptor",
    "as": "QStringList",
    "a{ss}": "QVariantMap",
    "a{sv}": "QVariantMap",
}

# Map specific D-Bus signatures to Kapsule library types
# These types have proper QDBusArgument streaming operators defined
# Note: Values must be XML-escaped (use &lt; and &gt; for angle brackets)
KAPSULE_TYPE_MAP = {
    "(sssss)": "Kapsule::Container",                  # Container class with D-Bus streaming
    "a(sssss)": "QList&lt;Kapsule::Container&gt;",   # List of containers (Qt handles QList<T> automatically)
    "(bsas)": "Kapsule::EnterResult",                 # PrepareEnter result
}


def dbus_sig_to_cpp_type(sig: str) -> str:
    """Convert a D-Bus signature to a C++ type string.
    
    Handles basic types, arrays, and structs (tuples).
    """
    # Check for direct mapping first
    if sig in DBUS_TO_CPP_TYPE:
        return DBUS_TO_CPP_TYPE[sig]
    
    # Handle struct (tuple) types: (...)
    if sig.startswith("(") and sig.endswith(")"):
        inner = sig[1:-1]
        # Parse the inner types
        types = []
        i = 0
        while i < len(inner):
            c = inner[i]
            if c in "sybnqiuxtdoghv":
                types.append(DBUS_TO_CPP_TYPE.get(c, "QVariant"))
                i += 1
            elif c == "a":
                # Array - find the element type
                if i + 1 < len(inner):
                    if inner[i + 1] == "{":
                        # Dict: a{...}
                        end = inner.find("}", i)
                        if end != -1:
                            dict_sig = inner[i:end + 1]
                            types.append(DBUS_TO_CPP_TYPE.get(dict_sig, "QVariantMap"))
                            i = end + 1
                        else:
                            types.append("QVariant")
                            i += 1
                    elif inner[i + 1] == "(":
                        # Array of structs - find matching paren
                        depth = 0
                        start = i
                        for j in range(i + 1, len(inner)):
                            if inner[j] == "(":
                                depth += 1
                            elif inner[j] == ")":
                                depth -= 1
                                if depth == 0:
                                    array_sig = inner[start:j + 1]
                                    elem_type = dbus_sig_to_cpp_type(inner[start + 1:j + 1])
                                    types.append(f"QList&lt;{elem_type}&gt;")
                                    i = j + 1
                                    break
                        else:
                            types.append("QVariant")
                            i += 1
                    elif inner[i + 1] == "s":
                        types.append("QStringList")
                        i += 2
                    else:
                        # Simple array
                        elem = inner[i + 1]
                        elem_type = DBUS_TO_CPP_TYPE.get(elem, "QVariant")
                        types.append(f"QList&lt;{elem_type}&gt;")
                        i += 2
                else:
                    types.append("QVariant")
                    i += 1
            elif c == "(":
                # Nested struct - find matching paren
                depth = 0
                start = i
                for j in range(i, len(inner)):
                    if inner[j] == "(":
                        depth += 1
                    elif inner[j] == ")":
                        depth -= 1
                        if depth == 0:
                            nested_sig = inner[start:j + 1]
                            types.append(dbus_sig_to_cpp_type(nested_sig))
                            i = j + 1
                            break
                else:
                    types.append("QVariant")
                    i += 1
            else:
                types.append("QVariant")
                i += 1
        
        return "std::tuple&lt;" + ", ".join(types) + "&gt;"
    
    # Handle array of structs: a(...)
    if sig.startswith("a(") and sig.endswith(")"):
        elem_type = dbus_sig_to_cpp_type(sig[1:])
        return f"QList&lt;{elem_type}&gt;"
    
    # Handle simple arrays: aX
    if sig.startswith("a") and len(sig) == 2:
        elem_type = DBUS_TO_CPP_TYPE.get(sig[1], "QVariant")
        return f"QList&lt;{elem_type}&gt;"
    
    return "QVariant"


def dbus_type_to_qt_type(dbus_sig: str) -> str | None:
    """Convert a D-Bus signature to a Qt/C++ type name for annotations.
    
    Returns None for basic types that don't need annotations.
    Uses Kapsule library types where defined, otherwise std::tuple.
    """
    # Basic types that don't need annotations
    basic_types = {"s", "b", "y", "n", "q", "i", "u", "x", "t", "d", "o", "g", "h"}
    if dbus_sig in basic_types:
        return None
    
    # Check for Kapsule library types first
    if dbus_sig in KAPSULE_TYPE_MAP:
        return KAPSULE_TYPE_MAP[dbus_sig]
    
    # Generate the C++ type
    cpp_type = dbus_sig_to_cpp_type(dbus_sig)
    if cpp_type == "QVariant":
        return None
    
    return cpp_type


def generate_introspection_xml(service_path: Path) -> str:
    """Generate D-Bus introspection XML for Qt.
    
    Args:
        service_path: Path to service.py
    """
    methods, signals, properties = parse_service_interface(service_path)
    
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"',
        '    "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">',
        '',
        '<!--',
        '  D-Bus Introspection XML for org.kde.kapsule.Manager',
        '  Auto-generated from service.py - DO NOT EDIT',
        '-->',
        '',
        '<node name="/org/kde/kapsule">',
    ]
    
    # Our interface
    lines.append('  <interface name="org.kde.kapsule.Manager">')
    
    # Methods (sorted alphabetically)
    for method in sorted(methods, key=lambda m: m.name):
        lines.append(f'    <method name="{method.name}">')
        
        # Input arguments with Qt annotations
        for idx, (arg_name, arg_type) in enumerate(method.args):
            lines.append(f'      <arg name="{arg_name}" direction="in" type="{arg_type}"/>')
            qt_type = dbus_type_to_qt_type(arg_type)
            if qt_type:
                lines.append(f'      <annotation name="org.qtproject.QtDBus.QtTypeName.In{idx}" value="{qt_type}"/>')
        
        # Return type with Qt annotation
        if method.return_type:
            lines.append(f'      <arg direction="out" type="{method.return_type}"/>')
            qt_type = dbus_type_to_qt_type(method.return_type)
            if qt_type:
                lines.append(f'      <annotation name="org.qtproject.QtDBus.QtTypeName.Out0" value="{qt_type}"/>')
        
        lines.append('    </method>')
    
    # Properties (sorted alphabetically)
    for prop in sorted(properties, key=lambda p: p.name):
        lines.append(f'    <property name="{prop.name}" type="{prop.type}" access="{prop.access}"/>')
    
    # Signals (sorted alphabetically)
    for signal in sorted(signals, key=lambda s: s.name):
        lines.append(f'    <signal name="{signal.name}">')
        for idx, (arg_name, arg_type) in enumerate(signal.args):
            lines.append(f'      <arg name="{arg_name}" direction="out" type="{arg_type}"/>')
        lines.append('    </signal>')
    
    lines.append('  </interface>')
    lines.append('</node>')
    
    return '\n'.join(lines)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate D-Bus introspection XML for Kapsule daemon"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file path (default: stdout)",
    )
    parser.add_argument(
        "--service-path",
        type=Path,
        default=Path(__file__).parent.parent / "src" / "daemon" / "service.py",
        help="Path to service.py file",
    )
    args = parser.parse_args()

    try:
        xml = generate_introspection_xml(args.service_path)
    except Exception as e:
        print(f"Error generating introspection XML: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    if args.output:
        args.output.write_text(xml)
        print(f"Generated {args.output}", file=sys.stderr)
    else:
        print(xml)

    return 0


if __name__ == "__main__":
    sys.exit(main())
