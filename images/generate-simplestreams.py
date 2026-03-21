#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate simplestreams-compatible index.json and images.json from built images."""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def sha256sum(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def combined_sha256(meta_path: Path, rootfs_path: Path) -> str:
    """Compute sha256(meta_content + rootfs_content).

    Incus uses this "combined" hash as the image fingerprint.
    """
    h = hashlib.sha256()
    for p in (meta_path, rootfs_path):
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    return h.hexdigest()


def load_kapsule_yaml(image_dir: Path) -> dict[str, object]:
    kapsule_yaml = image_dir / "kapsule.yaml"
    if not kapsule_yaml.exists():
        print(f"Warning: {kapsule_yaml} not found, skipping", file=sys.stderr)
        return {}
    with open(kapsule_yaml) as f:
        data: dict[str, object] = yaml.safe_load(f)
    return data


def main() -> None:
    if len(sys.argv) not in (4, 6):
        print(
            f"Usage: {sys.argv[0]} <images-dir> <out-dir> <streams-dir> [<artifacts-base-url> <job-name>]",
            file=sys.stderr,
        )
        print(
            f"Example: {sys.argv[0]} images/ out/ streams/",
            file=sys.stderr,
        )
        print(
            f"Example: {sys.argv[0]} images/ out/ streams/ https://gitlab.com/api/v4/projects/123/jobs/artifacts/master/raw/out build_images",
            file=sys.stderr,
        )
        sys.exit(1)

    images_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    streams_dir = Path(sys.argv[3])
    artifacts_base_url: str | None = None
    artifacts_job_name: str | None = None
    if len(sys.argv) == 6:
        artifacts_base_url = sys.argv[4].rstrip("/")
        artifacts_job_name = sys.argv[5]

    products: dict[str, dict[str, object]] = {}
    product_ids: list[str] = []

    # Discover images by looking for kapsule.yaml in subdirectories
    for image_name in sorted(os.listdir(images_dir)):
        image_def_dir = images_dir / image_name
        if not image_def_dir.is_dir():
            continue

        kapsule_yaml = image_def_dir / "kapsule.yaml"
        if not kapsule_yaml.exists():
            continue

        meta = load_kapsule_yaml(image_def_dir)
        if not meta:
            continue

        image_out_dir = out_dir / image_name
        if not image_out_dir.is_dir():
            print(
                f"Warning: built output not found at {image_out_dir}, skipping",
                file=sys.stderr,
            )
            continue

        # Read version
        version_file = image_out_dir / "version"
        if version_file.exists():
            version = version_file.read_text().strip()
        else:
            version = datetime.now(tz=timezone.utc).strftime("%Y%m%d")

        arch = "amd64"
        aliases_list: list[str] = meta.get("aliases", [])  # type: ignore[assignment]
        aliases = ",".join(aliases_list)
        default_options: dict[str, object] = meta.get("default_options", {})  # type: ignore[assignment]

        product_id = f"com.kapsule:{image_name}:{arch}:default"
        product_ids.append(product_id)

        # Build items for this version
        items: dict[str, dict[str, object]] = {}

        # Paths are always relative to the simplestreams server root.
        # When using artifact storage, the Pages site hosts a _redirects
        # file that 302-redirects /artifacts/<image>/<file> to the GitLab
        # job artifacts API URL. Incus uses url.JoinPath(baseURL, path)
        # to construct the download URL, so absolute URLs do NOT work.
        def image_path(filename: str) -> str:
            if artifacts_base_url and artifacts_job_name:
                return f"artifacts/{image_name}/{filename}"
            return f"images/{image_name}/{arch}/{version}/{filename}"

        incus_tar = image_out_dir / "incus.tar.xz"
        rootfs = image_out_dir / "rootfs.squashfs"

        if incus_tar.exists():
            incus_sha = sha256sum(incus_tar)
            incus_size = incus_tar.stat().st_size
            incus_item: dict[str, object] = {
                "ftype": "incus.tar.xz",
                "sha256": incus_sha,
                "size": incus_size,
                "path": image_path("incus.tar.xz"),
            }

            # Incus uses combined_*_sha256 as the image fingerprint.
            if rootfs.exists():
                combined = combined_sha256(incus_tar, rootfs)
                incus_item["combined_squashfs_sha256"] = combined

            items["incus.tar.xz"] = incus_item

            # Also publish as lxd.tar.xz (same file) for backward compat.
            lxd_item = dict(incus_item)
            lxd_item["ftype"] = "lxd.tar.xz"
            items["lxd.tar.xz"] = lxd_item

        if rootfs.exists():
            items["root.squashfs"] = {
                "ftype": "squashfs",
                "sha256": sha256sum(rootfs),
                "size": rootfs.stat().st_size,
                "path": image_path("rootfs.squashfs"),
            }

        product_entry: dict[str, object] = {
            "aliases": aliases,
            "arch": arch,
            "os": image_name.title(),
            "release": "current",
            "release_title": "current",
            "variant": "default",
            "versions": {
                version: {
                    "items": items,
                },
            },
        }

        if default_options:
            # Incus flattens nested dicts into dotted image properties,
            # e.g. {"kapsule": {"default_options": "..."}} becomes
            # "kapsule.default_options" in the local image store.
            # The value must be a string since Incus image properties
            # are dict[str, str].
            product_entry["kapsule"] = {
                "default_options": json.dumps(default_options, separators=(",", ":")),
            }

        products[product_id] = product_entry

    # Generate index.json
    index = {
        "format": "index:1.0",
        "index": {
            "images": {
                "datatype": "image-downloads",
                "path": "streams/v1/images.json",
                "format": "products:1.0",
                "products": product_ids,
            },
        },
    }

    # Generate images.json
    images = {
        "content_id": "images",
        "datatype": "image-downloads",
        "format": "products:1.0",
        "products": products,
    }

    # Write output
    streams_v1 = streams_dir / "v1"
    streams_v1.mkdir(parents=True, exist_ok=True)

    index_path = streams_v1 / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"Wrote {index_path}")

    images_path = streams_v1 / "images.json"
    with open(images_path, "w") as f:
        json.dump(images, f, indent=2)
    print(f"Wrote {images_path}")


if __name__ == "__main__":
    main()
