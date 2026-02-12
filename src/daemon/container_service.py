# SPDX-FileCopyrightText: 2026 Lasath Fernando <devel@lasath.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Container lifecycle operations for the Kapsule daemon.

This module implements the core container management operations,
using the operation decorator for automatic progress reporting.
"""

from __future__ import annotations

import logging
import os
import pwd
import subprocess
from typing import TYPE_CHECKING

from .config import load_config
from .operations import OperationError, OperationReporter, OperationTracker, operation

if TYPE_CHECKING:
    from .service import KapsuleManagerInterface
    from dbus_fast.aio import MessageBus

# Import Incus client and models from local modules
from .incus_client import IncusClient, IncusError
from .models_generated import InstanceSource, InstancesPost


# Config keys for kapsule metadata stored in container config
KAPSULE_SESSION_MODE_KEY = "user.kapsule.session-mode"
KAPSULE_DBUS_MUX_KEY = "user.kapsule.dbus-mux"
KAPSULE_HOST_ROOTFS_KEY = "user.kapsule.host-rootfs"
KAPSULE_GPU_KEY = "user.kapsule.gpu"
KAPSULE_NVIDIA_DRIVERS_KEY = "user.kapsule.nvidia-drivers"

logger = logging.getLogger(__name__)

# Absolute path to the NVIDIA container hook script (installed by CMake)
NVIDIA_HOOK_PATH = "/usr/lib/kapsule/nvidia-container-hook.sh"

# Path to kapsule-dbus-mux binary inside container (via hostfs mount)
KAPSULE_DBUS_MUX_BIN = "/.kapsule/host/usr/lib/kapsule/kapsule-dbus-mux"

# D-Bus socket path template using %t (systemd specifier for XDG_RUNTIME_DIR)
KAPSULE_DBUS_SOCKET_USER_PATH = "kapsule/{container}/dbus.socket"
KAPSULE_DBUS_SOCKET_SYSTEMD = "/.kapsule/host%t/" + KAPSULE_DBUS_SOCKET_USER_PATH

# Environment variables to skip when passing through to container
_ENTER_ENV_SKIP = frozenset({
    "_",              # Last command (set by shell)
    "SHLVL",          # Shell nesting level
    "OLDPWD",         # Previous directory
    "PWD",            # Current directory (will be wrong in container)
    "HOSTNAME",       # Host's hostname
    "HOST",           # Host's hostname (zsh)
    "LS_COLORS",      # Often huge and causes issues
    "LESS_TERMCAP_mb", "LESS_TERMCAP_md", "LESS_TERMCAP_me",  # Less colors
    "LESS_TERMCAP_se", "LESS_TERMCAP_so", "LESS_TERMCAP_ue", "LESS_TERMCAP_us",
})


def _base_container_config(nvidia_drivers: bool = True) -> dict[str, str]:
    """Base Incus config applied to every new Kapsule container.

    Args:
        nvidia_drivers: If True *and* the hook script is present on the host,
            register an LXC mount hook that injects NVIDIA userspace drivers
            into the container before pivot_root.

    Returns:
        Config dict with security and networking settings.
    """
    raw_lxc = "lxc.net.0.type=none\n"

    # Register NVIDIA driver injection hook when enabled.
    # We use our own hook rather than Incus's nvidia.runtime because
    # upstream rejects that option on privileged containers.  See the
    # header comment in data/nvidia-container-hook.sh for the full
    # rationale.  The hook silently exits 0 when nvidia-container-cli
    # or /dev/nvidia0 are absent, so this is safe on non-NVIDIA hosts.
    if nvidia_drivers and os.path.isfile(NVIDIA_HOOK_PATH):
        raw_lxc += f"lxc.hook.mount={NVIDIA_HOOK_PATH}\n"

    return {
        # In a future version, we might investigate what
        # we can do with unprivileged containers.
        "security.privileged": "true",
        "security.nesting": "true",
        # Use host networking (+ optional NVIDIA hook)
        "raw.lxc": raw_lxc,
    }


def _base_container_devices(host_rootfs: bool, gpu: bool = True) -> dict[str, dict[str, str]]:
    """Base Incus devices applied to every new Kapsule container.

    Args:
        host_rootfs: If True, mount the entire host filesystem at /.kapsule/host.
            If False, only targeted mounts are added later during user setup.
        gpu: If True, include GPU passthrough device.

    Returns:
        Devices dict with root disk, optionally GPU passthrough, and optionally host filesystem.
    """
    devices: dict[str, dict[str, str]] = {
        # Root disk - required for container storage
        "root": {
            "type": "disk",
            "path": "/",
            "pool": "default",
        },
    }

    if gpu:
        # GPU passthrough — in privileged containers this exposes all GPU
        # device nodes (/dev/nvidia*, /dev/dri/*, etc.) automatically.
        devices["gpu"] = {
            "type": "gpu",
        }

    if host_rootfs:
        # Mount the entire host filesystem at /.kapsule/host
        devices["hostfs"] = {
            "type": "disk",
            "source": "/",
            "path": "/.kapsule/host",
            "propagation": "rslave",
            "recursive": "true",
            "shift": "false",
        }

    return devices


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

    def set_bus(self, bus: MessageBus) -> None:
        """Set the message bus for operation object export.

        Must be called after initialization to enable D-Bus operation objects.
        """
        self._tracker.set_bus(bus)

    def list_operations(self) -> list[str]:
        """List D-Bus object paths of all running operations."""
        return self._tracker.list_paths()

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
        session_mode: bool = False,
        dbus_mux: bool = False,
        host_rootfs: bool = True,
        gpu: bool = True,
        nvidia_drivers: bool = True,
    ) -> None:
        """Create a new container.

        Args:
            progress: Operation reporter (auto-injected)
            name: Container name
            image: Image to use (e.g., "images:archlinux")
            session_mode: Enable session mode with container D-Bus
            dbus_mux: Enable D-Bus multiplexer (implies session_mode)
            host_rootfs: Mount entire host filesystem at /.kapsule/host.
                When False, only targeted mounts are added during user setup.
            gpu: Include GPU passthrough device.
            nvidia_drivers: Inject host NVIDIA userspace drivers into the
                container on each start.  Only effective when gpu is True
                and nvidia-container-cli is available on the host.
        """
        # dbus_mux implies session_mode
        if dbus_mux:
            session_mode = True

        # D-Bus mux requires the full host rootfs (the mux binary is
        # accessed via /.kapsule/host/usr/lib/kapsule/kapsule-dbus-mux)
        if dbus_mux and not host_rootfs:
            raise OperationError(
                "D-Bus multiplexer requires --host-rootfs "
                "(the mux binary is accessed via the host filesystem mount)"
            )

        # Check if container already exists
        if await self._incus.instance_exists(name):
            raise OperationError(f"Container '{name}' already exists")

        progress.info(f"Image: {image}")
        if not host_rootfs:
            progress.info("Minimal host mounts (no full rootfs)")
        if not gpu:
            progress.info("GPU passthrough: disabled")
        if gpu and not nvidia_drivers:
            progress.info("NVIDIA driver injection: disabled")

        # Parse image source
        instance_source = self._parse_image_source(image)
        if instance_source is None:
            raise OperationError(f"Invalid image format: {image}")

        # Build instance config — base settings applied directly
        instance_config_dict = _base_container_config(nvidia_drivers=gpu and nvidia_drivers)
        if session_mode:
            instance_config_dict[KAPSULE_SESSION_MODE_KEY] = "true"
        if dbus_mux:
            instance_config_dict[KAPSULE_DBUS_MUX_KEY] = "true"
        instance_config_dict[KAPSULE_HOST_ROOTFS_KEY] = str(host_rootfs).lower()
        instance_config_dict[KAPSULE_GPU_KEY] = str(gpu).lower()
        instance_config_dict[KAPSULE_NVIDIA_DRIVERS_KEY] = str(gpu and nvidia_drivers).lower()

        instance_config = InstancesPost(
            name=name,
            profiles=[],
            source=instance_source,
            start=True,
            architecture=None,
            config=instance_config_dict,
            description=None,
            devices=_base_container_devices(host_rootfs=host_rootfs, gpu=gpu),
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

        # Apply host-networking fixups (mask services that don't work with lxc.net.0.type=none)
        await self._apply_host_network_fixups(progress, name)

        # Restore file capabilities stripped during image extraction
        await self._fix_file_capabilities(progress, name)

        # Set up session mode if enabled
        if session_mode:
            await self._setup_session_mode(progress, name, dbus_mux)
        else:
            # Non-session containers lack a systemd user instance, so
            # rootless Podman's default cgroup_manager=systemd will fail.
            await self._configure_rootless_podman(progress, name)

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
        home_basename = os.path.basename(home_dir)
        container_home = f"/home/{home_basename}"

        # Mount home directory
        progress.info(f"Mounting home directory: {home_dir} -> {container_home}")
        device_name = f"kapsule-home-{username}"
        try:
            await self._incus.add_instance_device(
                container_name,
                device_name,
                {
                    "type": "disk",
                    "source": home_dir,
                    "path": container_home,
                },
            )
        except IncusError as e:
            raise OperationError(f"Failed to mount home directory: {e}")

        # Create group
        progress.info(f"Creating group '{username}' (gid={gid})")
        result = subprocess.run(
            ["incus", "exec", container_name, "--", "groupadd", "-o", "-g", str(gid), username],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            progress.warning(f"groupadd: {result.stderr.strip()}")

        # Create user
        progress.info(f"Creating user '{username}' (uid={uid})")
        result = subprocess.run(
            [
                "incus",
                "exec",
                container_name,
                "--",
                "useradd",
                "-o",  # Allow duplicate UID
                "-M",  # Don't create home directory
                "-u",
                str(uid),
                "-g",
                str(gid),
                "-d",
                container_home,
                "-s",
                "/bin/bash",
                username,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "already exists" not in result.stderr:
            progress.warning(f"useradd: {result.stderr.strip()}")

        # Configure passwordless sudo
        progress.info(f"Configuring passwordless sudo for '{username}'")
        # Ensure /etc/sudoers.d/ exists (Alpine and other minimal images may lack it)
        subprocess.run(
            ["incus", "exec", container_name, "--", "mkdir", "-p", "/etc/sudoers.d"],
            capture_output=True,
        )
        sudoers_content = f"{username} ALL=(ALL) NOPASSWD:ALL\n"
        sudoers_file = f"/etc/sudoers.d/{username}"
        try:
            await self._incus.push_file(
                container_name,
                sudoers_file,
                sudoers_content,
                uid=0,
                gid=0,
                mode="0440",
            )
        except IncusError as e:
            raise OperationError(f"Failed to configure sudo: {e}")

        # Check if session mode is enabled
        instance = await self._incus.get_instance(container_name)
        instance_config = instance.config or {}
        session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"

        if session_mode:
            progress.info(f"Enabling linger for '{username}' (session mode)")
            result = subprocess.run(
                ["incus", "exec", container_name, "--", "loginctl", "enable-linger", username],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                progress.warning(f"loginctl enable-linger: {result.stderr.strip()}")

        # Mark user as mapped
        user_mapped_key = f"user.kapsule.host-users.{uid}.mapped"
        try:
            await self._incus.patch_instance_config(container_name, {user_mapped_key: "true"})
        except IncusError as e:
            raise OperationError(f"Failed to update container config: {e}")

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
                await self._setup_user_sync(container_name, uid, gid, username, home_dir)
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
            if key in _ENTER_ENV_SKIP:
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

    async def _create_default_container(self, name: str, image: str) -> None:
        """Create the default container without progress reporting.

        Args:
            name: Container name
            image: Image to use
        """
        # Parse image source
        instance_source = self._parse_image_source(image)
        if instance_source is None:
            raise OperationError(f"Invalid image format: {image}")

        # Create instance — base settings applied directly
        instance_config = InstancesPost(
            name=name,
            profiles=None,
            source=instance_source,
            start=True,
            architecture=None,
            config={
                **_base_container_config(nvidia_drivers=True),
                KAPSULE_HOST_ROOTFS_KEY: "true",
                KAPSULE_GPU_KEY: "true",
                KAPSULE_NVIDIA_DRIVERS_KEY: "true",
            },
            description=None,
            devices=_base_container_devices(host_rootfs=True),
            ephemeral=None,
            instance_type=None,
            restore=None,
            stateful=None,
            type=None,
        )

        try:
            operation = await self._incus.create_instance(instance_config, wait=True)
            if operation.status != "Success":
                raise OperationError(f"Creation failed: {operation.err or operation.status}")
        except IncusError as e:
            raise OperationError(f"Failed to create container: {e}")

        # Restore file capabilities stripped during image extraction
        await self._fix_file_capabilities(None, name)

    async def _setup_user_sync(
        self,
        container_name: str,
        uid: int,
        gid: int,
        username: str,
        home_dir: str,
    ) -> None:
        """Set up a host user in a container without progress reporting.

        Args:
            container_name: Container name
            uid: User ID
            gid: Group ID
            username: Username
            home_dir: Path to home directory on host
        """
        home_basename = os.path.basename(home_dir)
        container_home = f"/home/{home_basename}"

        # Mount home directory
        device_name = f"kapsule-home-{username}"
        try:
            await self._incus.add_instance_device(
                container_name,
                device_name,
                {
                    "type": "disk",
                    "source": home_dir,
                    "path": container_home,
                },
            )
        except IncusError as e:
            raise OperationError(f"Failed to mount home directory: {e}")

        # When not using full host rootfs, add targeted mounts for
        # /run/user/<uid> and /tmp/.X11-unix so socket symlinks work.
        instance = await self._incus.get_instance(container_name)
        instance_config = instance.config or {}
        has_host_rootfs = instance_config.get(KAPSULE_HOST_ROOTFS_KEY) == "true"

        if not has_host_rootfs:
            # Mount /run/user/<uid> at /.kapsule/host/run/user/<uid>
            hostrun_device = f"kapsule-hostrun-{uid}"
            try:
                await self._incus.add_instance_device(
                    container_name,
                    hostrun_device,
                    {
                        "type": "disk",
                        "source": f"/run/user/{uid}",
                        "path": f"/.kapsule/host/run/user/{uid}",
                        "shift": "false",
                        "recursive": "true",
                        "propagation": "rslave",
                    },
                )
            except IncusError as e:
                raise OperationError(f"Failed to mount host runtime dir: {e}")

            # Mount /tmp/.X11-unix at /.kapsule/host/tmp/.X11-unix for X11
            try:
                await self._incus.add_instance_device(
                    container_name,
                    "kapsule-x11",
                    {
                        "type": "disk",
                        "source": "/tmp/.X11-unix",
                        "path": "/.kapsule/host/tmp/.X11-unix",
                        "shift": "false",
                        "recursive": "true",
                        "propagation": "rslave",
                    },
                )
            except IncusError as e:
                raise OperationError(f"Failed to mount host X11 dir: {e}")

        # Create group
        subprocess.run(
            ["incus", "exec", container_name, "--", "groupadd", "-o", "-g", str(gid), username],
            capture_output=True,
        )

        # Create user
        subprocess.run(
            [
                "incus",
                "exec",
                container_name,
                "--",
                "useradd",
                "-o",
                "-M",
                "-u",
                str(uid),
                "-g",
                str(gid),
                "-d",
                container_home,
                "-s",
                "/bin/bash",
                username,
            ],
            capture_output=True,
        )

        # Configure passwordless sudo
        # Ensure /etc/sudoers.d/ exists (Alpine and other minimal images may lack it)
        subprocess.run(
            ["incus", "exec", container_name, "--", "mkdir", "-p", "/etc/sudoers.d"],
            capture_output=True,
        )
        sudoers_content = f"{username} ALL=(ALL) NOPASSWD:ALL\n"
        sudoers_file = f"/etc/sudoers.d/{username}"
        try:
            await self._incus.push_file(
                container_name,
                sudoers_file,
                sudoers_content,
                uid=0,
                gid=0,
                mode="0440",
            )
        except IncusError as e:
            raise OperationError(f"Failed to configure sudo: {e}")

        # Check if session mode is enabled and enable linger
        session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"

        if session_mode:
            subprocess.run(
                ["incus", "exec", container_name, "--", "loginctl", "enable-linger", username],
                capture_output=True,
            )

        # Mark user as mapped
        user_mapped_key = f"user.kapsule.host-users.{uid}.mapped"
        try:
            await self._incus.patch_instance_config(container_name, {user_mapped_key: "true"})
        except IncusError as e:
            raise OperationError(f"Failed to update container config: {e}")

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

        In session mode, the dbus socket is not mounted (the container has
        its own D-Bus session).

        Args:
            container_name: Container name
            uid: User ID
            gid: Group ID
            env: Environment variables (for WAYLAND_DISPLAY etc)
        """
        instance = await self._incus.get_instance(container_name)
        instance_config = instance.config or {}

        session_mode = instance_config.get(KAPSULE_SESSION_MODE_KEY) == "true"

        runtime_dir = f"/run/user/{uid}"
        host_runtime_dir = f"/.kapsule/host/run/user/{uid}"

        # Ensure container runtime dir exists
        try:
            await self._incus.mkdir(container_name, "/run/user", uid=0, gid=0, mode="0755")
        except IncusError:
            pass
        try:
            await self._incus.mkdir(container_name, runtime_dir, uid=uid, gid=gid, mode="0700")
        except IncusError:
            pass

        # Bind-mount individual sockets from host runtime dir.
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

            self._bind_mount_in_container(container_name, source, target, uid, gid)

        # X11: bind-mount the individual socket from the host's /tmp/.X11-unix/
        # into the container. The host's /tmp is accessible via hostfs.
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
            self._bind_mount_in_container(
                container_name, host_x11, f"{container_x11_dir}/{x11_socket}", 0, 0,
            )

        # PulseAudio: create a real pulse/ directory and bind-mount native inside.
        # PulseAudio refuses to use pulse/ if it's itself a symlink (security check).
        pulse_dir = f"{runtime_dir}/pulse"
        host_pulse_native = f"{host_runtime_dir}/pulse/native"
        try:
            await self._incus.mkdir(container_name, pulse_dir, uid=uid, gid=gid, mode="0700")
        except IncusError:
            pass
        self._bind_mount_in_container(
            container_name, host_pulse_native, f"{pulse_dir}/native", uid, gid,
        )

        # XAUTHORITY: the env value is a full path (e.g. /run/user/1000/xauth_LAPpeP).
        # Bind-mount just the basename inside the container's runtime dir to the
        # corresponding host file via hostfs.
        xauth_path = env.get("XAUTHORITY", "")
        if xauth_path:
            xauth_basename = os.path.basename(xauth_path)
            host_xauth = f"{host_runtime_dir}/{xauth_basename}"
            target_xauth = f"{runtime_dir}/{xauth_basename}"
            self._bind_mount_in_container(
                container_name, host_xauth, target_xauth, uid, gid,
            )

    def _bind_mount_in_container(
        self,
        container_name: str,
        source: str,
        target: str,
        uid: int,
        gid: int,
    ) -> None:
        """Bind-mount a host file/socket into a container.

        Creates the mount point if it doesn't exist, removes any stale
        symlink at the target, and performs a bind mount.  If the target
        is already a mount point or the source does not exist, this is a
        no-op.

        Args:
            container_name: Container name.
            source: Source path inside the container (typically under
                /.kapsule/host/).
            target: Target path inside the container.
            uid: Mount-point owner UID.
            gid: Mount-point owner GID.
        """
        # Single shell command that handles idempotency:
        #  1. Skip if target is already a mount point
        #  2. Remove any stale symlink at target
        #  3. Skip if the source doesn't exist (host socket absent)
        #  4. Create mount point file and bind-mount
        script = (
            f'mountpoint -q "$1" 2>/dev/null && exit 0; '
            f'rm -f "$1"; '
            f'[ -e "$2" ] || exit 0; '
            f'touch "$1" && chown {uid}:{gid} "$1" && '
            f'mount --bind "$2" "$1"'
        )
        subprocess.run(
            [
                "incus", "exec", container_name, "--",
                "sh", "-c", script, "sh", target, source,
            ],
            capture_output=True,
        )

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

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

    async def _fix_file_capabilities(
        self,
        progress: OperationReporter | None,
        name: str,
    ) -> None:
        """Restore file capabilities stripped during image extraction.

        Container images from linuxcontainers.org lose ``security.capability``
        extended attributes during image build or extraction.  Binaries like
        ``newuidmap`` / ``newgidmap`` (from the ``shadow`` package) need file
        capabilities (``cap_setuid+ep`` / ``cap_setgid+ep``) for rootless
        Podman / Docker to set up user namespaces inside the container.

        Upstream issue: https://github.com/lxc/lxc-ci/issues/955

        This method restores the expected capabilities if the binaries exist
        and ``setcap`` is available.

        Args:
            progress: Operation reporter (may be None for silent fixups)
            name: Container name
        """
        caps: list[tuple[str, str]] = [
            ("/usr/bin/newuidmap", "cap_setuid+ep"),
            ("/usr/bin/newgidmap", "cap_setgid+ep"),
        ]
        for binary, cap in caps:
            result = subprocess.run(
                ["incus", "exec", name, "--", "setcap", cap, binary],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # Binary or setcap may not exist on every image — not fatal
                if progress:
                    progress.warning(
                        f"Could not set {cap} on {binary}: {result.stderr.strip()}"
                    )
            else:
                if progress:
                    progress.dim(f"Set {cap} on {binary}")

    async def _apply_host_network_fixups(
        self,
        progress: OperationReporter,
        name: str,
    ) -> None:
        """Apply fixups for containers using host networking (lxc.net.0.type=none).

        Kapsule containers share the host's network namespace, so there are no
        network interfaces for systemd-networkd to manage inside the container.
        This causes systemd-networkd-wait-online.service to wait for a timeout
        (~30s) before services like Docker can start.

        We mask that service since the host network is already online.

        Args:
            progress: Operation reporter
            name: Container name
        """
        # Mask systemd-networkd-wait-online.service by symlinking to /dev/null
        # This is what `systemctl mask` does
        progress.info("Masking systemd-networkd-wait-online.service (host networking)")
        try:
            await self._incus.create_symlink(
                name,
                "/etc/systemd/system/systemd-networkd-wait-online.service",
                "/dev/null",
                uid=0,
                gid=0,
            )
        except IncusError as e:
            # Not fatal - some images may not have systemd
            progress.warning(f"Could not mask systemd-networkd-wait-online: {e}")

    async def _configure_rootless_podman(
        self,
        progress: OperationReporter,
        name: str,
    ) -> None:
        """Configure rootless Podman for non-session containers.

        Kapsule's default (non-session) containers forward the host's D-Bus
        session bus rather than running their own systemd user instance.
        Podman defaults to ``cgroup_manager = "systemd"`` which asks systemd
        to create a transient scope via sd-bus, but the host's systemd cannot
        manage the container's PIDs so this fails with "No such process".

        Dropping a config file into ``/etc/containers/containers.conf.d/``
        switches rootless Podman to the ``cgroupfs`` cgroup manager which
        writes cgroup entries directly instead of going through sd-bus.

        Args:
            progress: Operation reporter
            name: Container name
        """
        parent_dir = "/etc/containers"
        dropin_dir = f"{parent_dir}/containers.conf.d"
        dropin_file = f"{dropin_dir}/50-kapsule-cgroupfs.conf"
        dropin_content = (
            "# Installed by Kapsule – non-session containers lack a systemd\n"
            "# user instance, so the default systemd cgroup manager fails.\n"
            "[engine]\n"
            'cgroup_manager = "cgroupfs"\n'
        )

        # Create the full directory hierarchy – most images don't ship
        # with Podman so /etc/containers/ won't exist yet.
        for d in (parent_dir, dropin_dir):
            try:
                await self._incus.mkdir(name, d, uid=0, gid=0, mode="0755")
            except IncusError:
                pass  # Directory might already exist

        try:
            await self._incus.push_file(
                name, dropin_file, dropin_content,
                uid=0, gid=0, mode="0644",
            )
        except IncusError as e:
            # Not fatal – best-effort config for when Podman is installed later
            progress.warning(f"Could not configure rootless Podman: {e}")
            return

        progress.dim("Configured rootless Podman (cgroup_manager=cgroupfs)")

    async def _setup_session_mode(
        self,
        progress: OperationReporter,
        name: str,
        dbus_mux: bool,
    ) -> None:
        """Set up session mode for a container.

        Without D-Bus mux, the container's own systemd dbus.socket creates
        /run/user/$uid/bus natively — no extra setup is needed (loginctl
        enable-linger is handled by _setup_user_sync).

        With D-Bus mux, we redirect the container's dbus.socket to a hostfs
        path so the mux process can reach it from the host, then install the
        kapsule-dbus-mux.service that listens at the normal /run/user/$uid/bus.

        Args:
            progress: Operation reporter
            name: Container name
            dbus_mux: Whether to set up D-Bus multiplexer
        """
        if not dbus_mux:
            progress.info("Session mode: container will use its own D-Bus session bus")
            return

        # Use uid 1000 as placeholder - the drop-in uses %t so it works for any user
        uid = 1000
        host_socket_path = f"/run/user/{uid}/kapsule/{name}/dbus.socket"

        progress.info(f"Configuring container D-Bus socket at: {host_socket_path}")

        # Create the directory on host with correct ownership
        kapsule_base_dir = f"/run/user/{uid}/kapsule"
        host_socket_dir = os.path.dirname(host_socket_path)
        os.makedirs(host_socket_dir, exist_ok=True)
        # Set ownership of both the kapsule base dir and container-specific dir
        os.chown(kapsule_base_dir, uid, uid)
        os.chown(host_socket_dir, uid, uid)

        # Create systemd user drop-in directory
        dropin_dir = "/etc/systemd/user/dbus.socket.d"
        try:
            await self._incus.mkdir(name, dropin_dir, uid=0, gid=0, mode="0755")
        except IncusError:
            pass  # Directory might already exist

        # Create the drop-in file
        systemd_socket_path = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=name)
        dropin_content = f"""[Socket]
# Kapsule: redirect D-Bus session socket to shared path
# This makes the container's D-Bus accessible from the host
# %t expands to XDG_RUNTIME_DIR (/run/user/UID)
ListenStream=
ListenStream={systemd_socket_path}
"""
        dropin_file = f"{dropin_dir}/kapsule.conf"
        try:
            await self._incus.push_file(name, dropin_file, dropin_content, uid=0, gid=0, mode="0644")
        except IncusError as e:
            raise OperationError(f"Failed to configure D-Bus socket: {e}")

        # Set up D-Bus multiplexer if requested
        if dbus_mux:
            await self._setup_dbus_mux(progress, name)

        # Reload systemd
        progress.info("Reloading systemd user configuration...")
        subprocess.run(
            ["incus", "exec", name, "--", "systemctl", "--user", "--global", "daemon-reload"],
            capture_output=True,
        )

    async def _setup_dbus_mux(self, progress: OperationReporter, name: str) -> None:
        """Set up D-Bus multiplexer service in a container.

        Args:
            progress: Operation reporter
            name: Container name
        """
        progress.info("Installing kapsule-dbus-mux.service for D-Bus multiplexing")

        service_dir = "/etc/systemd/user"
        try:
            await self._incus.mkdir(name, service_dir, uid=0, gid=0, mode="0755")
        except IncusError:
            pass  # Directory might already exist

        container_dbus_socket = KAPSULE_DBUS_SOCKET_SYSTEMD.format(container=name)
        host_dbus_socket = "unix:path=/.kapsule/host%t/bus"
        mux_listen_socket = "%t/bus"

        service_content = f"""[Unit]
Description=Kapsule D-Bus Multiplexer
Documentation=man:kapsule(1)
After=dbus.service
Requires=dbus.service

[Service]
Type=simple
Environment=RUST_LOG=trace
ExecStart={KAPSULE_DBUS_MUX_BIN} \\
    --log-level debug \\
    --listen {mux_listen_socket} \\
    --container-bus unix:path={container_dbus_socket} \\
    --host-bus {host_dbus_socket}
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
"""

        service_file = f"{service_dir}/kapsule-dbus-mux.service"
        try:
            await self._incus.push_file(name, service_file, service_content, uid=0, gid=0, mode="0644")
        except IncusError as e:
            raise OperationError(f"Failed to install dbus-mux service: {e}")

        progress.info("Enabling kapsule-dbus-mux.service globally")
        subprocess.run(
            ["incus", "exec", name, "--", "systemctl", "--user", "--global", "enable", "kapsule-dbus-mux.service"],
            capture_output=True,
        )
