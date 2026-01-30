"""High-level Incus REST API client.

This module provides a typed async client for the Incus REST API,
communicating over the Unix socket at /var/lib/incus/unix.socket.
"""

from __future__ import annotations

from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, RootModel

T = TypeVar("T", bound=BaseModel)

from .models_generated import (
    Instance,
    InstanceSource,
    InstancesPost,
    InstanceStatePut,
    Operation,
    Profile,
    ProfilesPost,
    Server,
)


# List wrapper models for typed API responses
class InstanceList(RootModel[list[Instance]]):
    """List of Instance objects."""
    pass


class StringList(RootModel[list[str]]):
    """List of string URLs/paths."""
    pass


# Wrapper for async operation responses (the full response, not just metadata)
class AsyncOperationResponse(BaseModel):
    """Async operation response wrapper."""
    type: str
    status: str
    status_code: int
    operation: str | None = None
    metadata: Operation | None = None


class IncusError(Exception):
    """Error from Incus API."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class ContainerInfo(BaseModel):
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
        self, method: str, path: str, *, response_type: type[T], json: dict[str, Any] | None = None
    ) -> T:
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

        Args:
            method: HTTP method.
            path: API path.
            response_type: Pydantic model to deserialize the response into.
            json: Optional JSON body for the request.

        Returns:
            A validated instance of response_type.
        """
        client = await self._get_client()
        response = await client.request(method, path, json=json)

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
            return response_type.model_validate(data)

        metadata = data.get("metadata", data)
        return response_type.model_validate(metadata)

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
        if recursion == 0:
            # Just URLs like ["/1.0/instances/foo", "/1.0/instances/bar"]
            # We'd need to fetch each one - not implemented yet
            raise NotImplementedError("recursion=0 not yet supported")

        # With recursion=1, we get full instance objects
        result = await self._request(
            "GET", f"/1.0/instances?recursion={recursion}", response_type=InstanceList
        )
        return result.root

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
        return await self._request("GET", f"/1.0/instances/{name}", response_type=Instance)

    async def is_available(self) -> bool:
        """Check if Incus is available and responding.

        Returns:
            True if Incus is available.
        """
        try:
            await self._request("GET", "/1.0", response_type=Server)
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
        result = await self._request("GET", "/1.0/profiles", response_type=StringList)
        # Returns URLs like ["/1.0/profiles/default", "/1.0/profiles/foo"]
        return [url.split("/")[-1] for url in result.root]

    async def get_profile(self, name: str) -> Profile:
        """Get a profile by name.

        Args:
            name: Profile name.

        Returns:
            Profile object.
        """
        return await self._request("GET", f"/1.0/profiles/{name}", response_type=Profile)

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
        await self._request(
            "POST", "/1.0/profiles",
            response_type=StringList,  # Empty response, but typed
            json=profile.model_dump(exclude_none=True),
        )

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

    async def create_instance(self, instance: InstancesPost, wait: bool = False) -> Operation:
        """Create a new instance (container or VM).

        Args:
            instance: Instance configuration.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        response = await self._request(
            "POST", "/1.0/instances",
            response_type=AsyncOperationResponse,
            json=instance.model_dump(exclude_none=True),
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if wait and operation.id:
            operation = await self.wait_operation(operation.id)

        return operation

    async def get_operation(self, operation_id: str) -> Operation:
        """Get an operation by ID.

        Args:
            operation_id: Operation UUID.

        Returns:
            Operation object.
        """
        return await self._request(
            "GET", f"/1.0/operations/{operation_id}", response_type=Operation
        )

    async def wait_operation(self, operation_id: str, timeout: int = 60) -> Operation:
        """Wait for an operation to complete.

        Args:
            operation_id: Operation UUID.
            timeout: Timeout in seconds.

        Returns:
            Operation object with final status.
        """
        return await self._request(
            "GET", f"/1.0/operations/{operation_id}/wait?timeout={timeout}",
            response_type=Operation,
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

    # -------------------------------------------------------------------------
    # Instance state operations
    # -------------------------------------------------------------------------

    async def change_instance_state(
        self, name: str, state: InstanceStatePut, wait: bool = False
    ) -> Operation:
        """Change instance state (start, stop, restart, freeze, unfreeze).

        Args:
            name: Instance name.
            state: State change request.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        response = await self._request(
            "PUT", f"/1.0/instances/{name}/state",
            response_type=AsyncOperationResponse,
            json=state.model_dump(exclude_none=True),
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if wait and operation.id:
            operation = await self.wait_operation(operation.id)

        return operation

    async def start_instance(self, name: str, wait: bool = False) -> Operation:
        """Start an instance.

        Args:
            name: Instance name.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        state = InstanceStatePut(
            action="start",
            force=None,
            stateful=None,
            timeout=None,
        )
        return await self.change_instance_state(name, state, wait=wait)

    async def stop_instance(
        self, name: str, force: bool = False, wait: bool = False
    ) -> Operation:
        """Stop an instance.

        Args:
            name: Instance name.
            force: If True, force stop the instance.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        state = InstanceStatePut(
            action="stop",
            force=force,
            stateful=None,
            timeout=None,
        )
        return await self.change_instance_state(name, state, wait=wait)

    # -------------------------------------------------------------------------
    # Instance deletion
    # -------------------------------------------------------------------------

    async def delete_instance(self, name: str, wait: bool = False) -> Operation:
        """Delete an instance.

        Args:
            name: Instance name.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        response = await self._request(
            "DELETE", f"/1.0/instances/{name}",
            response_type=AsyncOperationResponse,
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if wait and operation.id:
            operation = await self.wait_operation(operation.id)

        return operation

    # -------------------------------------------------------------------------
    # File operations
    # -------------------------------------------------------------------------

    async def push_file(
        self,
        instance: str,
        path: str,
        content: str | bytes,
        *,
        uid: int = 0,
        gid: int = 0,
        mode: str = "0644",
    ) -> None:
        """Push a file to an instance.

        Args:
            instance: Instance name.
            path: Absolute path inside the instance.
            content: File content (str or bytes).
            uid: File owner UID.
            gid: File owner GID.
            mode: File mode (octal string, e.g., "0644").
        """
        if isinstance(content, str):
            content = content.encode("utf-8")

        client = await self._get_client()
        response = await client.post(
            f"/1.0/instances/{instance}/files",
            params={"path": path},
            headers={
                "X-Incus-uid": str(uid),
                "X-Incus-gid": str(gid),
                "X-Incus-mode": mode,
                "X-Incus-type": "file",
                "X-Incus-write": "overwrite",
                "Content-Type": "application/octet-stream",
            },
            content=content,
        )

        if response.status_code >= 400:
            raise IncusError(f"Failed to push file {path}: {response.text}", response.status_code)

    async def create_symlink(
        self,
        instance: str,
        path: str,
        target: str,
        *,
        uid: int = 0,
        gid: int = 0,
    ) -> None:
        """Create a symlink in an instance.

        Args:
            instance: Instance name.
            path: Absolute path for the symlink.
            target: Symlink target (what it points to).
            uid: Symlink owner UID.
            gid: Symlink owner GID.
        """
        client = await self._get_client()
        response = await client.post(
            f"/1.0/instances/{instance}/files",
            params={"path": path},
            headers={
                "X-Incus-uid": str(uid),
                "X-Incus-gid": str(gid),
                "X-Incus-type": "symlink",
            },
            content=target.encode("utf-8"),
        )

        if response.status_code >= 400:
            raise IncusError(f"Failed to create symlink {path}: {response.text}", response.status_code)

    async def mkdir(
        self,
        instance: str,
        path: str,
        *,
        uid: int = 0,
        gid: int = 0,
        mode: str = "0755",
    ) -> None:
        """Create a directory in an instance.

        Args:
            instance: Instance name.
            path: Absolute path for the directory.
            uid: Directory owner UID.
            gid: Directory owner GID.
            mode: Directory mode (octal string, e.g., "0755").
        """
        client = await self._get_client()
        response = await client.post(
            f"/1.0/instances/{instance}/files",
            params={"path": path},
            headers={
                "X-Incus-uid": str(uid),
                "X-Incus-gid": str(gid),
                "X-Incus-mode": mode,
                "X-Incus-type": "directory",
            },
            content=b"",
        )

        if response.status_code >= 400:
            raise IncusError(f"Failed to create directory {path}: {response.text}", response.status_code)