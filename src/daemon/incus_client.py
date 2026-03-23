# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""High-level Incus REST API client.

This module provides a typed async client for the Incus REST API,
communicating over the Unix socket at /var/lib/incus/unix.socket.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, RootModel

T = TypeVar("T", bound=BaseModel)

from .models_generated import (
    Image,
    ImageAliasesEntry,
    ImageAliasesPost,
    ImagesPost,
    ImagesPostSource,
    Instance,
    InstancesPost,
    InstanceState,
    InstanceStatePut,
    Operation,
    Server,
    ServerPut,
    StoragePool,
    StoragePoolsPost,
)


# List wrapper models for typed API responses
class InstanceList(RootModel[list[Instance]]):
    """List of Instance objects."""

    pass


class StringList(RootModel[list[str]]):
    """List of string URLs/paths."""

    pass


class StoragePoolList(RootModel[list[StoragePool]]):
    """List of StoragePool objects."""

    pass


class ImageList(RootModel[list[Image]]):
    """List of Image objects."""

    pass


class EmptyResponse(BaseModel):
    """Empty response from Incus API (for PUT/POST that return {})."""

    pass

    class Config:
        extra = "allow"  # Allow any fields since response may be empty dict


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


# Module-level singleton instance
_client: "IncusClient | None" = None


def get_client() -> "IncusClient":
    """Get the shared IncusClient instance."""
    global _client
    if _client is None:
        _client = IncusClient()
    return _client


class IncusClient:
    """Async client for Incus REST API over Unix socket."""

    def __init__(self, socket_path: str = "/var/lib/incus/unix.socket"):
        self._socket_path = socket_path
        self._client: httpx.AsyncClient | None = None

    @property
    def socket_path(self) -> str:
        """Path to the Incus Unix socket."""
        return self._socket_path

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
        self,
        method: str,
        path: str,
        *,
        response_type: type[T],
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
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
            timeout: Optional per-request timeout in seconds. Overrides the
                client default (30s). Use for long-polling endpoints like
                wait_operation where the server may hold the connection open
                for minutes.

        Returns:
            A validated instance of response_type.
        """
        client = await self._get_client()
        kwargs: dict[str, Any] = {}
        if json is not None:
            kwargs["json"] = json
        if timeout is not None:
            kwargs["timeout"] = timeout

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
            return response_type.model_validate(data)

        # Get metadata, defaulting to empty dict if None
        metadata = data.get("metadata")
        if metadata is None:
            metadata = {}
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

        containers: list[ContainerInfo] = []
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
        return await self._request(
            "GET", f"/1.0/instances/{name}", response_type=Instance
        )

    async def get_instance_state(self, name: str) -> InstanceState:
        """Get the runtime state of an instance.

        This returns live state information including the init PID,
        resource usage, and network details.

        Args:
            name: Instance name.

        Returns:
            InstanceState with pid, status, etc.
        """
        return await self._request(
            "GET", f"/1.0/instances/{name}/state", response_type=InstanceState
        )

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
    # Instance creation
    # -------------------------------------------------------------------------

    async def create_instance(
        self, instance: InstancesPost, wait: bool = False
    ) -> Operation:
        """Create a new instance (container or VM).

        Args:
            instance: Instance configuration.
            wait: If True, wait for the operation to complete.

        Returns:
            Operation with status info.
        """
        response = await self._request(
            "POST",
            "/1.0/instances",
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

        Uses Incus long-polling: the server holds the connection open until the
        operation finishes or the server-side timeout elapses. The httpx
        client-side timeout is set to ``timeout + 30`` seconds so it never
        expires before the server responds.

        Args:
            operation_id: Operation UUID.
            timeout: Server-side timeout in seconds.

        Returns:
            Operation object with final status.
        """
        return await self._request(
            "GET",
            f"/1.0/operations/{operation_id}/wait?timeout={timeout}",
            response_type=Operation,
            timeout=timeout + 30,
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
            "PUT",
            f"/1.0/instances/{name}/state",
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
            "DELETE",
            f"/1.0/instances/{name}",
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
            raise IncusError(
                f"Failed to push file {path}: {response.text}", response.status_code
            )

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
            raise IncusError(
                f"Failed to create symlink {path}: {response.text}",
                response.status_code,
            )

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
            raise IncusError(
                f"Failed to create directory {path}: {response.text}",
                response.status_code,
            )

    # -------------------------------------------------------------------------
    # Instance configuration
    # -------------------------------------------------------------------------

    async def patch_instance_config(
        self,
        name: str,
        config: dict[str, str],
    ) -> None:
        """Patch instance configuration (merge with existing config).

        Uses HTTP PATCH so only the ``config`` field is sent, avoiding
        accidental overwrites of ``devices`` or other fields that may
        have been changed by preceding pipeline steps.

        Args:
            name: Instance name.
            config: Config keys to add/update.
        """
        await self._request(
            "PATCH",
            f"/1.0/instances/{name}",
            response_type=EmptyResponse,
            json={"config": config},
        )

    async def add_instance_device(
        self,
        name: str,
        device_name: str,
        device_config: dict[str, str],
    ) -> None:
        """Add a device to an instance.

        Uses HTTP PATCH so only the ``devices`` field is sent, avoiding
        accidental overwrites of ``config`` or other fields that may
        have been changed by preceding pipeline steps.

        Args:
            name: Instance name.
            device_name: Name for the device.
            device_config: Device configuration (type, source, path, etc.).
        """
        await self._request(
            "PATCH",
            f"/1.0/instances/{name}",
            response_type=EmptyResponse,
            json={"devices": {device_name: device_config}},
        )

    # -------------------------------------------------------------------------
    # Storage pool operations
    # -------------------------------------------------------------------------

    async def list_storage_pools(self, recursion: int = 1) -> list[StoragePool]:
        """List all storage pools.

        Args:
            recursion: 0 returns just URLs, 1 returns full objects.

        Returns:
            List of StoragePool objects.
        """
        if recursion == 0:
            result = await self._request(
                "GET", "/1.0/storage-pools", response_type=StringList
            )
            return [
                StoragePool(
                    name=url.split("/")[-1],
                    config=None,
                    description=None,
                    driver=None,
                    locations=None,
                    status=None,
                    used_by=None,
                )
                for url in result.root
            ]

        result = await self._request(
            "GET",
            f"/1.0/storage-pools?recursion={recursion}",
            response_type=StoragePoolList,
        )
        return result.root

    async def storage_pool_exists(self, name: str) -> bool:
        """Check if a storage pool exists.

        Args:
            name: Storage pool name.

        Returns:
            True if the storage pool exists.
        """
        pools = await self.list_storage_pools(recursion=0)
        return any(p.name == name for p in pools)

    async def create_storage_pool(
        self, name: str, driver: str, config: dict[str, str] | None = None
    ) -> None:
        """Create a new storage pool.

        Args:
            name: Storage pool name.
            driver: Storage driver (btrfs, dir, zfs, lvm, etc.).
            config: Optional configuration map.
        """
        pool = StoragePoolsPost(
            name=name,
            driver=driver,
            config=config,
            description=None,
        )
        await self._request(
            "POST",
            "/1.0/storage-pools",
            response_type=EmptyResponse,
            json=pool.model_dump(exclude_none=True),
        )

    # -------------------------------------------------------------------------
    # Image operations
    # -------------------------------------------------------------------------

    async def list_images(self, recursion: int = 1) -> list[Image]:
        """List all images.

        Args:
            recursion: 0 returns just URLs, 1 returns full objects.

        Returns:
            List of Image objects.
        """
        result = await self._request(
            "GET",
            f"/1.0/images?recursion={recursion}",
            response_type=ImageList,
        )
        return result.root

    async def refresh_image(self, fingerprint: str) -> str:
        """Trigger an immediate refresh of a cached image from its upstream source.

        This is an async Incus operation. The image must have auto_update=True
        and a valid update_source.

        Args:
            fingerprint: Full SHA-256 fingerprint of the image.

        Returns:
            Operation ID for the refresh operation.
        """
        response = await self._request(
            "POST",
            f"/1.0/images/{fingerprint}/refresh",
            response_type=AsyncOperationResponse,
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if not operation.id:
            raise IncusError("No operation ID in response")

        return operation.id

    async def download_remote_image(
        self,
        server: str,
        protocol: str,
        alias: str,
        *,
        auto_update: bool = True,
    ) -> str:
        """Download an image from a remote server into the local image store.

        Issues ``POST /1.0/images`` with a pull-mode source so Incus
        fetches the image directly from the remote simplestreams (or LXD)
        server.

        Args:
            server: URL of the remote image server.
            protocol: Transfer protocol (``"simplestreams"`` or ``"lxd"``).
            alias: Image alias to pull from the remote server.
            auto_update: Whether the cached image should auto-update.

        Returns:
            Operation ID for the download operation.
        """
        body = ImagesPost(
            auto_update=auto_update,
            source=ImagesPostSource(
                type="image",
                mode="pull",
                server=server,
                protocol=protocol,
                alias=alias,
                certificate=None,
                fingerprint=None,
                image_type=None,
                name=None,
                project=None,
                secret=None,
                url=None,
            ),
            aliases=None,
            compression_algorithm=None,
            expires_at=None,
            filename=None,
            format=None,
            profiles=None,
            properties=None,
            public=None,
        )

        response = await self._request(
            "POST",
            "/1.0/images",
            response_type=AsyncOperationResponse,
            json=body.model_dump(exclude_none=True),
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if not operation.id:
            raise IncusError("No operation ID in response")

        return operation.id

    async def import_image(
        self, meta_path: Path, rootfs_path: Path, aliases: list[str]
    ) -> str:
        """Import a split image (metadata tarball + rootfs) into Incus.

        Uploads via multipart/form-data with two file parts. Bypasses
        ``_request()`` since it only handles JSON bodies.

        Args:
            meta_path: Path to the metadata tarball (e.g., ``incus.tar.xz``).
            rootfs_path: Path to the rootfs file (e.g., ``rootfs.squashfs``).
            aliases: List of alias names to assign to the image.

        Returns:
            The SHA-256 fingerprint of the imported image.
        """
        client = await self._get_client()
        response = await client.post(
            "/1.0/images",
            files={
                "metadata": (
                    meta_path.name,
                    meta_path.read_bytes(),
                    "application/octet-stream",
                ),
                "rootfs": (
                    rootfs_path.name,
                    rootfs_path.read_bytes(),
                    "application/octet-stream",
                ),
            },
        )

        if response.status_code >= 400:
            raise IncusError(
                f"Failed to import image: {response.text}",
                response.status_code,
            )

        data = response.json()
        if data.get("type") == "error":
            raise IncusError(
                data.get("error", "Unknown error"),
                data.get("error_code"),
            )

        op_response = AsyncOperationResponse.model_validate(data)
        operation = op_response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if operation.id:
            operation = await self.wait_operation(operation.id, timeout=300)

        if operation.metadata is None or "fingerprint" not in operation.metadata:
            raise IncusError("No fingerprint in operation metadata")

        fingerprint = operation.metadata["fingerprint"]

        # Create aliases as separate API calls since the X-Incus-aliases
        # header is not reliably applied during multipart uploads.
        for alias in aliases:
            await self.create_image_alias(alias, fingerprint)

        return fingerprint

    async def create_image_alias(self, alias: str, fingerprint: str) -> None:
        """Create an alias for an image.

        Args:
            alias: Alias name to create.
            fingerprint: Full SHA-256 fingerprint of the target image.
        """
        client = await self._get_client()
        body = ImageAliasesPost(
            name=alias,
            target=fingerprint,
            description=None,
            type=None,
        )
        response = await client.post(
            "/1.0/images/aliases",
            json=body.model_dump(exclude_none=True),
        )

        if response.status_code >= 400:
            raise IncusError(
                f"Failed to create alias '{alias}': {response.text}",
                response.status_code,
            )

    async def delete_image(self, fingerprint: str) -> None:
        """Delete an image by fingerprint.

        Args:
            fingerprint: Full SHA-256 fingerprint of the image to delete.
        """
        response = await self._request(
            "DELETE",
            f"/1.0/images/{fingerprint}",
            response_type=AsyncOperationResponse,
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if operation.id:
            await self.wait_operation(operation.id)

    async def get_image_fingerprint_by_alias(self, alias: str) -> str | None:
        """Look up an image fingerprint by alias name.

        Args:
            alias: Image alias name to look up.

        Returns:
            The fingerprint string if the alias exists, or ``None`` if not found.
        """
        try:
            entry = await self._request(
                "GET",
                f"/1.0/images/aliases/{alias}",
                response_type=ImageAliasesEntry,
            )
            return entry.target
        except IncusError as exc:
            if exc.code == 404:
                return None
            raise

    async def get_image(self, fingerprint: str) -> Image:
        """Get a single image by fingerprint.

        Args:
            fingerprint: Full SHA-256 fingerprint of the image.

        Returns:
            Image object with full details including properties.
        """
        return await self._request(
            "GET",
            f"/1.0/images/{fingerprint}",
            response_type=Image,
        )

    async def download_image(
        self,
        source: ImagesPostSource,
        auto_update: bool = True,
    ) -> tuple[Image | None, str | None]:
        """Download a remote image into the local store.

        Triggers Incus to pull the image from the given source (e.g. a
        simplestreams server) and cache it locally.  Returns a tuple of
        ``(image, operation_id)`` — if the image is already cached, returns
        ``(image, None)``; if a download was started, returns
        ``(None, operation_id)`` so the caller can track progress.

        If the image is already cached locally (same alias from the same
        server), the existing image is returned instead of re-downloading.

        Args:
            source: Image source descriptor (protocol, server, alias).
            auto_update: Mark the image for automatic updates.

        Returns:
            Tuple of (Image, None) if cached, or (None, operation_id) if
            a download was started.
        """
        # Check if the image is already cached locally by matching
        # alias + server in the local image store.
        if source.alias:
            for img in await self.list_images():
                if (
                    img.update_source
                    and img.update_source.alias == source.alias
                    and img.update_source.server == source.server
                    and img.fingerprint
                ):
                    return img, None

        body = ImagesPost(
            auto_update=auto_update,
            source=source,
            aliases=None,
            compression_algorithm=None,
            expires_at=None,
            filename=None,
            format=None,
            profiles=None,
            properties=None,
            public=None,
        )

        response = await self._request(
            "POST",
            "/1.0/images",
            response_type=AsyncOperationResponse,
            json=body.model_dump(exclude_none=True),
        )

        operation = response.metadata
        if operation is None:
            raise IncusError("No operation metadata in response")

        if not operation.id:
            raise IncusError("No operation ID in response")

        return None, operation.id

    # -------------------------------------------------------------------------
    # Server configuration
    # -------------------------------------------------------------------------

    async def get_server(self) -> Server:
        """Get server information and configuration.

        Returns:
            Server object with config and environment info.
        """
        return await self._request("GET", "/1.0", response_type=Server)

    async def set_server_config(self, key: str, value: str) -> None:
        """Set a server configuration value.

        Args:
            key: Configuration key.
            value: Configuration value.
        """
        # Get current config to merge
        server = await self.get_server()
        current_config = server.config or {}
        new_config = {**current_config, key: value}

        put_data = ServerPut(config=new_config)
        await self._request(
            "PUT",
            "/1.0",
            response_type=EmptyResponse,
            json=put_data.model_dump(exclude_none=True),
        )
