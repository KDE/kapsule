"""High-level Incus REST API client.

This module provides a typed async client for the Incus REST API,
communicating over the Unix socket at /var/lib/incus/unix.socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .models_generated import Instance


class IncusError(Exception):
    """Error from Incus API."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class ContainerInfo:
    """Simplified container information for D-Bus exposure."""

    name: str
    status: str
    image: str
    created: str

    def to_dbus_struct(self) -> tuple[str, str, str, str]:
        """Convert to D-Bus struct (ssss)."""
        return (self.name, self.status, self.image, self.created)


class IncusClient:
    """Async client for Incus REST API over Unix socket."""

    def __init__(self, socket_path: str = "/var/lib/incus/unix.socket"):
        self._socket_path = socket_path
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            transport = httpx.AsyncHTTPTransport(uds=self._socket_path)
            self._client = httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        """Make request and handle Incus response format.

        Incus wraps all responses in:
        {
            "type": "sync" | "async" | "error",
            "status": "Success" | ...,
            "status_code": 200 | ...,
            "metadata": <actual data>
        }
        """
        client = await self._get_client()
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        data = response.json()

        if data.get("type") == "error":
            raise IncusError(
                data.get("error", "Unknown error"),
                data.get("error_code"),
            )

        return data.get("metadata", data)

    async def get(self, path: str) -> dict[str, Any]:
        """GET request."""
        return await self._request("GET", path)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST request."""
        return await self._request("POST", path, json=json)

    async def put(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """PUT request."""
        return await self._request("PUT", path, json=json)

    async def delete(self, path: str) -> dict[str, Any]:
        """DELETE request."""
        return await self._request("DELETE", path)

    # -------------------------------------------------------------------------
    # High-level instance operations
    # -------------------------------------------------------------------------

    async def list_instances(self, recursion: int = 1) -> list[Instance]:
        """List all instances (containers and VMs).

        Args:
            recursion: 0 returns just URLs, 1 returns full objects.

        Returns:
            List of Instance objects.
        """
        data = await self.get(f"/1.0/instances?recursion={recursion}")

        if recursion == 0:
            # Just URLs like ["/1.0/instances/foo", "/1.0/instances/bar"]
            # We'd need to fetch each one - not implemented yet
            raise NotImplementedError("recursion=0 not yet supported")

        # With recursion=1, we get full instance objects
        instances = []
        for item in data:
            instances.append(Instance.model_validate(item))
        return instances

    async def list_containers(self) -> list[ContainerInfo]:
        """List all containers with simplified info for D-Bus.

        Returns:
            List of ContainerInfo suitable for D-Bus exposure.
        """
        instances = await self.list_instances(recursion=1)

        containers = []
        for inst in instances:
            # Extract image description from config
            image_desc = "unknown"
            if inst.config:
                image_desc = inst.config.get(
                    "image.description",
                    inst.config.get("image.os", "unknown"),
                )

            # Format created timestamp
            created = ""
            if inst.created_at:
                created = inst.created_at.isoformat()

            containers.append(
                ContainerInfo(
                    name=inst.name or "",
                    status=inst.status or "Unknown",
                    image=image_desc,
                    created=created,
                )
            )

        return containers

    async def get_instance(self, name: str) -> Instance:
        """Get a single instance by name.

        Args:
            name: Instance name.

        Returns:
            Instance object.
        """
        data = await self.get(f"/1.0/instances/{name}")
        return Instance.model_validate(data)

    async def is_available(self) -> bool:
        """Check if Incus is available and responding.

        Returns:
            True if Incus is available.
        """
        try:
            await self.get("/1.0")
            return True
        except Exception:
            return False
