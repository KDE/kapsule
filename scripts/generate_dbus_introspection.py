#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

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


@dataclass
class TypeAliasInfo:
    """Information about a D-Bus type alias."""
    dbus_sig: str
    cpp_type: str | None = None  # C++ type for Qt annotation, if specified


# Mapping of type aliases to D-Bus signatures and optional C++ types
# This will be populated by parsing dbus_types.py
TYPE_ALIASES: dict[str, TypeAliasInfo] = {}

# Builtin type mappings (always available)
BUILTIN_TYPE_ALIASES = {
    "DBusStr": TypeAliasInfo("s"),
    "DBusObjectPath": TypeAliasInfo("o"),
    "DBusSignature": TypeAliasInfo("g"),
    "DBusBool": TypeAliasInfo("b"),
    "DBusByte": TypeAliasInfo("y"),
    "DBusInt16": TypeAliasInfo("n"),
    "DBusUInt16": TypeAliasInfo("q"),
    "DBusInt32": TypeAliasInfo("i"),
    "DBusUInt32": TypeAliasInfo("u"),
    "DBusInt64": TypeAliasInfo("x"),
    "DBusUInt64": TypeAliasInfo("t"),
    "DBusDouble": TypeAliasInfo("d"),
    "DBusUnixFD": TypeAliasInfo("h"),
    "DBusStrArray": TypeAliasInfo("as"),
    "DBusStrDict": TypeAliasInfo("a{ss}"),
    # Basic Python types
    "str": TypeAliasInfo("s"),
    "bool": TypeAliasInfo("b"),
    "int": TypeAliasInfo("i"),
    "float": TypeAliasInfo("d"),
}


def parse_dbus_types(dbus_types_path: Path) -> dict[str, TypeAliasInfo]:
    """Parse dbus_types.py to extract type alias definitions with CppType metadata.
    
    Returns a dict mapping type alias names to TypeAliasInfo.
    """
    source = dbus_types_path.read_text()
    tree = ast.parse(source)
    
    type_aliases: dict[str, TypeAliasInfo] = {}
    
    for node in ast.walk(tree):
        # Look for assignments like: DBusContainer = Annotated[..., "sig", CppType("...")]
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name.startswith("DBus"):
                    info = extract_type_alias_info(node.value)
                    if info:
                        type_aliases[name] = info
    
    return type_aliases


def extract_type_alias_info(value: ast.expr) -> TypeAliasInfo | None:
    """Extract TypeAliasInfo from an Annotated type expression.
    
    Handles both old style: Annotated[type, "signature", CppType("...")]
    And new style: Annotated[type, DBusSignature("signature"), CppType("...")]
    """
    if not isinstance(value, ast.Subscript):
        return None
    
    # Check if it's Annotated[...]
    if not (isinstance(value.value, ast.Name) and value.value.id == "Annotated"):
        return None
    
    if not isinstance(value.slice, ast.Tuple):
        return None
    
    elts = value.slice.elts
    if len(elts) < 2:
        return None
    
    # Second element should be the D-Bus signature
    sig_elt = elts[1]
    dbus_sig = None
    
    # Old style: bare string
    if isinstance(sig_elt, ast.Constant) and isinstance(sig_elt.value, str):
        dbus_sig = sig_elt.value
    # New style: DBusSignature("sig")
    elif isinstance(sig_elt, ast.Call):
        if isinstance(sig_elt.func, ast.Name) and sig_elt.func.id == "DBusSignature":
            if sig_elt.args and isinstance(sig_elt.args[0], ast.Constant):
                dbus_sig = sig_elt.args[0].value
    
    if dbus_sig is None:
        return None
    
    cpp_type = None
    
    # Look for CppType("...") in remaining elements
    for elt in elts[2:]:
        if isinstance(elt, ast.Call):
            if isinstance(elt.func, ast.Name) and elt.func.id == "CppType":
                if elt.args and isinstance(elt.args[0], ast.Constant):
                    cpp_type = elt.args[0].value
    
    return TypeAliasInfo(dbus_sig=dbus_sig, cpp_type=cpp_type)


def extract_annotated_signature(annotation: ast.expr) -> str | None:
    """Extract D-Bus signature from an Annotated[...] type expression.
    
    Handles both old style: Annotated[type, "signature"]
    And new style: Annotated[type, DBusSignature("signature")]
    """
    if isinstance(annotation, ast.Subscript):
        # Check if it's Annotated[type, "signature"] or Annotated[type, DBusSignature("sig")]
        if isinstance(annotation.value, ast.Name) and annotation.value.id == "Annotated":
            if isinstance(annotation.slice, ast.Tuple):
                elts = annotation.slice.elts
                if len(elts) >= 2:
                    # The second element should be the signature
                    sig_elt = elts[1]
                    # Old style: bare string
                    if isinstance(sig_elt, ast.Constant) and isinstance(sig_elt.value, str):
                        return sig_elt.value
                    # New style: DBusSignature("sig")
                    if isinstance(sig_elt, ast.Call):
                        if isinstance(sig_elt.func, ast.Name) and sig_elt.func.id == "DBusSignature":
                            if sig_elt.args and isinstance(sig_elt.args[0], ast.Constant):
                                return sig_elt.args[0].value
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
        info = TYPE_ALIASES.get(type_name)
        if info:
            return info.dbus_sig
        return "v"
    
    # Handle Attribute types (e.g., typing.List)
    if isinstance(annotation, ast.Attribute):
        return "v"
    
    # Handle Subscript types (e.g., list[str], dict[str, str])
    if isinstance(annotation, ast.Subscript):
        if isinstance(annotation.value, ast.Name):
            container = annotation.value.id
            
            if container in ("list", "List"):
                if isinstance(annotation.slice, ast.Name):
                    info = TYPE_ALIASES.get(annotation.slice.id)
                    elem_type = info.dbus_sig if info else "v"
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
        info = TYPE_ALIASES.get(annotation.value)
        if info:
            return info.dbus_sig
        # Check if it looks like a D-Bus signature
        if re.match(r'^[sybnqiuxtdoghvas{}\(\)]+$', annotation.value):
            return annotation.value
    
    return "v"

def parse_service_interface(
    source_path: Path,
    class_name: str,
) -> tuple[list[DBusMethod], list[DBusSignal], list[DBusProperty]]:
    """Parse a Python file to extract D-Bus interface definitions from a class.
    
    Args:
        source_path: Path to the Python source file
        class_name: Name of the ServiceInterface class to parse
    """
    source = source_path.read_text()
    tree = ast.parse(source)
    
    methods: list[DBusMethod] = []
    signals: list[DBusSignal] = []
    properties: list[DBusProperty] = []
    
    # Find the specified class
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
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
                    
                    if "method" in decorator_names or "dbus_method" in decorator_names:
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
                    
                    elif "signal" in decorator_names or "dbus_signal" in decorator_names:
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
    "a{ss}": "QMap<QString, QString>",
    "a{sv}": "QVariantMap",
}


def dbus_sig_to_cpp_type(sig: str) -> str:
    """Convert a D-Bus signature to a C++ type string.
    
    Handles basic types, arrays, and structs (tuples).
    Returns XML-escaped type names (with &lt; and &gt;).
    """
    # Check for direct mapping first
    if sig in DBUS_TO_CPP_TYPE:
        result = DBUS_TO_CPP_TYPE[sig]
        # Escape < and > for XML attribute values
        return result.replace("<", "&lt;").replace(">", "&gt;")
    
    # Handle struct (tuple) types: (...)
    if sig.startswith("(") and sig.endswith(")"):
        inner = sig[1:-1]
        # Parse the inner types
        types: list[str] = []
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


def get_cpp_type_for_signature(dbus_sig: str) -> str | None:
    """Look up a C++ type for a D-Bus signature from TYPE_ALIASES.
    
    Returns the CppType value if any type alias with that signature has one,
    otherwise None.
    """
    for info in TYPE_ALIASES.values():
        if info.dbus_sig == dbus_sig and info.cpp_type:
            # XML-escape angle brackets for annotations
            return info.cpp_type.replace("<", "&lt;").replace(">", "&gt;")
    return None


def dbus_type_to_qt_type(dbus_sig: str) -> str | None:
    """Convert a D-Bus signature to a Qt/C++ type name for annotations.
    
    Returns None for basic types that don't need annotations.
    Uses Kapsule library types where defined, otherwise std::tuple.
    """
    # Basic types that don't need annotations
    basic_types = {"s", "b", "y", "n", "q", "i", "u", "x", "t", "d", "o", "g", "h"}
    if dbus_sig in basic_types:
        return None
    
    # Check for library types with explicit CppType metadata
    cpp_type = get_cpp_type_for_signature(dbus_sig)
    if cpp_type:
        return cpp_type
    
    # Generate the C++ type
    cpp_type = dbus_sig_to_cpp_type(dbus_sig)
    if cpp_type == "QVariant":
        return None
    
    return cpp_type

def initialize_type_aliases(daemon_path: Path) -> None:
    """Initialize TYPE_ALIASES by parsing dbus_types.py.
    
    Args:
        daemon_path: Path to the daemon directory containing dbus_types.py
    """
    global TYPE_ALIASES
    
    # Start with builtins
    TYPE_ALIASES = dict(BUILTIN_TYPE_ALIASES)
    
    # Parse dbus_types.py for additional types
    dbus_types_path = daemon_path / "dbus_types.py"
    if dbus_types_path.exists():
        parsed = parse_dbus_types(dbus_types_path)
        TYPE_ALIASES.update(parsed)


def generate_interface_xml(
    interface_name: str,
    object_path: str,
    methods: list[DBusMethod],
    signals: list[DBusSignal],
    properties: list[DBusProperty],
    comment: str = "",
) -> str:
    """Generate D-Bus introspection XML for a single interface.
    
    Args:
        interface_name: Full D-Bus interface name (e.g., "org.kde.kapsule.Operation")
        object_path: D-Bus object path (e.g., "/org/kde/kapsule/operations")
        methods: List of methods
        signals: List of signals
        properties: List of properties
        comment: Optional comment for the XML header
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"',
        '    "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">',
        '',
    ]
    
    if comment:
        lines.extend([
            '<!--',
            f'  {comment}',
            '  Auto-generated - DO NOT EDIT',
            '-->',
            '',
        ])
    
    lines.append(f'<node name="{object_path}">')
    lines.append(f'  <interface name="{interface_name}">')
    
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
        help="Output file path for Manager interface (default: stdout)",
    )
    parser.add_argument(
        "--operation-output",
        type=Path,
        help="Output file path for Operation interface",
    )
    parser.add_argument(
        "--service-path",
        type=Path,
        default=Path(__file__).parent.parent / "src" / "daemon" / "service.py",
        help="Path to service.py file",
    )
    parser.add_argument(
        "--operations-path",
        type=Path,
        default=Path(__file__).parent.parent / "src" / "daemon" / "operations.py",
        help="Path to operations.py file",
    )
    args = parser.parse_args()

    daemon_path = args.service_path.parent
    
    try:
        # Initialize type aliases from dbus_types.py
        initialize_type_aliases(daemon_path)
        
        # Generate Manager interface XML
        methods, signals, properties = parse_service_interface(
            args.service_path, "KapsuleManagerInterface"
        )
        manager_xml = generate_interface_xml(
            "org.kde.kapsule.Manager",
            "/org/kde/kapsule",
            methods, signals, properties,
            "D-Bus Introspection XML for org.kde.kapsule.Manager",
        )
        
        if args.output:
            args.output.write_text(manager_xml)
            print(f"Generated {args.output}", file=sys.stderr)
        else:
            print(manager_xml)
        
        # Generate Operation interface XML if requested
        if args.operation_output:
            op_methods, op_signals, op_properties = parse_service_interface(
                args.operations_path, "OperationInterface"
            )
            operation_xml = generate_interface_xml(
                "org.kde.kapsule.Operation",
                "/org/kde/kapsule/operations",
                op_methods, op_signals, op_properties,
                "D-Bus Introspection XML for org.kde.kapsule.Operation",
            )
            args.operation_output.write_text(operation_xml)
            print(f"Generated {args.operation_output}", file=sys.stderr)
            
    except Exception as e:
        print(f"Error generating introspection XML: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
