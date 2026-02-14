# CI Investigation Context (Focused)

## Scope
This file tracks only the **remaining investigation** into CI integration failures around `kapsule enter`.

## Active branch
- `work/fernando/audio-smoke-3da68dd`

## Investigation goal
- Determine the root cause of the `kapsule enter` failure/hang in CI and collect minimal evidence to fix it safely.

## Confirmed findings
1. Failure reproduces in **inline `.gitlab-ci.yml` smoke steps** (not via `tests/integration/*.sh` harness).
2. In integration CI, container creation and state checks succeed.
3. `incus exec <container> -- true` succeeds (`exit code: 0`).
4. `kapsule enter <container> -- true` then fails/hangs in the same job.
5. Therefore this currently looks **Kapsule-specific**, not an Incus/container readiness issue.
6. `kapsule enter` works correctly and all integration tests pass on the local development VM — the failure is **CI-environment-specific**.

## Relevant CI behavior right now
- `integration_test` now runs inline smoke diagnostics in `.gitlab-ci.yml`.
- Python jobs are marked `allow_failure: true` so they don’t block integration debugging.
- `after_script` is forced to collect diagnostics with `set +e` and `exit 0`.

## Most relevant commits on this branch
- `0e04864`: inline smoke expanded with audio-style checks
- `2dbe720`: allow Python checks to fail during integration debugging
- `427fe5d`: add timeout/diagnostics around smoke steps
- `8931908`: print `kapsule enter` smoke exit code
- `016057b`: robust `after_script` diagnostics collection
- `72f6c4e`: compare `incus exec` vs `kapsule enter` explicitly

## Fast triage commands
Project ID: `24978`

- Latest pipelines for this branch:
  - `curl -fsSL 'https://invent.kde.org/api/v4/projects/24978/pipelines?ref=work%2Ffernando%2Faudio-smoke-3da68dd&per_page=5' | jq`

- Jobs for a pipeline:
  - `curl -fsSL 'https://invent.kde.org/api/v4/projects/24978/pipelines/<PIPELINE_ID>/jobs?per_page=100' | jq -r 'sort_by(.id)[] | "\(.id)\t\(.stage)\t\(.name)\t\(.status)"'`

- Integration job raw log:
  - `curl -fsSL 'https://invent.kde.org/kde-linux/kapsule/-/jobs/<JOB_ID>/raw' | tail -n 300`

- Extract only decisive markers:
  - `curl -fsSL 'https://invent.kde.org/kde-linux/kapsule/-/jobs/<JOB_ID>/raw' | grep -nE 'verify incus exec path|incus exec smoke exit code|verify kapsule enter path|kapsule enter smoke exit code|kapsule-specific issue|ERROR:'`

## Important shell note
- In `zsh`, avoid assigning to `status` (readonly special var). Use `job_status` instead.

## Next investigation steps
1. Add targeted daemon-side logs around Enter D-Bus call handling (`kapsule-daemon` path).
2. In CI, capture daemon journal immediately before/after `kapsule enter` attempt.
3. If needed, add temporary debug output in daemon enter flow (request args, subprocess spawn, timeout path).
4. Keep integration smoke minimal until root cause is isolated.
