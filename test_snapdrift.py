#!/usr/bin/env python3
"""Test suite for SnapDrift (stdlib unittest only)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snapdrift  # noqa: E402

TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapdrift.py")


def write(root, rel, data):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)
    return path


def run_cli(*argv):
    proc = subprocess.run(
        [sys.executable, TOOL, *argv],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


class SnapDriftTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "tree")
        os.makedirs(self.root)
        self.snap = os.path.join(self._tmp.name, "snap.json")

    def tearDown(self):
        self._tmp.cleanup()

    def snap_now(self):
        code, out, err = run_cli("snap", self.root, "-o", self.snap)
        self.assertEqual(code, 0, err)
        return self.snap

    def check(self, *extra):
        return run_cli("check", self.root, self.snap, *extra)

    def test_snapshot_deterministic_and_portable(self):
        write(self.root, "a.txt", "hello\n")
        write(self.root, "sub/b.txt", "world\n")
        s1 = snapdrift.make_snapshot(self.root)
        s2 = snapdrift.make_snapshot(self.root)
        b1 = snapdrift.snapshot_bytes(s1)
        self.assertEqual(b1, snapdrift.snapshot_bytes(s2))
        self.assertNotIn(self._tmp.name.encode(), b1)
        self.assertNotIn(b"timestamp", b1)

    def test_check_clean_tree(self):
        write(self.root, "a.txt", "hello\n")
        os.makedirs(os.path.join(self.root, "empty"))
        self.snap_now()
        code, out, _ = self.check()
        self.assertEqual(code, 0)
        self.assertIn("no drift", out)

    def test_modified_file_is_changed(self):
        write(self.root, "a.txt", "hello\n")
        self.snap_now()
        write(self.root, "a.txt", "HELLO\n")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("changed: a.txt", out)

    def test_added_and_removed(self):
        write(self.root, "a.txt", "hello\n")
        self.snap_now()
        write(self.root, "new.txt", "new\n")
        os.remove(os.path.join(self.root, "a.txt"))
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("added: new.txt", out)
        self.assertIn("removed: a.txt", out)

    def test_renamed_file_is_moved_not_added_removed(self):
        write(self.root, "old-name.txt", "payload\n")
        self.snap_now()
        os.rename(os.path.join(self.root, "old-name.txt"),
                  os.path.join(self.root, "new-name.txt"))
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("moved: old-name.txt -> new-name.txt", out)
        self.assertNotIn("added: new-name.txt", out)
        self.assertNotIn("removed: old-name.txt", out)

    def test_move_into_subdirectory(self):
        write(self.root, "a.txt", "payload\n")
        os.makedirs(os.path.join(self.root, "sub"))
        self.snap_now()
        os.rename(os.path.join(self.root, "a.txt"),
                  os.path.join(self.root, "sub/a.txt"))
        code, out, _ = self.check("--json")
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["moved"],
                         [{"from": "a.txt", "sha256": report["moved"][0]["sha256"],
                           "to": "sub/a.txt"}])

    def test_touch_is_not_drift(self):
        path = write(self.root, "a.txt", "hello\n")
        self.snap_now()
        os.utime(path, (1_000_000, 1_000_000))
        code, out, _ = self.check()
        self.assertEqual(code, 0, out)

    def test_empty_dir_tracked(self):
        os.makedirs(os.path.join(self.root, "empty"))
        self.snap_now()
        os.rmdir(os.path.join(self.root, "empty"))
        os.makedirs(os.path.join(self.root, "other-empty"))
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("removed: empty", out)
        self.assertIn("added: other-empty", out)

    def test_file_becomes_directory_is_changed(self):
        write(self.root, "thing", "was a file\n")
        self.snap_now()
        os.remove(os.path.join(self.root, "thing"))
        os.makedirs(os.path.join(self.root, "thing"))
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("changed: thing", out)

    def test_binary_change_detected(self):
        write(self.root, "blob.bin", b"\x00\x01\x02")
        self.snap_now()
        write(self.root, "blob.bin", b"\x00\x01\x03")
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("changed: blob.bin", out)

    def test_symlink_target_change(self):
        write(self.root, "a.txt", "a\n")
        write(self.root, "b.txt", "b\n")
        link = os.path.join(self.root, "lnk")
        try:
            os.symlink("a.txt", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.snap_now()
        os.remove(link)
        os.symlink("b.txt", link)
        code, out, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn("changed: lnk", out)

    def test_diff_two_snapshots(self):
        write(self.root, "a.txt", "one\n")
        snap1 = os.path.join(self._tmp.name, "s1.json")
        code, _, err = run_cli("snap", self.root, "-o", snap1)
        self.assertEqual(code, 0, err)
        write(self.root, "a.txt", "two\n")
        snap2 = os.path.join(self._tmp.name, "s2.json")
        code, _, err = run_cli("snap", self.root, "-o", snap2)
        self.assertEqual(code, 0, err)
        code, out, _ = run_cli("diff", snap1, snap2)
        self.assertEqual(code, 1)
        self.assertIn("changed: a.txt", out)
        code, out, _ = run_cli("diff", snap1, snap1)
        self.assertEqual(code, 0)

    def test_malformed_snapshot_exit_2(self):
        write(self._tmp.name, "bad.json", "{not json")
        code, _, err = run_cli("check", self.root, os.path.join(self._tmp.name, "bad.json"))
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_wrong_format_snapshot_exit_2(self):
        write(self._tmp.name, "bad.json", json.dumps({"format": 99, "entries": []}))
        code, _, _ = run_cli("diff", os.path.join(self._tmp.name, "bad.json"),
                             os.path.join(self._tmp.name, "bad.json"))
        self.assertEqual(code, 2)

    def test_nonexistent_path_exit_2(self):
        code, _, err = run_cli("snap", os.path.join(self._tmp.name, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_default_skips_and_exclude_glob(self):
        write(self.root, ".git/config", "x")
        write(self.root, "keep.txt", "k\n")
        write(self.root, "skip.log", "s\n")
        snap = snapdrift.make_snapshot(self.root, excludes=["*.log"])
        paths = [e["path"] for e in snap["entries"]]
        self.assertNotIn(".git/config", paths)
        self.assertNotIn(".git", paths)
        self.assertNotIn("skip.log", paths)
        self.assertIn("keep.txt", paths)
        snap_all = snapdrift.make_snapshot(self.root, include_all=True)
        self.assertIn(".git/config", [e["path"] for e in snap_all["entries"]])

    def test_snapshot_inside_tree_is_excluded(self):
        write(self.root, "a.txt", "hello\n")
        inside = os.path.join(self.root, "snap.json")
        code, _, err = run_cli("snap", self.root, "-o", inside)
        self.assertEqual(code, 0, err)
        code, out, _ = run_cli("check", self.root, inside)
        self.assertEqual(code, 0, out)

    def test_json_report_is_deterministic(self):
        write(self.root, "a.txt", "hello\n")
        self.snap_now()
        write(self.root, "b.txt", "new\n")
        code1, out1, _ = self.check("--json")
        code2, out2, _ = self.check("--json")
        self.assertEqual(code1, 1)
        self.assertEqual(out1, out2)
        self.assertEqual(json.loads(out1)["added"], ["b.txt"])

    def test_duplicate_content_move_pairing_is_deterministic(self):
        write(self.root, "x.txt", "same\n")
        write(self.root, "y.txt", "same\n")
        self.snap_now()
        os.remove(os.path.join(self.root, "x.txt"))
        write(self.root, "z.txt", "same\n")
        code, out, _ = self.check("--json")
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(len(report["moved"]), 1)
        self.assertEqual(report["moved"][0]["from"], "x.txt")
        self.assertEqual(report["moved"][0]["to"], "z.txt")
        self.assertEqual(report["added"], [])
        self.assertEqual(report["removed"], [])


if __name__ == "__main__":
    unittest.main()
