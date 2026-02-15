# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Container lifecycle operations for the Kapsule daemon.

This module implements the core container management operations,
using the operation decorator for automatic progress reporting.

Container creation and user setup are structured as pipelines of
step functions.  Each step receives a context dataclass, checks its
own preconditions, and performs one concern.  This keeps individual
steps small and makes adding new features straightforward — decorate
a function with the pipeline's ``step`` decorator to register it.
"""

from __future__ import annotations

import logging
import os
import pwd
import subprocess
from typing import TYPE_CHECKING

from ..config import load_config
from ..container_options import ContainerOptions
from ..incus_client import IncusClient, IncusError
from ..models_generated import InstanceSource, InstancesPost
from ..operations import OperationError, OperationReporter, OperationTracker, operation
from .config_helpers import base_container_config, base_container_devices, store_option_metadata
from .constants import (
    ENTER_ENV_SKIP,
    KAPSULE_DBUS_MUX_KEY,
    KAPSULE_SESSION_MODE_KEY,
    NVIDIA_HOOK_PATH,
    BindMount,
)
from .contexts import PostCreateContext, UserSetupContext
from .post_create import post_create_pipeline
from .user_setup import user_setup_pipeline

if TYPE_CHECKING:
    from ..service import KapsuleManagerInterface
    from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)


class ContainerService:
    """Container lifecycle operations exposed over D-Bus.

    Each public method decorated with @operation returns a D-Bus object
    path for the operation. Clients subscribe to signals on that object
    for progress updates.
    """

    def __init__(
        self,
        interface: "KapsuleManagerInterface",
        incus: IncusClient,
    ):
        """Initialize the container service.

        Args:
            interface: D-Bus interface for emitting signals
            incus: Incus API client
        """
        self._interface = interface
        self._incus = incus
        self._tracker = OperationTracker()

        # Cache for runtime bind mounts.
        # Key: (container_name, uid)
        # Value: (started_at_iso, env_fingerprint)
        # Invalidated when the container restarts (started_at changes)
        # or the relevant env vars change (different WAYLAND_DISPLAY, etc.).
        self._mount_cache: dict[tuple[str, int], tuple[str, str]] = {}

    def set_bus(self, bus: MessageBus) -> None:
        """Set the message bus for operation object export.

        Must be called after initialization to enable D-Bus operation objects.
        """
        self._tracker.set_bus(bus)

    def list_operations(self) -> list[str]:
        """List D-Bus object paths of all running operations."""
        return self._tracker.list_paths()

    # -------------------------------------------------------------------------
    # Pipeline runners
    # -------------------------------------------------------------------------

    async def _run_post_create(
        self,
        name: str,
        opts: ContainerOptions,
        progress: OperationReporter | None = None,
    ) -> None:
        """Run the post-creation setup pipeline."""
        ctx = PostCreateContext(
            name=name, opts=opts, incus=self._incus, progress=progress,
        )
        await post_create_pipeline.run(ctx)

    async def _run_user_setup(
        self,
        container_name: str,
        uid: int,
        gid: int,
        username: str,
        home_dir: str,
        progress: OperationReporter | None = None,
    ) -> None:
        """Run the user setup pipeline."""
        instance = await self._incus.get_instance(container_name)
        ctx = UserSetupContext(
            container_name=container_name,
            uid=uid,
            gid=gid,
            username=username,
            home_dir=home_dir,
            container_home=f"/home/{os.path.basename(home_dir)}",
            instance_config=instance.config or {},
            incus=self._incus,
            progress=progress,
        )
        await user_setup_pipeline.run(ctx)

    # -------------------------------------------------------------------------
    # Container Lifecycle Operations
    # -------------------------------------------------------------------------

    @operation(
        "create",
        description="Creating container: {name}",
        target_param="name",
    )
    async def create_container(
        self,
        progress: OperationReporter,
        *,
        name: str,
        image: str,
        options: ContainerOptions | None = None,
    ) -> None:
        """Create a new container.

        Args:
            progress: Operation reporter (auto-injected)
            name: Container name
            image: Image to use (e.g., "images:archlinux")
            options: Validated container options. If None, schema defaults
                are used (all features enabled).
        """
        opts = options or ContainerOptions()

        # Check if container already exists
        if await self._incus.instance_exists(name):
            raise OperationError(f"Container '{name}' already exists")

        progress.info(f"Image: {image}")
        if not opts.host_rootfs:
            progress.info("Minimal host mounts (no full rootfs)")
        if not opts.mount_home:
            progress.info("Home directory mount: disabled")
        if opts.custom_mounts:
            progress.info(f"Custom mounts: {', '.join(opts.custom_mounts)}")
        if not opts.gpu:
            progress.info("GPU passthrough: disabled")
        if opts.gpu and not opts.nvidia_drivers:
            progress.info("NVIDIA driver injection: disabled")

        # Parse image source
        instance_source = self._parse_image_source(image)
        if instance_source is None:
            raise OperationError(f"Invalid image format: {image}")

        # Build instance config — base settings + option metadata
        instance_config_dict = base_container_config(
            nvidia_drivers=opts.gpu and opts.nvidia_drivers,
        )
        store_option_metadata(instance_config_dict, opts)

        instance_config = InstancesPost(
            name=name,
            profiles=[],
            source=instance_source,
            start=True,
            architecture=None,
            config=instance_config_dict,
            description=None,
            devices=base_container_devices(host_rootfs=opts.host_rootfs, gpu=opts.gpu),
            ephemeral=None,
            instance_type=None,
            restore=None,
            stateful=None,
            type=None,
        )

        # Create the container
        if NVIDIA_HOOK_PATH in instance_config_dict.get("raw.lxc", ""):
            progress.dim("NVIDIA userspace drivers will be injected on start")

        progress.info("Downloading image and creating container...")
        try:
            operation = await self._incus.create_instance(instance_config, wait=True)
            if operation.status != "Success":
                raise OperationError(f"Creation failed: {operation.err or operation.status}")
        except IncusError as e:
            raise OperationError(f"Failed to create container: {e}")

        # Post-create pipeline
        await self._run_post_create(name, opts, progress)

        progress.success(f"Container '{name}' created successfully")

    @operation(
        "delete",
        description="Removing container: {name}",
        target_param="name",
    )
    async def delete_container(
        self,
        progress: OperationReporter,
        *,
        name: str,
        force: bool = False,
    ) -> None:
        """Delete a container.

        Args:
            progress: Operation reporter (auto-injected)
            name: Container name
            force: Force removal even if running
        """
        # Check existence
        if not await self._incus.instance_exists(name):
            raise OperationError(f"Container '{name}' does not exist")

        instance = await self._incus.get_instance(name)
        is_running = instance.status and instance.status.lower() == "running"

        if is_running and not force:
            raise OperationError(f"Container '{name}' is running. Use force=True to remove anyway.")

        if is_running:
            progress.info("Stopping container...")
            try:
                op = await self._incus.stop_instance(name, force=True, wait=True)
                if op.status != "Success":
                    raise OperationError(f"Failed to stop: {op.err or op.status}")
            except IncusError as e:
                raise OperationError(f"Failed to stop container: {e}")
            progress.success("Container stopped")

        progress.info("Deleting container...")
        try:
            op = await self._incus.delete_instance(name, wait=True)
            if op.status != "Success":
                raise OperationError(f"Deletion failed: {op.err or op.status}")
        except IncusError as e:
            raise OperationError(f"Failed to delete container: {e}")

        progress.success(f"Container '{name}' removed successfully")

    @operation(
        "start",
        description="Starting container: {name}",
        target_param="name",
    )
    async def start_container(
        self,
        progress: OperationReporter,
        *,
        name: str,
    ) -> None:
        """Start a stopped container.

        Args:
            progress: Operation reporter (auto-injected)
            name: Container name
        """
        if not await self._incus.instance_exists(name):
            raise OperationError(f"Container '{name}' does not exist")

        instance = await self._incus.get_instance(name)
        if instance.status and instance.status.lower() == "running":
            progress.warning(f"Container '{name}' is already running")
            return

        raw_lxc = (instance.config or {}).get("raw.lxc", "")
        if NVIDIA_HOOK_PATH in raw_lxc:
            progress.dim("NVIDIA userspace drivers will be injected on start")

        progress.info("Starting container...")
        try:
            op = await self._incus.start_instance(name, wait=True)
            if op.status != "Success":
                raise OperationError(f"Start failed: {op.err or op.status}")
        except IncusError as e:
            raise OperationError(f"Failed to start container: {e}")

        progress.success(f"Container '{name}' started successfully")

    @operation(
        "stop",
        description="Stopping container: {name}",
        target_param="name",
    )
    async def stop_container(
        self,
        progress: OperationReporter,
        *,
        name: str,
        force: bool = False,
    ) -> None:
        """Stop a running container.

        Args:
            progress: Operation reporter (auto-injected)
            name: Container name
            force: Force stop
        """
        if not await self._incus.instance_exists(name):
            raise OperationError(f"Container '{name}' does not exist")

        instance = await self._incus.get_instance(name)
        if instance.status and instance.status.lower() != "running":
            progress.warning(f"Container '{name}' is not running")
            return

        progress.info("Stopping container...")
        try:
            op = await self._incus.stop_instance(name, force=force, wait=True)
            if op.status != "Success":
                raise OperationError(f"Stop failed: {op.err or op.status}")
        except IncusError as e:
            raise OperationError(f"Failed to stop container: {e}")

        progress.success(f"Container '{name}' stopped successfully")

    # -------------------------------------------------------------------------
    # User Setup Operations
    # -------------------------------------------------------------------------

    @operation(
        "setup_user",
        description="Setting up user '{username}' in {container_name}",
        target_param="container_name",
    )
    async def setup_user(
        self,
        progress: OperationReporter,
        *,
        container_name: str,
        uid: int,
        gid: int,
        username: str,
        home_dir: str,
    ) -> None:
        """Set up a host user in a container.

        This mounts the user's home directory and creates a matching
        user account in the container with passwordless sudo.

        Args:
            progress: Operation reporter (auto-injected)
            container_name: Container name
            uid: User ID
            gid: Group ID
            username: Username
            home_dir: Path to home directory on host
        """
        await self._run_user_setup(
            container_name, uid, gid, username, home_dir, progress,
        )
        progress.success(f"User '{username}' configured")

    # -------------------------------------------------------------------------
    # Query Methods (non-operation, synchronous response)
    # -------------------------------------------------------------------------

    async def list_containers(self) -> list[tuple[str, str, str, str, str]]:
        """List all containers.

        Returns:
            List of (name, status, image, created, kapsule_mode) tuples
        """
        containers = await self._incus.list_containers()
        result: list[tuple[str, str, str, str, str]] = []
        for c in containers:
            # Get kapsule mode from instance config
            try:
                instance = await self._incus.get_instance(c.name)
                config = instance.config or {}
                if config.get(KAPSULE_DBUS_MUX_KEY) == "true":
                    mode = "DbusMux"
                elif config.get(KAPSULE_SESSION_MODE_KEY) == "true":
                    mode = "Session"
                else:
                    mode = "Default"
            except IncusError:
                mode = "unknown"

            result.append((c.name, c.status, c.image, c.created, mode))
        return result

    async def get_container_info(self, name: str) -> tuple[str, str, str, str, str]:
        """Get container information.

        Args:
            name: Container name

        Returns:
            Tuple of (name, status, image, created, mode)
        """
        try:
            instance = await self._incus.get_instance(name)
        except IncusError as e:
            raise OperationError(f"Container '{name}' not found: {e}")

        config = instance.config or {}

        # Determine kapsule mode
        if config.get(KAPSULE_DBUS_MUX_KEY) == "true":
            mode = "DbusMux"
        elif config.get(KAPSULE_SESSION_MODE_KEY) == "true":
            mode = "Session"
        else:
            mode = "Default"

        image = config.get("image.description", config.get("image.os", "unknown"))

        return (
            instance.name or name,
            instance.status or "Unknown",
            image,
            instance.created_at.isoformat() if instance.created_at else "",
            mode,
        )

    async def is_user_setup(self, container_name: str, uid: int) -> bool:
        """Check if a user is already set up in a container.

        Args:
            container_name: Container name
            uid: User ID to check

        Returns:
            True if user is set up
        """
        try:
            instance = await self._incus.get_instance(container_name)
            config = instance.config or {}
            return config.get(f"user.kapsule.host-users.{uid}.mapped") == "true"
        except IncusError:
            return False

    async def get_config(self, uid: int) -> dict[str, str]:
        """Get user configuration.

        Args:
            uid: User ID to load config for

        Returns:
            Dictionary with config keys and values
        """
        # Get user info from UID
        try:
            pw_entry = pwd.getpwuid(uid)
            home_dir = pw_entry.pw_dir
        except KeyError:
            return {"error": f"User with UID {uid} not found"}

        # Load config using caller's home for XDG paths
        config = load_config(home_dir=home_dir)

        return {
            "default_container": config.default_container,
            "default_image": config.default_image,
        }

    async def prepare_enter(
        self,
        uid: int,
        gid: int,
        container_name: str | None,
        command: list[str],
        env: dict[str, str],
    ) -> tuple[bool, str, list[str]]:
        """Prepare everything needed to enter a container.

        This method handles all the setup logic for entering a container:
        - Resolves the container name from config if not specified
        - Creates the default container if it doesn't exist
        - Starts the container if needed
        - Sets up the user if needed
        - Configures runtime directory symlinks
        - Builds the full command to execute

        Args:
            uid: Caller's user ID (from D-Bus credentials)
            gid: Caller's group ID
            container_name: Container to enter, or None for default
            command: Command to run inside container (empty for shell)
            env: Environment variables from the caller

        Returns:
            Tuple of (success, message, command_array)
            On success: (True, "", ["incus", "exec", ...])
            On failure: (False, "error message", [])
        """
        # Get user info from UID
        try:
            pw_entry = pwd.getpwuid(uid)
            username = pw_entry.pw_name
            home_dir = pw_entry.pw_dir
        except KeyError:
            return (False, f"User with UID {uid} not found", [])

        # Load config for defaults (using caller's home for XDG paths)
        config = load_config(home_dir=home_dir)

        # Use default container name if not specified
        if not container_name:
            container_name = config.default_container

        # Check if container exists
        container_exists = await self._incus.instance_exists(container_name)

        if not container_exists:
            # Only auto-create if using default container
            if container_name == config.default_container:
                # Create the container (this is a synchronous operation here)
                try:
                    await self._create_default_container(
                        container_name, config.default_image
                    )
                except OperationError as e:
                    return (False, str(e), [])
            else:
                return (False, f"Container '{container_name}' does not exist", [])

        # Check container status
        instance = await self._incus.get_instance(container_name)
        status = (instance.status or "unknown").lower()

        if status != "running":
            # Start the container
            try:
                op = await self._incus.start_instance(container_name, wait=True)
                if op.status != "Success":
                    return (False, f"Failed to start container: {op.err or op.status}", [])
            except IncusError as e:
                return (False, f"Failed to start container: {e}", [])

        # Set up user if needed
        if not await self.is_user_setup(container_name, uid):
            try:
                await self._run_user_setup(
                    container_name, uid, gid, username, home_dir,
                )
            except OperationError as e:
                return (False, str(e), [])

        # Set up runtime directory symlinks
        try:
            await self._setup_runtime_symlinks(container_name, uid, gid, env)
        except OperationError as e:
            return (False, str(e), [])

        # Build environment arguments
        env_args: list[str] = []
        whitelist_keys: list[str] = []
        for key, value in env.items():
            if key in ENTER_ENV_SKIP:
                continue
            if "\n" in value or "\x00" in value:
                continue
            env_args.extend(["--env", f"{key}={value}"])
            whitelist_keys.append(key)

        # Build the command to run inside the container.
        #
        # Always use su -l for consistent behavior whether entering a
        # shell or running a command. su -l provides:
        #   - PAM session setup (pam_systemd, etc.)
        #   - Supplementary group resolution via initgroups()
        #   - Login shell profile sourcing (.bash_profile, etc.)
        #
        # The -w flag whitelists env vars passed via incus exec --env,
        # preventing su -l from clearing vars like XDG_RUNTIME_DIR
        # that are needed for PulseAudio/PipeWire socket discovery.
        whitelist_arg = ",".join(whitelist_keys) if whitelist_keys else ""
        if command:
            exec_cmd = ["su", "-l", "-w", whitelist_arg, "-c", " ".join(command), username]
        else:
            exec_cmd = ["su", "-l", "-w", whitelist_arg, username]

        # Build full incus exec command
        exec_args = [
            "incus",
            "exec",
            container_name,
            *env_args,
            "--",
            *exec_cmd,
        ]

        return (True, "", exec_args)

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    async def _create_default_container(self, name: str, image: str) -> None:
        """Create the default container without progress reporting.

        Args:
            name: Container name
            image: Image to use
        """
        opts = ContainerOptions()

        # Parse image source
        instance_source = self._parse_image_source(image)
        if instance_source is None:
            raise OperationError(f"Invalid image format: {image}")

        # Build instance config — base settings + option metadata
        instance_config_dict = base_container_config(
            nvidia_drivers=opts.gpu and opts.nvidia_drivers,
        )
        store_option_metadata(instance_config_dict, opts)

        instance_config = InstancesPost(
            name=name,
            profiles=None,
            source=instance_source,
            start=True,
            architecture=None,
            config=instance_config_dict,
            description=None,
            devices=base_container_devices(
                host_rootfs=opts.host_rootfs, gpu=opts.gpu,
            ),
            ephemeral=None,
            instance_type=None,
            restore=None,
            stateful=None,
            type=None,
        )

        try:
            operation = await self._incus.create_instance(instance_config, wait=True)
            if operation.status != "Success":
                raise OperationError(
                    f"Creation failed: {operation.err or operation.status}",
                )
        except IncusError as e:
            raise OperationError(f"Failed to create container: {e}")

        # Same post-create pipeline, silent
        await self._run_post_create(name, opts)

    @staticmethod
    def _mount_env_fingerprint(env: dict[str, str]) -> str:
        """Return a fingerprint of environment keys that affect mount setup.

        Used as part of the cache key so that mounts are re-done when the
        caller's relevant env vars change (e.g. different WAYLAND_DISPLAY
        or a new XAUTHORITY file).
        """
        keys = ("WAYLAND_DISPLAY", "DISPLAY", "XAUTHORITY")
        return "|".join(f"{k}={env.get(k, '')}" for k in keys)

    async def _setup_runtime_symlinks(
        self,
        container_name: str,
        uid: int,
        gid: int,
        env: dict[str, str],
    ) -> None:
        """Set up runtime directory bind mounts for graphics/audio access.

        Bind-mounts individual sockets from the host's /run/user/$uid (via
        hostfs) into the container's /run/user/$uid directory.  Bind mounts
        are used instead of symlinks so that applications running inside
        their own mount namespace (such as snap packages) can access the
        sockets correctly — snap-update-ns cannot follow symlinks that
        point into /.kapsule/host/.

        Results are cached per (container, uid) and invalidated when the
        container restarts or the relevant env vars change (different
        WAYLAND_DISPLAY, etc.).

        In session mode, the dbus socket is not mounted (the container has
        its own D-Bus session).

        Args:
            container_name: Container name
            uid: User ID
            gid: Group ID
            env: Environment variables (for WAYLAND_DISPLAY etc)
        """
        # --- Cache check ---------------------------------------------------
        state = await self._incus.get_instance_state(container_name)
        started_at = state.started_at.isoformat() if state.started_at else ""
        env_fp = self._mount_env_fingerprint(env)
        cache_key = (container_name, uid)

        cached = self._mount_cache.get(cache_key)
        if cached == (started_at, env_fp):
            return  # Mounts already set up for this boot + env

        # --- Gather mount list --------------------------------------------
        instance = await self._incus.get_instance(container_name)
        instance_config = instance.config or {}
        session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"

        runtime_dir = f"/run/user/{uid}"
        host_runtime_dir = f"/.kapsule/host/run/user/{uid}"

        # Ensure container runtime dirs exist
        try:
            await self._incus.mkdir(container_name, "/run/user", uid=0, gid=0, mode="0255")
        except IncusError:
            pass
        try:
            await self._incus.mkdir(container_name, runtime_dir, uid=uid, gid=gid, mode="0700")
        except IncusError:
            pass

        # Collect all mount descriptors.
        mounts: list[BindMount] = []

        # Runtime sockets from host runtime dir
        # Format: (item, is_env_var, source_subpath_override)
        runtime_links: list[tuple[str, bool, str | None]] = [
            ("WAYLAND_DISPLAY", True, None),
            ("pipewire-0", False, None),
        ]

        # D-Bus socket handling:
        # - Default mode: bind-mount host's session bus so container sees host services
        # - Session mode (any): no mount — container has its own D-Bus session.
        #   Without mux, systemd's dbus.socket creates /run/user/$uid/bus natively.
        #   With mux, the mux service listens at /run/user/$uid/bus.
        if not session_mode:
            runtime_links.append(("bus", False, None))

        for item, is_env, subpath in runtime_links:
            if is_env:
                socket_name = env.get(item)
                if not socket_name:
                    continue
            else:
                socket_name = item

            source = f"{host_runtime_dir}/{subpath if subpath else socket_name}"
            target = f"{runtime_dir}/{socket_name}"
            mounts.append(BindMount(source=source, target=target, uid=uid, gid=gid))

        # X11: bind-mount the individual socket from the host's /tmp/.X11-unix/
        display = env.get("DISPLAY", "")
        if display.startswith(":"):
            display_num = display.lstrip(":").split(".")[0]  # ":0.0" -> "0"
            x11_socket = f"X{display_num}"
            host_x11 = f"/.kapsule/host/tmp/.X11-unix/{x11_socket}"
            container_x11_dir = "/tmp/.X11-unix"
            try:
                await self._incus.mkdir(
                    container_name, container_x11_dir, uid=0, gid=0, mode="1777",
                )
            except IncusError:
                pass
            mounts.append(BindMount(
                source=host_x11,
                target=f"{container_x11_dir}/{x11_socket}",
                uid=0,
                gid=0,
            ))

        # PulseAudio: create a real pulse/ directory and bind-mount native inside.
        # PulseAudio refuses to use pulse/ if it's itself a symlink (security check).
        pulse_dir = f"{runtime_dir}/pulse"
        host_pulse_native = f"{host_runtime_dir}/pulse/native"
        try:
            await self._incus.mkdir(container_name, pulse_dir, uid=uid, gid=gid, mode="0700")
        except IncusError:
            pass
        mounts.append(BindMount(
            source=host_pulse_native,
            target=f"{pulse_dir}/native",
            uid=uid,
            gid=gid,
        ))

        # XAUTHORITY: the env value is a full path (e.g. /run/user/1000/xauth_LAPpeP).
        # Bind-mount just the basename inside the container's runtime dir to the
        # corresponding host file via hostfs.
        xauth_path = env.get("XAUTHORITY", "")
        if xauth_path:
            xauth_basename = os.path.basename(xauth_path)
            host_xauth = f"{host_runtime_dir}/{xauth_basename}"
            target_xauth = f"{runtime_dir}/{xauth_basename}"
            mounts.append(BindMount(source=host_xauth, target=target_xauth, uid=uid, gid=gid))

        # --- Execute all mounts via nsenter into the container namespace ---
        if mounts and state.pid:
            self._bind_mount_batch(state.pid, mounts)

        # Update cache
        self._mount_cache[cache_key] = (started_at, env_fp)

    @staticmethod
    def _bind_mount_batch(
        container_pid: int,
        mounts: list[BindMount],
    ) -> None:
        """Bind-mount multiple host files/sockets into a container.

        Uses ``nsenter`` to enter the container's mount namespace directly,
        bypassing the Incus API.  This is ~10-20x faster than ``incus exec``
        because it avoids the CLI→REST→WebSocket→fork chain.

        For each mount descriptor the script:
          1. Skips if target is already a mount point
          2. Removes any stale symlink at target
          3. Skips if the source doesn't exist (host socket absent)
          4. Creates a mount-point file and bind-mounts

        Args:
            container_pid: PID of the container's init process
                (from InstanceState.pid).
            mounts: List of bind-mount descriptors.
        """
        # Build a self-contained sh script that reads quad-tuples from args.
        # Usage: sh -c '<script>' sh src1 tgt1 uid1 gid1 src2 tgt2 uid2 gid2 ...
        script = (
            'while [ $# -ge 4 ]; do '
            'src=$1; tgt=$2; u=$3; g=$4; shift 4; '
            'mountpoint -q "$tgt" 2>/dev/null && continue; '
            'rm -f "$tgt"; '
            '[ -e "$src" ] || continue; '
            'touch "$tgt" && chown "$u:$g" "$tgt" && '
            'mount --bind "$src" "$tgt"; '
            'done'
        )

        args: list[str] = []
        for mount in mounts:
            args.extend([mount.source, mount.target, str(mount.uid), str(mount.gid)])

        subprocess.run(
            [
                "nsenter", "-t", str(container_pid), "-m", "--",
                "sh", "-c", script, "sh", *args,
            ],
            capture_output=True,
        )

    def _parse_image_source(self, image: str) -> InstanceSource | None:
        """Parse an image string into an InstanceSource.

        Args:
            image: Image string like "images:archlinux" or "ubuntu:24.04"

        Returns:
            InstanceSource or None if invalid
        """
        # Map common server aliases to URLs
        server_map = {
            "images": "https://images.linuxcontainers.org",
            "ubuntu": "https://cloud-images.ubuntu.com/releases",
        }

        if ":" in image:
            server_alias, image_alias = image.split(":", 1)
            server_url = server_map.get(server_alias)
            if not server_url:
                return None
        else:
            server_url = "https://images.linuxcontainers.org"
            image_alias = image

        return InstanceSource(
            type="image",
            protocol="simplestreams",
            server=server_url,
            alias=image_alias,
            allow_inconsistent=None,
            certificate=None,
            fingerprint=None,
            instance_only=None,
            live=None,
            mode=None,
            operation=None,
            project=None,
            properties=None,
            refresh=None,
            refresh_exclude_older=None,
            secret=None,
            secrets=None,
            source=None,
            **{"base-image": None},
        )
