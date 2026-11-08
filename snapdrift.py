#!/usr/bin/env python3
"""SnapDrift — snapshot a directory tree by content hash, then prove what drifted.

Single file, Python standard library only, no network access.
See README.md for usage.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import sys

VERSION = "1.0.0"
FORMAT = 1
DEFAULT_SKIP = {".git", "__pycache__", "node_modules", ".venv", "venv"}


class SnapError(Exception):
    """Usage/IO error -> exit 2."""


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 256)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _excluded(rel, name, excludes):
    for pat in excludes:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def build_entries(root, excludes=(), include_all=False, exclude_files=()):
    """Return the sorted entry list for the tree at root.

    exclude_files: absolute paths to skip (the snapshot file itself when
    it lives inside the tree).
    """
    root_abs = os.path.abspath(root)
    skip_abs = {os.path.abspath(p) for p in exclude_files}
    if os.path.islink(root_abs) or os.path.isfile(root_abs):
        name = os.path.basename(root_abs.rstrip(os.sep)) or root_abs
        if os.path.islink(root_abs):
            return [{"path": name, "target": os.readlink(root_abs), "type": "link"}]
        return [{
            "path": name,
            "sha256": _hash_file(root_abs),
            "size": os.path.getsize(root_abs),
            "type": "file",
        }]
    if not os.path.isdir(root_abs):
        raise SnapError(f"path does not exist or is not readable: {root}")

    entries = []
    for dirpath, dirnames, filenames in os.walk(root_abs, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root_abs)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep_dirs = []
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if os.path.islink(full):
                entries.append({"path": rel, "target": os.readlink(full), "type": "link"})
                continue
            if not include_all and name in DEFAULT_SKIP:
                continue
            if _excluded(rel, name, excludes):
                continue
            keep_dirs.append(name)
            entries.append({"path": rel, "type": "dir"})
        dirnames[:] = keep_dirs
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.abspath(full) in skip_abs:
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _excluded(rel, name, excludes):
                continue
            if os.path.islink(full):
                entries.append({"path": rel, "target": os.readlink(full), "type": "link"})
            elif os.path.isfile(full):
                try:
                    digest = _hash_file(full)
                except OSError as exc:
                    raise SnapError(f"cannot read file: {rel}: {exc}") from exc
                entries.append({
                    "path": rel,
                    "sha256": digest,
                    "size": os.path.getsize(full),
                    "type": "file",
                })
            else:
                entries.append({"path": rel, "type": "other"})
    entries.sort(key=lambda e: e["path"])
    return entries


def make_snapshot(root, excludes=(), include_all=False, exclude_files=()):
    return {
        "entries": build_entries(root, excludes, include_all, exclude_files),
        "format": FORMAT,
        "tool": "snapdrift",
        "version": VERSION,
    }


def snapshot_bytes(snapshot):
    return (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_snapshot(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise SnapError(f"snapshot not found: {path}")
    except (OSError, UnicodeDecodeError) as exc:
        raise SnapError(f"cannot read snapshot {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise SnapError(f"snapshot is not valid JSON: {path}: {exc}")
    if not isinstance(data, dict) or data.get("format") != FORMAT \
            or not isinstance(data.get("entries"), list):
        raise SnapError(f"snapshot has an unsupported format: {path}")
    for entry in data["entries"]:
        if not isinstance(entry, dict) or "path" not in entry or "type" not in entry:
            raise SnapError(f"snapshot contains a malformed entry: {path}")
    return data


def diff_entries(old_entries, new_entries):
    old = {e["path"]: e for e in old_entries}
    new = {e["path"]: e for e in new_entries}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(
        p for p in set(old) & set(new) if old[p] != new[p]
    )
    unchanged = len(set(old) & set(new)) - len(changed)

    # Move detection: pair removed/added files with equal hashes,
    # deterministically in sorted-path order.
    removed_by_hash = {}
    for p in removed:
        e = old[p]
        if e.get("type") == "file" and e.get("sha256"):
            removed_by_hash.setdefault(e["sha256"], []).append(p)
    added_by_hash = {}
    for p in added:
        e = new[p]
        if e.get("type") == "file" and e.get("sha256"):
            added_by_hash.setdefault(e["sha256"], []).append(p)
    moved = []
    for digest in sorted(set(removed_by_hash) & set(added_by_hash)):
        for src, dst in zip(sorted(removed_by_hash[digest]),
                            sorted(added_by_hash[digest])):
            moved.append({"from": src, "sha256": digest, "to": dst})
    moved_from = {m["from"] for m in moved}
    moved_to = {m["to"] for m in moved}
    added = [p for p in added if p not in moved_to]
    removed = [p for p in removed if p not in moved_from]
    moved.sort(key=lambda m: m["from"])
    return {
        "added": added,
        "changed": changed,
        "moved": moved,
        "removed": removed,
        "unchanged": unchanged,
    }


def has_drift(report):
    return bool(report["added"] or report["removed"]
                or report["changed"] or report["moved"])


def print_report(report, as_json, total_entries):
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    for p in report["added"]:
        print(f"added: {p}")
    for p in report["removed"]:
        print(f"removed: {p}")
    for p in report["changed"]:
        print(f"changed: {p}")
    for m in report["moved"]:
        print(f"moved: {m['from']} -> {m['to']}")
    if has_drift(report):
        print(
            f"snapdrift: {len(report['added'])} added, "
            f"{len(report['removed'])} removed, "
            f"{len(report['changed'])} changed, "
            f"{len(report['moved'])} moved, "
            f"{report['unchanged']} unchanged"
        )
    else:
        print(f"no drift: {total_entries} entries unchanged")


def cmd_snap(args):
    exclude_files = [args.output] if args.output else []
    snapshot = make_snapshot(args.path, args.exclude, args.all, exclude_files)
    data = snapshot_bytes(snapshot)
    if args.output:
        try:
            with open(args.output, "wb") as fh:
                fh.write(data)
        except OSError as exc:
            raise SnapError(f"cannot write snapshot: {exc}")
        print(f"snapdrift: wrote {len(snapshot['entries'])} entries to {args.output}",
              file=sys.stderr)
    else:
        sys.stdout.write(data.decode("utf-8"))
    return 0


def cmd_check(args):
    snapshot = load_snapshot(args.snapshot)
    exclude_files = [args.snapshot]
    live = make_snapshot(args.path, args.exclude, args.all, exclude_files)
    report = diff_entries(snapshot["entries"], live["entries"])
    print_report(report, args.json, len(snapshot["entries"]))
    return 1 if has_drift(report) else 0


def cmd_diff(args):
    old = load_snapshot(args.old)
    new = load_snapshot(args.new)
    report = diff_entries(old["entries"], new["entries"])
    print_report(report, args.json, len(old["entries"]))
    return 1 if has_drift(report) else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="snapdrift",
        description="Snapshot a directory tree by content hash and report drift: "
                    "added, removed, changed, and moved files. Offline, stdlib only.",
    )
    parser.add_argument("--version", action="version", version=f"snapdrift {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snap", help="write a snapshot of PATH")
    p_snap.add_argument("path")
    p_snap.add_argument("-o", "--output", help="snapshot file (default: stdout)")
    p_snap.add_argument("--exclude", action="append", default=[],
                        help="glob to exclude (repeatable)")
    p_snap.add_argument("--all", action="store_true",
                        help="include default-skipped directories (.git, node_modules, ...)")
    p_snap.set_defaults(func=cmd_snap)

    p_check = sub.add_parser("check", help="compare a live tree against a snapshot")
    p_check.add_argument("path")
    p_check.add_argument("snapshot")
    p_check.add_argument("--exclude", action="append", default=[],
                         help="glob to exclude (repeatable)")
    p_check.add_argument("--all", action="store_true",
                         help="include default-skipped directories")
    p_check.add_argument("--json", action="store_true", help="JSON report")
    p_check.set_defaults(func=cmd_check)

    p_diff = sub.add_parser("diff", help="compare two snapshots")
    p_diff.add_argument("old")
    p_diff.add_argument("new")
    p_diff.add_argument("--json", action="store_true", help="JSON report")
    p_diff.set_defaults(func=cmd_diff)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SnapError as exc:
        print(f"snapdrift: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
