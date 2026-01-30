"""High-level Incus REST API client.

This module provides a typed async client for the Incus REST API,
communicating over the Unix socket at /var/lib/incus/unix.socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .models_generated import (
    Instance,
    InstanceSource,
    InstancesPost,
    Profile,
    ProfilesPost,
)


@dataclass
class OperationResult:
    """Simplified operation result."""

    id: str
    status: str
    err: str | None = None

    @property
    def succeeded(self) -> bool:
        """Check if operation succeeded."""
        return self.status == "Success"


class IncusError(Exception):
    """Error from Incus API."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass
class ContainerInfo:
    """Simplified container information."""

    name: str
    status: str
    image: str
    created: str


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

        For async operations, returns the full response (not just metadata)
        so callers can access the operation URL.
        """
        client = await self._get_client()
        response = await client.request(method, path, **kwargs)

        # Handle HTTP errors and convert to IncusError
        if response.status_code >= 400:
            # Try to parse Incus error response
            try:
                data = response.json()
                error_msg = data.get("error", response.reason_phrase)
                error_code = data.get("error_code", response.status_code)
            except Exception:
                error_msg = response.reason_phrase
                error_code = response.status_code
            raise IncusError(error_msg, error_code)

        data = response.json()

        if data.get("type") == "error":
            raise IncusError(
                data.get("error", "Unknown error"),
                data.get("error_code"),
            )

        # For async operations, return the full response
        if data.get("type") == "async":
            return data

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
        """List all containers with simplified info.

        Returns:
            List of ContainerInfo.
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

    # -------------------------------------------------------------------------
    # Profile operations
    # -------------------------------------------------------------------------

    async def list_profiles(self) -> list[str]:
        """List all profile names.

        Returns:
            List of profile names.
        """
        data = await self.get("/1.0/profiles")
        # Returns URLs like ["/1.0/profiles/default", "/1.0/profiles/foo"]
        return [url.split("/")[-1] for url in data]

    async def get_profile(self, name: str) -> Profile:
        """Get a profile by name.

        Args:
            name: Profile name.

        Returns:
            Profile object.
        """
        data = await self.get(f"/1.0/profiles/{name}")
        return Profile.model_validate(data)

    async def profile_exists(self, name: str) -> bool:
        """Check if a profile exists.

        Args:
            name: Profile name.

        Returns:
            True if the profile exists.
        """
        profiles = await self.list_profiles()
        return name in profiles

    async def create_profile(self, profile: ProfilesPost) -> None:
        """Create a new profile.

        Args:
            profile: Profile configuration.
        """
        await self.post("/1.0/profiles", json=profile.model_dump(exclude_none=True))

    async def ensure_profile(self, name: str, profile_data: ProfilesPost) -> bool:
        """Ensure a profile exists, creating it if necessary.

        Args:
            name: Profile name.
            profile_data: Profile configuration (used if creating).

        Returns:
            True if the profile was created, False if it already existed.
        """
        if await self.profile_exists(name):
            return False

        await self.create_profile(profile_data)
        return True

    # -------------------------------------------------------------------------
    # Instance creation
    # -------------------------------------------------------------------------

    async def create_instance(self, instance: InstancesPost, wait: bool = False) -> OperationResult:
        """Create a new instance (container or VM).

        Args:
            instance: Instance configuration.
            wait: If True, wait for the operation to complete.

        Returns:
            OperationResult with status info.
        """
        response = await self.post("/1.0/instances", json=instance.model_dump(exclude_none=True))

        # For async operations, response contains the full response with operation URL
        metadata = response.get("metadata", {})
        operation = OperationResult(
            id=metadata.get("id", ""),
            status=metadata.get("status", "Unknown"),
            err=metadata.get("err") or None,
        )

        if wait and operation.id:
            operation = await self.wait_operation(operation.id)

        return operation

    async def get_operation(self, operation_id: str) -> OperationResult:
        """Get an operation by ID.

        Args:
            operation_id: Operation UUID.

        Returns:
            OperationResult object.
        """
        data = await self.get(f"/1.0/operations/{operation_id}")
        return OperationResult(
            id=data.get("id", operation_id),
            status=data.get("status", "Unknown"),
            err=data.get("err") or None,
        )

    async def wait_operation(self, operation_id: str, timeout: int = 60) -> OperationResult:
        """Wait for an operation to complete.

        Args:
            operation_id: Operation UUID.
            timeout: Timeout in seconds.

        Returns:
            OperationResult object with final status.
        """
        data = await self.get(f"/1.0/operations/{operation_id}/wait?timeout={timeout}")
        return OperationResult(
            id=data.get("id", operation_id),
            status=data.get("status", "Unknown"),
            err=data.get("err") or None,
        )

    async def instance_exists(self, name: str) -> bool:
        """Check if an instance exists.

        Args:
            name: Instance name.

        Returns:
            True if the instance exists.
        """
        try:
            await self.get_instance(name)
            return True
        except IncusError:
            return False
