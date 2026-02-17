# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kapsule container creation option schema and validation.

This module defines the **Kapsule option schema** — a purpose-built format
that describes every option available when creating a container.  It serves
as the single source of truth for the daemon, CLI, and any future GUI.

Design goals
------------
- **One schema, many consumers**: the daemon validates incoming D-Bus
  ``a{sv}`` dicts against it; the CLI derives ``--flags`` from it; a
  future KCM can render form widgets from it dynamically.
- **Forward-compatible transport**: ``CreateContainer`` accepts ``a{sv}``
  (a D-Bus variant dict), so adding an option never changes the D-Bus
  method signature.  Old clients that omit a key simply get the default.
- **No external dependencies**: the schema is a plain Python dict
  serialised to JSON.  No JSON Schema library is needed.

Schema format
-------------
The schema is a JSON object with a ``version`` integer and an ordered
array of ``sections``.  Each section groups related options for UI layout.

Top-level::

    {
        "version": 1,
        "sections": [ <section>, ... ]
    }

Section::

    {
        "id": "mounts",              // stable identifier
        "title": "Host Mounts",       // human-readable label
        "options": [ <option>, ... ]  // ordered list of options
    }

Option (boolean)::

    {
        "key": "mount_home",                        // D-Bus a{sv} dict key
        "type": "boolean",                           // value type
        "title": "Mount Home Directory",              // short UI label
        "description": "Mount the user's home ...",   // longer help text
        "default": true                              // value when omitted
    }

Option (array of strings)::

    {
        "key": "custom_mounts",
        "type": "array",
        "items": {"type": "string", "format": "directory-path"},
        "title": "Additional Mounts",
        "description": "Extra host directories ...",
        "default": []
    }

Optional fields on any option:

``requires``  (dict[str, value])
    Inter-option dependency.  If present, the option is only valid
    when every listed prerequisite has the specified value.  Used for
    UI (disable the toggle) and for server-side validation.
    Example: ``{"gpu": true}`` means this option requires ``gpu=true``.

Supported ``type`` values:

========== =================== ==========================
type       Python type          CLI mapping
========== =================== ==========================
``boolean`` ``bool``            ``--flag`` / ``--no-flag``
``string``  ``str``             ``--key <value>``
``array``   ``list[str]``       ``--key <v>`` (repeatable)
========== =================== ==========================

The ``items.format`` field is a UI hint (e.g. ``"directory-path"`` tells
a GUI to show a directory picker).

Data flow
---------
1. Client calls ``GetCreateSchema()`` over D-Bus → receives JSON string.
2. Client renders UI / ``--help`` from the schema.
3. User makes choices → client builds an ``a{sv}`` dict with only the
   keys the user explicitly set (omitted keys get schema defaults).
4. Client calls ``CreateContainer(name, image, options_dict)``.
5. Daemon calls :func:`parse_options` which:
   a. Rejects unknown keys.
   b. Fills in defaults for missing keys.
   c. Type-checks every value.
   d. Enforces constraints (``requires``, implied values).
   e. Returns a validated :class:`ContainerOptions` dataclass.
6. ``ContainerService.create_container`` receives the dataclass and
   proceeds with container setup.

Adding a new option
-------------------
1. Add an entry to :data:`CREATE_SCHEMA` (in the appropriate section).
2. Add a corresponding field to :class:`ContainerOptions`.
3. Handle the new field in ``container_service.py``.
4. **The CLI picks up the new option automatically** — it fetches
   the schema from the daemon at runtime and generates ``--flags``
   dynamically.  No C++ change is needed.
5. **No D-Bus signature change.  No proxy regeneration.**
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# Schema Definition
# =============================================================================
#
# The canonical schema for container creation options.
#
# Naming conventions borrow from JSON Schema ("type", "title",
# "description", "default", "items") so that the format feels familiar,
# but this is NOT JSON Schema — there is no $ref, no allOf/anyOf, and
# no meta-schema validation.  The format is intentionally small so that
# parsing it in C++/QML is trivial (QJsonDocument + a Repeater).
#
# The ordering of sections and options within each section is
# significant: it defines the display order in UIs.

CREATE_SCHEMA: dict[str, Any] = {
    "version": 1,
    "sections": [
        {
            "id": "dbus",
            "title": "D-Bus Integration",
            "options": [
                {
                    "key": "session_mode",
                    "type": "boolean",
                    "title": "Session Mode",
                    "description": "Container gets its own D-Bus session bus",
                    "default": False,
                },
                {
                    "key": "dbus_mux",
                    "type": "boolean",
                    "title": "D-Bus Multiplexer",
                    "description": "Enable D-Bus multiplexer for hybrid host/container access (implies Session Mode)",
                    "default": False,
                    "requires": {"session_mode": True},
                },
            ],
        },
        {
            "id": "mounts",
            "title": "Host Mounts",
            "options": [
                {
                    "key": "host_rootfs",
                    "type": "boolean",
                    "title": "Mount Host Filesystem",
                    "description": "Mount entire host rootfs at /.kapsule/host",
                    "default": True,
                },
                {
                    "key": "mount_home",
                    "type": "boolean",
                    "title": "Mount Home Directory",
                    "description": "Mount the user's home directory in the container",
                    "default": True,
                },
                {
                    "key": "custom_mounts",
                    "type": "array",
                    "items": {"type": "string", "format": "directory-path"},
                    "title": "Additional Mounts",
                    "description": "Extra host directories to mount in the container",
                    "default": [],
                },
            ],
        },
        {
            "id": "gpu",
            "title": "GPU",
            "options": [
                {
                    "key": "gpu",
                    "type": "boolean",
                    "title": "GPU Passthrough",
                    "description": "Pass through GPU devices to the container",
                    "default": True,
                },
                {
                    "key": "nvidia_drivers",
                    "type": "boolean",
                    "title": "NVIDIA Driver Injection",
                    "description": "Inject host NVIDIA userspace drivers on each start",
                    "default": False,
                    "requires": {"gpu": True},
                },
            ],
        },
    ],
}


def get_create_schema_json() -> str:
    """Return the create schema as a compact JSON string.

    This is the value returned by the ``GetCreateSchema()`` D-Bus method.
    Clients receive this over D-Bus as a plain string (signature ``s``)
    and can parse it with any JSON library (``QJsonDocument`` in C++,
    ``JSON.parse()`` in QML/JS, ``json.loads()`` in Python).
    """
    return json.dumps(CREATE_SCHEMA, separators=(",", ":"))


# =============================================================================
# Defaults & Validation
# =============================================================================

def _build_defaults() -> dict[str, Any]:
    """Build a flat dict of option key -> default value from the schema."""
    defaults: dict[str, Any] = {}
    for section in CREATE_SCHEMA["sections"]:
        for option in section["options"]:
            defaults[option["key"]] = option["default"]
    return defaults


def _build_type_map() -> dict[str, str]:
    """Build a flat dict of option key -> expected type name."""
    types: dict[str, str] = {}
    for section in CREATE_SCHEMA["sections"]:
        for option in section["options"]:
            types[option["key"]] = str(option["type"])
    return types


_DEFAULTS = _build_defaults()
_TYPES = _build_type_map()

# Python type expected for each schema type
_TYPE_CHECK: dict[str, type | tuple[type, ...]] = {
    "boolean": bool,
    "string": str,
    "array": list,
}


@dataclass
class ContainerOptions:
    """Validated container creation options.

    Constructed from a D-Bus ``a{sv}`` dict via :func:`parse_options`.
    All fields have defaults matching the schema.  The 1:1 mapping
    between schema keys and dataclass fields is intentional — the
    schema is the external contract, this class is the internal
    representation.

    The C++ mirror of this class is ``Kapsule::ContainerOptions`` in
    ``types.h``, which serialises to ``a{sv}`` via ``toVariantMap()``.
    """

    session_mode: bool = False
    dbus_mux: bool = False
    host_rootfs: bool = True
    mount_home: bool = True
    custom_mounts: list[str] = field(default_factory=lambda: list[str]())
    gpu: bool = True
    nvidia_drivers: bool = False


class OptionValidationError(Exception):
    """Raised when an option dict fails validation."""


def parse_options(raw: dict[str, Any]) -> ContainerOptions:
    """Parse and validate an ``a{sv}`` option dict into :class:`ContainerOptions`.

    - Unknown keys are rejected.
    - Missing keys get their schema defaults.
    - Type mismatches are rejected.
    - Constraint violations (``requires``) are rejected.

    Args:
        raw: Dict from the D-Bus ``a{sv}`` parameter. Values have already
            been unwrapped from ``Variant`` by dbus-fast.

    Returns:
        Validated ``ContainerOptions`` instance.

    Raises:
        OptionValidationError: On validation failure.
    """
    # Reject unknown keys
    unknown = set(raw.keys()) - set(_DEFAULTS.keys())
    if unknown:
        raise OptionValidationError(f"Unknown options: {', '.join(sorted(unknown))}")

    # Merge with defaults
    merged: dict[str, Any] = {**_DEFAULTS, **raw}

    # Type-check each value
    for key, value in merged.items():
        expected_type = _TYPES.get(key)
        if expected_type and expected_type in _TYPE_CHECK:
            py_type = _TYPE_CHECK[expected_type]
            if not isinstance(value, py_type):
                raise OptionValidationError(
                    f"Option '{key}' must be {expected_type}, got {type(value).__name__}"
                )

    # Validate array element types
    if "custom_mounts" in merged:
        mounts = merged["custom_mounts"]
        if isinstance(mounts, list):
            for i, mount_item in enumerate(mounts):  # type: ignore[arg-type]
                if not isinstance(mount_item, str):
                    raise OptionValidationError(
                        f"custom_mounts[{i}] must be a string, "
                        f"got {type(mount_item).__name__}"  # type: ignore[union-attr]
                    )

    # Apply constraint logic
    opts = ContainerOptions(
        session_mode=bool(merged["session_mode"]),
        dbus_mux=bool(merged["dbus_mux"]),
        host_rootfs=bool(merged["host_rootfs"]),
        mount_home=bool(merged["mount_home"]),
        custom_mounts=list(merged["custom_mounts"]),  # type: ignore[arg-type]
        gpu=bool(merged["gpu"]),
        nvidia_drivers=bool(merged["nvidia_drivers"]),
    )

    # dbus_mux implies session_mode
    if opts.dbus_mux:
        opts = ContainerOptions(
            session_mode=True,
            dbus_mux=opts.dbus_mux,
            host_rootfs=opts.host_rootfs,
            mount_home=opts.mount_home,
            custom_mounts=opts.custom_mounts,
            gpu=opts.gpu,
            nvidia_drivers=opts.nvidia_drivers,
        )

    # dbus_mux requires host_rootfs
    if opts.dbus_mux and not opts.host_rootfs:
        raise OptionValidationError(
            "D-Bus multiplexer requires host_rootfs "
            "(the mux binary is accessed via the host filesystem mount)"
        )

    # nvidia_drivers requires gpu
    if opts.nvidia_drivers and not opts.gpu:
        raise OptionValidationError(
            "nvidia_drivers requires gpu to be enabled"
        )

    return opts
