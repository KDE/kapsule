#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Test script to list Incus instances using the generated models."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from incus.client import IncusClient
from incus.models import Instance, InstanceFull


def main():
    """List all Incus instances."""
    print("Connecting to Incus via Unix socket...")
    client = IncusClient()

    # Get server info first to verify connection
    print("\n--- Server Info ---")
    try:
        response = client.get("/1.0")
        response.raise_for_status()
        server_info = response.json()
        print(f"API Version: {server_info.get('metadata', {}).get('api_version', 'unknown')}")
        print(f"Environment: {server_info.get('metadata', {}).get('environment', {}).get('server_name', 'unknown')}")
    except Exception as e:
        print(f"Error connecting to Incus: {e}")
        print("\nMake sure:")
        print("  1. Incus is running: systemctl status incus")
        print("  2. You have permission to access the socket (user in 'incus' or 'incus-admin' group)")
        print("  3. Socket exists at /var/lib/incus/unix.socket")
        return 1

    # List instances (basic info)
    print("\n--- Instances (names only) ---")
    response = client.get("/1.0/instances")
    response.raise_for_status()
    data = response.json()
    
    instance_urls = data.get("metadata", [])
    if not instance_urls:
        print("No instances found.")
    else:
        for url in instance_urls:
            # URL format is /1.0/instances/<name>
            name = url.split("/")[-1]
            print(f"  - {name}")

    # List instances with full details using recursion=1
    print("\n--- Instances (with details) ---")
    response = client.get("/1.0/instances", params={"recursion": 1})
    response.raise_for_status()
    data = response.json()

    instances = data.get("metadata", [])
    if not instances:
        print("No instances found.")
    else:
        for inst_data in instances:
            # Parse into our Pydantic model
            try:
                instance = Instance.model_validate(inst_data)
                status = inst_data.get("status", "Unknown")
                inst_type = inst_data.get("type", "container")
                
                print(f"\n  {instance.name}:")
                print(f"    Type: {inst_type}")
                print(f"    Status: {status}")
                print(f"    Architecture: {instance.architecture}")
                print(f"    Created: {instance.created_at}")
                if instance.description:
                    print(f"    Description: {instance.description}")
                if instance.profiles:
                    print(f"    Profiles: {', '.join(instance.profiles)}")
            except Exception as e:
                # Fallback to raw data if model parsing fails
                name = inst_data.get("name", "unknown")
                status = inst_data.get("status", "unknown")
                print(f"\n  {name}: {status} (model parse error: {e})")

    # Get state of first instance if any exist
    if instances:
        first_name = instances[0].get("name")
        print(f"\n--- State of '{first_name}' ---")
        response = client.get(f"/1.0/instances/{first_name}/state")
        response.raise_for_status()
        state_data = response.json().get("metadata", {})
        
        print(f"  Status: {state_data.get('status', 'unknown')}")
        print(f"  PID: {state_data.get('pid', 'N/A')}")
        
        cpu = state_data.get("cpu", {})
        if cpu:
            print(f"  CPU Usage: {cpu.get('usage', 0) / 1_000_000_000:.2f}s")
        
        memory = state_data.get("memory", {})
        if memory:
            usage_mb = memory.get("usage", 0) / 1024 / 1024
            print(f"  Memory Usage: {usage_mb:.1f} MB")

    print("\n✓ Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
