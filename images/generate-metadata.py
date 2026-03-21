#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate an Incus image metadata.yaml from a kapsule.yaml definition.

Writes a properly formatted ``metadata.yaml`` that Incus stores as image
properties when the image is imported.  If the kapsule.yaml contains
``default_options``, they are embedded as a JSON-encoded string under the
``kapsule.default_options`` property so the daemon can read them back from
any locally cached image.
"""

import json
import sys
from pathlib import Path

import yaml


def main() -> None:
    if len(sys.argv) not in (4, 5):
        print(
            f"Usage: {sys.argv[0]} <arch> <creation_date> <output-file> [kapsule.yaml]",
            file=sys.stderr,
        )
        print(
            f"Example: {sys.argv[0]} amd64 1710979200 /tmp/metadata.yaml images/archlinux/kapsule.yaml",
            file=sys.stderr,
        )
        sys.exit(1)

    arch = sys.argv[1]
    creation_date = int(sys.argv[2])
    output_path = Path(sys.argv[3])
    kapsule_yaml_path = Path(sys.argv[4]) if len(sys.argv) == 5 else None

    description = "Kapsule container image"
    default_options: dict[str, object] = {}

    if kapsule_yaml_path and kapsule_yaml_path.exists():
        with open(kapsule_yaml_path) as f:
            data: dict[str, object] = yaml.safe_load(f) or {}
        if data.get("description"):
            description = str(data["description"])
        default_options = data.get("default_options", {}) or {}  # type: ignore[assignment]

    properties: dict[str, str] = {
        "os": "linux",
        "architecture": arch,
        "description": description,
    }

    if default_options:
        properties["kapsule.default_options"] = json.dumps(
            default_options, separators=(",", ":")
        )

    metadata = {
        "architecture": arch,
        "creation_date": creation_date,
        "properties": properties,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(metadata, f, default_flow_style=False, sort_keys=False)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
