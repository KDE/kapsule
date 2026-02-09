# CI Pipeline Context

## Branch
`work/fernando/ci` on `invent.kde.org/kde-linux/kapsule`

## What we're doing
Setting up a GitLab CI pipeline for Kapsule following KDE best practices. The pipeline
is being iterated on — push, check CI results, fix, repeat.

## Current state
The pipeline was last pushed at commit `1c294f1` and is waiting for CI results.
Check the latest pipeline at: https://invent.kde.org/kde-linux/kapsule/-/pipelines?ref=work/fernando/ci

## Pipeline structure

### `.gitlab-ci.yml` — 6 jobs across 3 stages

| Stage | Job | Runner | Purpose |
|-------|-----|--------|---------|
| validate | `reuse` | Docker (Linux) | REUSE/SPDX license compliance (KDE template) |
| build | `suse_tumbleweed_qt610` | Docker (Linux) | Standard KDE C++ build on openSUSE (KDE template, tests disabled) |
| build | `python_lint` | Docker (`python:3.14-slim`) | `ruff check` + `ruff format --check` on Python daemon |
| build | `python_typecheck` | Docker (`python:3.14-slim`) | `mypy` on `src/daemon/` |
| build | `build_sysext` | **VM** (`kde-linux-builder`) | Builds C++ + Python, packages as sysext tarball artifact |
| test | `integration_test` | **VM** (`kde-linux-builder`) | Deploys sysext, starts incus + daemon, runs integration tests |

### `.kde-ci.yml` — KDE CI dependency metadata
- Dependencies: ECM, KCoreAddons, KI18n, KConfig (`@latest-kf6`), QCoro (`@latest`)
- Linux only
- `run-tests: False` (no CTest unit tests, only VM-based integration tests)

## VM runner details
- Tagged `VM` + `amd64`, uses custom executor (`vm-runner`)
- Image: `storage.kde.org/vm-images/kde-linux-builder` (Arch Linux cloud VM)
- The CI user is `user` with passwordless sudo
- Each job gets a fresh VM — no state is preserved between jobs

## Issues fixed so far (in chronological order)

1. **iptables conflict** (commit `91696bf`): `incus` depends on `iptables-nft` which
   conflicts with `iptables` pre-installed in the VM image. Fixed with `--ask 4` flag
   to pacman so it auto-resolves conflicts.

2. **D-Bus policy not loaded** (commit `c9f3dd0`): The sysext installs
   `org.kde.kapsule.conf` D-Bus policy into `/usr/share/dbus-1/system.d/`, but
   dbus-daemon doesn't see new policy files until reloaded. Fixed by adding
   `sudo systemctl reload dbus.service` after `systemd-sysext refresh`.

3. **Missing shared libraries** (commit `1c294f1`): The kapsule CLI binary links
   against Qt6, KF6, and QCoro shared libs. The build_sysext job installs them for
   build but the integration_test VM is a separate fresh VM that didn't have them.
   Fixed by also installing `qt6-base ki18n kcoreaddons kconfig qcoro-qt6` in the
   test job.

4. **Incus socket permissions** (commit `1c294f1`): `usermod -aG incus $(whoami)`
   doesn't take effect until a new login session. Fixed by doing
   `chmod 0666 /var/lib/incus/unix.socket` instead.

5. **Root SSH for tests** (commit `1c294f1`): `test-profile-sync.sh` SSHes as root
   to restart the daemon. Fixed by copying the CI user's SSH public key into
   `/root/.ssh/authorized_keys`.

6. **after_script silent failure** (commit TBD): The `after_script` in
   `integration_test` produced zero diagnostic output and failed with exit status 1.
   The `|| true` guards on individual commands didn't help because the after_script
   block likely failed at the `sudo` call (custom VM executor may not preserve sudo
   context in after_script). Fixed by adding `set +e` as the first after_script line,
   using `sudo -n` (non-interactive) to avoid hanging on password prompts, and
   wrapping each command with `2>&1` + fallback echo.

7. **No pre-test diagnostics** (commit TBD): The integration_test job had no
   verification steps between environment setup and running tests, making it
   impossible to know which component was broken when tests failed. Fixed by adding
   verification sections after sysext deploy (check files exist, CLI works), after
   incus start (version, socket), after daemon start (systemctl status, D-Bus tree),
   and after SSH setup (test connection).

## Likely next issues
- The test-audio-sockets test creates an archlinux container. The test hit
  "Waiting for container to initialize" and then something after that failed —
  likely `kapsule enter` or the socket checks on a headless CI VM without audio.
  Now that diagnostic logs are collected, the next run will show exactly where.
- The `python_lint` job is failing with 157 ruff errors (import sorting, line length,
  etc.) — these need to be fixed separately or the ruff config adjusted.
- The Arch package names for KF6 libs might be wrong (e.g. `ki18n` vs `ki18n6` etc.)
  — check the build log if pacman can't find them.
- The `build_sysext` job might also need the `--ask 4` flag if it hits the same
  iptables conflict (it doesn't install incus though, so probably fine).
- Integration tests may hit real test failures once environment setup is working.
- The `python_lint` and `python_typecheck` jobs install from `.[dev]` which requires
  setuptools to parse `pyproject.toml` — if these fail, check that the package
  metadata is correct.
