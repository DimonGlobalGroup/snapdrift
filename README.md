# SnapDrift

**Snapshot a directory tree by content hash — then prove exactly what drifted.**

SnapDrift records the state of a tree in a small plain-JSON snapshot.
Later, on the same machine or a different one, it compares the live tree
(or a second snapshot) against that record and reports what was **added**,
**removed**, **changed**, or **moved**. One Python file, no dependencies,
no network.

```
$ snapdrift snap ~/site -o site.snap.json
$ # ... deploy, copy, restore, wait a week ...
$ snapdrift check ~/site site.snap.json
changed: assets/logo.png
moved: docs/draft.md -> docs/guide.md
removed: tmp/cache.bin
snapdrift: 0 added, 1 removed, 1 changed, 1 moved, 412 unchanged
```

## Why it's different

- **Content is the verdict, mtime is not.** Tools that compare
  timestamps or file metadata report drift on every `touch`, checkout,
  and copy. SnapDrift compares SHA-256 content hashes, entry types, and
  symlink targets — a file whose bytes did not change has not drifted,
  no matter what its clock says.
- **Moves are moves, not losses.** When a vanished file's hash matches
  a new file's hash, SnapDrift reports `moved: old -> new` instead of a
  delete plus an unrelated create, so a reorganization reads as a
  reorganization.
- **Portable, deterministic snapshots.** A snapshot contains relative
  paths only — no timestamps, no absolute paths, no host information.
  The same tree produces byte-identical snapshots on any machine, so a
  snapshot taken on a laptop verifies a tree on a server, and snapshots
  can be committed to version control and diffed like any other text.
- **The whole tree counts.** Empty directories and symlink targets are
  first-class entries — both are invisible to Git and to most hash
  tools. Symlinks are recorded, never followed, so link loops cannot
  hang a scan. A snapshot file stored inside the tree it describes is
  excluded automatically.

## Install

Download `snapdrift.py` and run it with Python 3.10 or newer.
That's the whole installation.

## Usage

```
snapdrift snap PATH [-o SNAPSHOT] [--exclude GLOB]... [--all]
snapdrift check PATH SNAPSHOT [--json] [--exclude GLOB]... [--all]
snapdrift diff OLD.json NEW.json [--json]
snapdrift --version
```

- `snap` writes a snapshot to a file (`-o`) or to stdout.
- `check` compares a live tree against a snapshot.
- `diff` compares two snapshots without touching the trees they describe.
- Default-skipped directories: `.git`, `__pycache__`, `node_modules`,
  `.venv`, `venv`. Pass `--all` to include them, and `--exclude GLOB`
  (repeatable, matched against the relative path and the file name) to
  skip anything else.
- `--json` prints the report as `{"added": [...], "removed": [...],
  "changed": [...], "moved": [{"from": ..., "to": ...}],
  "unchanged": N}` with deterministic ordering in both formats.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Snapshot written, or no drift found |
| 1 | Drift found (`check` / `diff`) |
| 2 | Usage or I/O error (missing path, malformed snapshot, unreadable file) |

That contract makes `check` a deployment and restore gate in any script:

```
snapdrift check /srv/app baseline.snap.json || echo "tree drifted — investigate"
```

## Limitations, stated honestly

- Hashing reads every byte: snapshotting a huge tree costs the I/O of
  reading it once. SnapDrift keeps no cache or daemon.
- A file that is moved *and* modified has a new hash, so it reports as
  removed + added, not moved. Move detection is exact-content only.
- When several identical files could pair up as moves, pairing is by
  sorted path order — deterministic, but it cannot know your intent.
- Snapshots record content, type, size, and symlink targets — not
  owners, permissions, or timestamps. A permissions-only change is not
  drift as SnapDrift defines it.
- Special files (FIFOs, sockets, devices) are recorded as type `other`
  with no hash.

## License

MIT — see `LICENSE`. A Dimon Global Group release.
