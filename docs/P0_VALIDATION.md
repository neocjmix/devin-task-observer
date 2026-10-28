# P0: Desktop probe and validation

Status: **not passed**. This record separates observation from product documentation and hypotheses. No production adapter, checkpoint engine, registry, dashboard, or semantic integration is claimed.

## Environment and setup (2026-10-28)

- Personal macOS 26.6.2, Apple Silicon; a fresh checkout of this planning repository. No existing `~/Library/Application Support/Devin`, `~/Library/Application Support/Windsurf`, `~/.devin`, or `~/.codeium/windsurf` was present before installation. Existence checks only; no other personal session data was examined.
- System Git and Homebrew cannot run because the Xcode license has not been accepted. A preinstalled alternate Git successfully cloned the private repository using the existing GitHub login. The license was not accepted on the user's behalf.
- Installed Devin Desktop 3.10.67 in `~/Applications` from the vendor's distribution URL listed in the [Homebrew cask](https://formulae.brew.sh/cask/devin-desktop); the DMG SHA-256 matched the cask, and `codesign --verify --deep --strict` succeeded. Launched the GUI and reached the signed-out onboarding and browser login. No IDE settings were imported and no terminal command was installed. After launch, `~/.devin`, `~/.codeium/windsurf`, and `~/Library/Application Support/Devin` existed; their existence alone does not identify session storage.
- The [official Desktop documentation](https://docs.devin.ai/desktop/getting-started) says login is required. The [Desktop Local Agent documentation](https://docs.devin.ai/desktop/devin-local) says the Local agent shares a harness with the CLI and exposes customizations including hooks; this is documentation, not proof of any hook executing in Desktop or its actual payload. No assumption is made about storage layout or behavior based on CLI docs.

## Probe tool

`probe/snapshot.py` is a preliminary filesystem comparison aid, not a session collector. It hashes regular files below an explicitly chosen test root, skips symlinks, avoids saving bodies or filenames, and keeps its snapshot outside the observed root. Output is aggregate counts only. Detected concurrent file changes or read errors make the run fail and leave the previous snapshot intact; metadata checks cannot detect every concurrent change. Snapshot files contain a root path, salted path hashes, content hashes and sizes; keep them private and outside the repository. Hashes and sizes are still potentially sensitive, especially for predictable content. It does not provide a consistent SQLite transaction snapshot, observe transient writes, infer session IDs, or establish event-level completeness.

Synthetic tests: `python3 -m unittest discover -s tests -v` passed 3 tests for identical snapshots, same-length changes, additions, deletion, symlink exclusion, private snapshot permissions and preservation of the last snapshot after an incomplete scan. These are not live Devin tests.

A separate command-line smoke test on a temporary synthetic file passed initial discovery, same-size content modification, unchanged replay and verification that the probe did not alter the source file.

## Live acceptance matrix

| Question | Evidence / result |
|---|---|
| Install and launch | Passed: version 3.10.67 launched to login. Personal account authentication pending. |
| Local session metadata and transcript location / format | Not observed |
| Stability of session and message IDs | Not observed |
| Updates while a session is active | Not observed |
| JSON rewrite, SQLite writes, restart, compaction | Not observed |
| Desktop hook execution and payload | Not observed |
| Discovery of multiple simultaneous sessions | Not observed |
| Read-only incremental collection without affecting Devin | Not established; P0 gate remains closed |

## Next experiment

Install and launch the official Desktop build, authenticate only with a personal account, and create isolated synthetic test sessions. Establish a baseline, observe changes during multiple sessions and restarts, and inspect only test-generated files locally. Record sanitized schemas and identifier stability without committing any transcript, credentials, or live snapshot. Test Desktop hooks independently. Proceed to P1 only after reliable read-only discovery and change detection are demonstrated.
