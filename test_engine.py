"""Pass/fail tests for Photo Sorter and File Sorter. Run: python3 -m unittest test_engine"""
import errno
import hashlib
import http.client
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import engine

HERE = Path(__file__).resolve().parent
TOOL = engine.Tool(
    title="Test",
    images=(".jpg", ".png"),
    videos=(".mov",),
    categories=("Food", "Nature", "Other"),
    instructions="Sort these.",
)


class Base(unittest.TestCase):
    def setUp(self):
        # Inside the repo on purpose: this runs the safe move on the repo's own disk.
        self.dir = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def put(self, name, data=None):
        path = self.dir / name
        path.write_bytes(name.encode() if data is None else data)
        return path

    def tree(self):
        out = {}
        for p in self.dir.rglob("*"):
            if p.is_file() and ".organizer-runs" not in p.parts:
                out[p.relative_to(self.dir).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
        return out

    def sorted_items(self, mapping):
        items = engine.scan(self.dir, TOOL).items
        for item in items:
            item.category = mapping[item.name]
        return items


class Scanning(Base):
    def test_1_lists_only_top_level_media_any_case(self):
        for n in ("a.jpg", "B.JPG", "c.Png", "d.MOV", "notes.txt", ".hidden.jpg"):
            self.put(n)
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "deep.jpg").write_bytes(b"x")
        os.symlink(self.dir / "a.jpg", self.dir / "link.jpg")
        self.assertEqual([i.name for i in engine.scan(self.dir, TOOL).items],
                         sorted(["a.jpg", "B.JPG", "c.Png", "d.MOV"]))

    def test_2_extra_copies_go_to_duplicates_one_copy_sorted_normally(self):
        self.put("a.jpg", b"same")
        self.put("b.jpg", b"same")
        self.put("c.jpg", b"other")
        self.put("d.png", b"same")
        items = engine.scan(self.dir, TOOL).items
        self.assertEqual(engine.find_duplicates(self.dir, items), {"b.jpg": "a.jpg", "d.png": "a.jpg"})

    def test_25_cap_keeps_first_1500_in_name_order(self):
        for i in range(1501):
            self.put(f"{i:04}.jpg")
        result = engine.scan(self.dir, TOOL)
        self.assertEqual(len(result.items), 1500)
        self.assertEqual(result.items[-1].name, "1499.jpg")
        self.assertEqual(result.left_out, 1)

    def test_28_hostile_names_are_plain_files(self):
        names = ["-rf.jpg", "$(touch p).jpg", "a;b.jpg"]
        if not sys.platform == "win32":  # Windows can't create a file name containing a line break
            names.append("new\nline.jpg")
        for n in names:
            self.put(n)
        self.assertEqual(len(engine.scan(self.dir, TOOL).items), len(names))
        self.assertFalse((self.dir / "p").exists())


class FolderRefusal(Base):
    def assert_refused(self, path):
        with self.assertRaises(engine.Refused, msg=str(path)):
            engine.check_folder(path)

    def test_19_refuses_protected_folders(self):
        home = Path.home()
        for path in ("/", "/Users", "/Volumes", "/System", "/System/Library", "/Library",
                     "/usr/local", "/Applications", home, home / "Library", home / "Library" / "Caches"):
            self.assert_refused(path)

    def test_19_refuses_spelling_tricks(self):
        self.assert_refused(str(Path.home()).replace("/Users", "/USERS"))
        self.assert_refused("/SYSTEM")
        self.assert_refused(str(Path.home()) + "/./")

    def test_19_refuses_mounted_disk_roots(self):
        with mock.patch("os.path.ismount", return_value=True):
            self.assert_refused(self.dir)

    def test_19_refuses_inside_packages(self):
        for pack in ("My.photoslibrary", "Some.app"):
            inner = self.dir / pack / "originals"
            inner.mkdir(parents=True)
            self.assert_refused(inner)
            self.assert_refused(self.dir / pack)

    def test_refuses_a_missing_folder(self):
        self.assert_refused(self.dir / "nope")

    def test_19_apply_and_undo_check_the_folder_too(self):
        self.put("a.jpg")
        items = engine.scan(self.dir, TOOL).items
        items[0].category = "Food"
        with mock.patch("os.path.ismount", return_value=True):
            with self.assertRaises(engine.Refused):
                engine.apply(self.dir, items, TOOL)
            with self.assertRaises(engine.Refused):
                engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "a.jpg").exists())

    def test_19_blank_paths_and_system_var_folders_are_refused(self):
        for path in ("", "   ", "/var/log", "/private/var/log", "/var/db", "/private/tmp",
                     "/private/var/folders", "/private/var/vm"):
            self.assert_refused(path)

    def test_19_disk_number_paths_are_refused(self):
        st = self.dir.stat()
        self.assert_refused(f"/.vol/{st.st_dev}/{st.st_ino}")
        self.assert_refused("/.vol")

    def test_19_older_photo_libraries_are_refused(self):
        for pack in ("Old.photolibrary", "Aperture.aplibrary", "A.migratedphotolibrary", "Cat.lrdata"):
            inner = self.dir / pack
            inner.mkdir()
            self.assert_refused(inner)

    def test_19_odd_typed_paths_are_plain_refusals(self):
        self.assert_refused("~nosuchuser/x")
        self.assert_refused("/tmp/a\0b")

    @unittest.skipIf(sys.platform == "win32", "chmod 0 doesn't lock a folder on Windows, which uses ACLs")
    def test_an_unreadable_folder_is_a_plain_refusal(self):
        locked = self.dir / "locked"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        with self.assertRaises(engine.Refused) as caught:
            engine.scan(locked, TOOL)
        self.assertNotIn(str(self.dir), str(caught.exception))

    @unittest.skipIf(sys.platform == "win32", "chmod 0o500 doesn't stop new files in a Windows folder, which uses ACLs")
    def test_a_read_only_folder_is_a_plain_refusal_at_apply(self):
        self.put("a.jpg")
        items = engine.scan(self.dir, TOOL).items
        items[0].category = "Food"
        self.dir.chmod(0o500)
        self.addCleanup(self.dir.chmod, 0o700)
        with self.assertRaises(engine.Refused) as caught:
            engine.apply(self.dir, items, TOOL)
        self.assertNotIn(str(self.dir), str(caught.exception))

    def test_a_file_path_is_a_plain_refusal_for_the_undo_offer(self):
        with self.assertRaises(engine.Refused):
            engine.pending_undo(self.put("a.jpg"), TOOL)

    def test_19_a_link_to_a_protected_folder_is_refused(self):
        link = self.dir / "looks-fine"
        os.symlink(Path.home() / "Library", link)
        self.assert_refused(link)

    def test_allows_an_ordinary_folder(self):
        self.assertEqual(engine.check_folder(self.dir), self.dir)


class Applying(Base):
    def test_3_and_4_nothing_moves_before_apply_then_every_file_moves(self):
        self.put("a.jpg")
        self.put("b.jpg")
        before = self.tree()
        items = self.sorted_items({"a.jpg": "Food", "b.jpg": "Nature"})
        self.assertEqual(self.tree(), before)
        result = engine.apply(self.dir, items, TOOL)
        self.assertEqual(result.moved, 2)
        self.assertIsNone(result.failed)
        self.assertEqual(sorted(self.tree()), ["Food/a.jpg", "Nature/b.jpg"])

    def test_duplicates_folder_is_allowed(self):
        self.put("a.jpg")
        engine.apply(self.dir, self.sorted_items({"a.jpg": engine.DUPLICATES}), TOOL)
        self.assertEqual(list(self.tree()), ["Duplicates/a.jpg"])

    def test_5_clash_with_a_file_outside_the_run_never_overwrites(self):
        (self.dir / "Food").mkdir()
        (self.dir / "Food" / "a.jpg").write_bytes(b"OLD PHOTO")
        self.put("a.jpg", b"NEW PHOTO")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual((self.dir / "Food" / "a.jpg").read_bytes(), b"OLD PHOTO")
        self.assertEqual((self.dir / "Food" / "a (2).jpg").read_bytes(), b"NEW PHOTO")

    def test_5_two_files_landing_on_one_name_in_any_case_both_survive(self):
        self.put("a.jpg", b"ONE")
        (self.dir / "Food").mkdir()
        (self.dir / "Food" / "A (2).JPG").write_bytes(b"TWO")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual(len(self.tree()), 2)

    def test_5_safe_move_refuses_a_taken_name_on_this_disk(self):
        """The first test of story 2: the real call, on the disk this repo is on."""
        a, b = self.put("a.jpg", b"A"), self.put("b.jpg", b"B")
        with self.assertRaises(FileExistsError):
            engine.rename_excl(a, b)
        self.assertEqual((a.read_bytes(), b.read_bytes()), (b"A", b"B"))
        engine.rename_excl(a, self.dir / "c.jpg")
        self.assertEqual((self.dir / "c.jpg").read_bytes(), b"A")
        self.assertFalse(a.exists())

    def test_16_safe_move_refuses_a_link_anywhere_in_the_path(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, self.dir / "Food")
        a = self.put("a.jpg")
        with self.assertRaises(OSError):
            engine.rename_excl(a, self.dir / "Food" / "a.jpg")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue(a.exists())

    def test_14_a_failure_reason_never_shows_a_path(self):
        self.put("a.jpg")
        items = self.sorted_items({"a.jpg": "Food"})
        real = engine.rename_excl

        def fail(src, dst):
            if str(src).endswith("a.jpg"):
                raise OSError(errno.EIO, os.strerror(errno.EIO), str(dst))
            real(src, dst)

        with mock.patch("engine.rename_excl", fail):
            result = engine.apply(self.dir, items, TOOL)
        self.assertNotIn(str(self.dir), result.reason)

    def test_14_failed_move_stops_at_once_and_says_which_file(self):
        for n in ("a.jpg", "b.jpg", "c.jpg"):
            self.put(n)
        items = self.sorted_items(dict.fromkeys(("a.jpg", "b.jpg", "c.jpg"), "Food"))
        real = engine.rename_excl
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if Path(src).name == "b.jpg":
                raise OSError(errno.EIO, "disk error")
            return real(src, dst)

        with mock.patch("engine.rename_excl", flaky):
            result = engine.apply(self.dir, items, TOOL)
        self.assertEqual(result.moved, 1)
        self.assertEqual(result.failed, "b.jpg")
        self.assertEqual([Path(c).name for c in calls[-2:]], ["a.jpg", "b.jpg"])
        self.assertEqual(sorted(self.tree()), ["Food/a.jpg", "b.jpg", "c.jpg"])
        self.assertTrue(all(p.stat().st_size > 0 for p in self.dir.rglob("*.jpg")))
        undone = engine.undo(self.dir, TOOL)
        self.assertEqual((undone.restored, undone.left), (1, []))
        self.assertEqual(sorted(self.tree()), ["a.jpg", "b.jpg", "c.jpg"])

    def test_16_a_file_changed_since_the_scan_stops_apply(self):
        self.put("a.jpg", b"one")
        items = self.sorted_items({"a.jpg": "Food"})
        self.put("a.jpg", b"changed!")
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)
        self.assertEqual(list(self.tree()), ["a.jpg"])

    def test_16_a_same_size_edit_stops_apply(self):
        path = self.put("a.jpg", b"one")
        items = self.sorted_items({"a.jpg": "Food"})
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)

    def test_16_a_same_size_replacement_stops_apply(self):
        path = self.put("a.jpg", b"one")
        items = self.sorted_items({"a.jpg": "Food"})
        st = path.stat()
        other = self.put("other.tmp", b"two")
        os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(other, path)
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)

    def test_16_a_change_between_planning_and_moving_stops_that_move(self):
        path = self.put("a.jpg", b"one")
        items = self.sorted_items({"a.jpg": "Food"})
        real = engine._write_log

        def then_edit(folder, moves):
            real(folder, moves)
            path.write_bytes(b"edited")

        with mock.patch("engine._write_log", then_edit):
            result = engine.apply(self.dir, items, TOOL)
        self.assertEqual((result.moved, result.failed), (0, "a.jpg"))
        self.assertEqual(path.read_bytes(), b"edited")

    def test_an_empty_apply_writes_no_log(self):
        self.put("a.jpg")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual(engine.apply(self.dir, [], TOOL).moved, 0)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 1)

    def test_5_names_taken_earlier_in_the_run_are_avoided_in_any_case(self):
        taken = set()
        self.assertEqual(engine._free_name(self.dir, "a.jpg", taken), "a.jpg")
        self.assertEqual(engine._free_name(self.dir, "A.JPG", taken), "A (2).JPG")

    def test_16_a_file_swapped_for_a_link_is_refused(self):
        self.put("a.jpg")
        items = self.sorted_items({"a.jpg": "Food"})
        os.unlink(self.dir / "a.jpg")
        os.symlink("/etc/hosts", self.dir / "a.jpg")
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)

    def test_16_a_name_with_a_path_in_it_is_refused(self):
        self.put("a.jpg")
        items = self.sorted_items({"a.jpg": "Food"})
        items[0].name = "../a.jpg"
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)

    def test_16_a_name_inside_a_subfolder_is_refused_before_any_log(self):
        (self.dir / "sub").mkdir()
        st = self.put("sub/a.jpg").stat()
        items = [engine.Item("sub/a.jpg", st.st_ino, st.st_size, st.st_mtime_ns, category="Food")]
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, items, TOOL)
        self.assertFalse((self.dir / engine.RUNS).exists())

    def test_16_a_category_outside_the_list_is_refused(self):
        self.put("a.jpg")
        for bad in ("../Food", "Elsewhere", "/tmp"):
            with self.assertRaises(engine.Refused, msg=bad):
                engine.apply(self.dir, self.sorted_items({"a.jpg": bad}), TOOL)

    def test_29_a_linked_category_folder_moves_nothing(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, self.dir / "Food")
        self.put("a.jpg")
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((self.dir / "a.jpg").exists())

    def test_29_a_category_name_taken_by_a_file_moves_nothing(self):
        self.put("Food", b"a file")
        self.put("a.jpg")
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertTrue((self.dir / "a.jpg").exists())

    def test_29_a_category_swapped_for_a_link_during_apply_is_caught(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        self.put("a.jpg")
        self.put("b.jpg")
        items = self.sorted_items({"a.jpg": "Food", "b.jpg": "Food"})
        real = engine.rename_excl

        def swap(src, dst):
            real(src, dst)
            if str(src).endswith("a.jpg"):
                (self.dir / "Food").rename(self.dir / "Moved")
                os.symlink(outside, self.dir / "Food")

        with mock.patch("engine.rename_excl", swap):
            result = engine.apply(self.dir, items, TOOL)
        self.assertEqual(result.failed, "b.jpg")
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_linked_run_folder_is_refused(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, self.dir / engine.RUNS)
        self.put("a.jpg")
        with self.assertRaises(engine.Refused):
            engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)

    def test_32_disk_without_safe_move_is_refused_before_anything_moves(self):
        self.put("a.jpg")
        items = self.sorted_items({"a.jpg": "Food"})
        with mock.patch("engine.rename_excl", side_effect=OSError(errno.ENOTSUP, "no")):
            with self.assertRaises(engine.Refused):
                engine.apply(self.dir, items, TOOL)
        self.assertEqual(list(self.tree()), ["a.jpg"])
        self.assertFalse((self.dir / "Food").exists())


class Undoing(Base):
    def run_apply(self):
        self.put("a.jpg", b"AAA")
        self.put("b.jpg", b"BBB")
        before = self.tree()
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food", "b.jpg": "Nature"}), TOOL)
        return before

    def log_path(self):
        return next((self.dir / engine.RUNS).glob("*.jsonl"))

    def test_6_undo_restores_every_file_with_identical_contents(self):
        before = self.run_apply()
        result = engine.undo(self.dir, TOOL)
        self.assertEqual((result.restored, result.left), (2, []))
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.dir / "Food").exists())

    def test_6_undo_works_after_a_restart(self):
        before = self.run_apply()
        code = ("import engine, sys\n"
                "tool = engine.Tool('T', ('.jpg',), (), ('Food', 'Nature', 'Other'), 'x')\n"
                "print(engine.undo(sys.argv[1], tool).restored)")
        done = subprocess.run([sys.executable, "-c", code, str(self.dir)], cwd=HERE,
                              capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout.strip(), "2")
        self.assertEqual(self.tree(), before)

    def test_undo_keeps_a_folder_holding_anything_else(self):
        self.run_apply()
        (self.dir / "Food" / ".DS_Store").write_bytes(b"x")
        engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "Food" / ".DS_Store").exists())

    def test_undo_keeps_a_folder_that_was_there_before(self):
        (self.dir / "Food").mkdir()
        self.run_apply()
        engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "Food").is_dir())

    def test_undo_after_a_rename_uses_the_original_name(self):
        (self.dir / "Food").mkdir()
        (self.dir / "Food" / "a.jpg").write_bytes(b"OLD")
        self.put("a.jpg", b"NEW")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        engine.undo(self.dir, TOOL)
        self.assertEqual((self.dir / "a.jpg").read_bytes(), b"NEW")
        self.assertEqual((self.dir / "Food" / "a.jpg").read_bytes(), b"OLD")

    def test_15_taken_spot_is_left_alone_and_listed(self):
        self.run_apply()
        self.put("a.jpg", b"SOMEONE ELSE")
        result = engine.undo(self.dir, TOOL)
        self.assertEqual(result.restored, 1)
        self.assertEqual([x.name for x in result.left], ["a.jpg"])
        self.assertEqual((self.dir / "a.jpg").read_bytes(), b"SOMEONE ELSE")
        self.assertEqual((self.dir / "Food" / "a.jpg").read_bytes(), b"AAA")

    def test_15_missing_or_changed_file_is_left_alone_and_listed(self):
        self.run_apply()
        (self.dir / "Food" / "a.jpg").rename(self.dir / "elsewhere.jpg")
        (self.dir / "Nature" / "b.jpg").write_bytes(b"EDITED SINCE")
        result = engine.undo(self.dir, TOOL)
        self.assertEqual(result.restored, 0)
        self.assertEqual(sorted(x.name for x in result.left), ["a.jpg", "b.jpg"])
        self.assertEqual((self.dir / "Nature" / "b.jpg").read_bytes(), b"EDITED SINCE")

    def test_a_run_that_cannot_go_back_stops_blocking_older_runs(self):
        self.put("a.jpg")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.put("b.jpg")
        engine.apply(self.dir, self.sorted_items({"b.jpg": "Nature"}), TOOL)
        (self.dir / "Nature" / "b.jpg").write_bytes(b"edited")
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 0)  # might be temporary, so it stays on offer
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 1)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 0)  # twice with no progress closes it
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 1)
        self.assertTrue((self.dir / "a.jpg").exists())

    def test_a_failed_undo_stays_available(self):
        before = self.run_apply()
        (self.dir / "Food").rename(self.dir / "Away")
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 1)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 2)
        (self.dir / "Away").rename(self.dir / "Food")
        result = engine.undo(self.dir, TOOL)
        self.assertEqual((result.restored, result.left), (1, []))
        self.assertEqual(self.tree(), before)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 0)

    def test_a_bad_newest_log_does_not_block_the_real_run(self):
        before = self.run_apply()
        (self.dir / engine.RUNS / "20990101T000000000000-aaaa.jsonl").write_text('{"kind":"move","name":"../x"}\n')
        (self.dir / engine.RUNS / "20990102T000000000000-aaaa.jsonl").write_text("garbage\n")
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 2)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 2)
        self.assertEqual(self.tree(), before)

    @unittest.skipIf(sys.platform == "win32", "chmod 0 doesn't make a file unreadable on Windows")
    def test_a_log_that_cannot_be_read_does_not_block_the_real_run(self):
        before = self.run_apply()
        runs = self.dir / engine.RUNS
        (runs / "20990101T000000000000-aaaa.jsonl").write_text("[" * 200000 + "\n")
        (runs / "20990102T000000000000-aaaa.jsonl").write_text(
            '{"kind":"move","name":"\\ud800.jpg","dir":"Food","dest":"a.jpg","ino":1,"size":1,"mtime_ns":1}\n')
        locked = runs / "20990103T000000000000-aaaa.jsonl"
        locked.write_text("{}\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 2)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 2)
        self.assertEqual(self.tree(), before)

    def test_a_mark_that_cannot_be_written_still_reports_the_restore(self):
        self.run_apply()
        log = self.log_path()
        log.chmod(0o400)
        self.addCleanup(log.chmod, 0o600)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 2)

    def test_30_forged_log_cannot_hide_a_file_or_set_odd_flags(self):
        path = self.put("private.jpg")
        st = path.stat()
        (self.dir / "Food").mkdir()
        path.rename(self.dir / "Food" / "private.jpg")
        id_ = {"ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        runs = self.dir / engine.RUNS
        runs.mkdir()
        for line in ({"kind": "move", "name": ".zshrc.sh", "dir": "Food", "dest": "private.jpg", **id_},
                     {"kind": "move", "name": ".hidden.jpg", "dir": "Food", "dest": "private.jpg", **id_},
                     {"kind": "move", "name": "a.jpg", "dir": "Food", "dest": "private.jpg", "made": "yes", **id_}):
            (runs / "20990101T000000000000-aaaa.jsonl").write_text(json.dumps(line) + "\n")
            with self.assertRaises(engine.Refused, msg=str(line)):
                engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "Food" / "private.jpg").exists())

    def test_30_forged_log_moves_nothing(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        victim = outside / "victim.jpg"
        victim.write_bytes(b"precious")
        st = victim.stat()
        id_ = {"ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        forgeries = [
            {"kind": "move", "name": "x.jpg", "dir": "Food", "dest": "../../" + outside.name + "/victim.jpg", **id_},
            {"kind": "move", "name": "x.jpg", "dir": str(outside), "dest": "victim.jpg", **id_},
            {"kind": "move", "name": "../x.jpg", "dir": "Food", "dest": "a.jpg", **id_},
            {"kind": "move", "name": "x.jpg", "dir": "Unknown", "dest": "a.jpg", **id_},
            {"kind": "move", "name": "x.jpg", "dir": "Food", "dest": "..", **id_},
            {"kind": "move", "name": "", "dir": "Food", "dest": "a.jpg", **id_},
            {"kind": "move", "name": 5, "dir": "Food", "dest": "a.jpg", **id_},
        ]
        (self.dir / "Food").mkdir()
        runs = self.dir / engine.RUNS
        runs.mkdir()
        for line in forgeries:
            log = runs / "20990101T000000000000-aaaa.jsonl"
            log.write_text(json.dumps(line) + "\n")
            with self.assertRaises(engine.Refused, msg=str(line)):
                engine.undo(self.dir, TOOL)
        self.assertEqual(victim.read_bytes(), b"precious")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".organizer-runs", "Food"])

    def test_30_a_linked_log_is_refused(self):
        runs = self.dir / engine.RUNS
        runs.mkdir()
        os.symlink("/etc/hosts", runs / "20990101T000000000000-aaaa.jsonl")
        with self.assertRaises(engine.Refused):
            engine.undo(self.dir, TOOL)

    def test_30_a_link_to_a_valid_looking_log_is_refused(self):
        self.run_apply()
        log = self.log_path()
        hidden = self.dir / engine.RUNS / "elsewhere.txt"
        log.rename(hidden)
        os.symlink(hidden, log)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 0)

    def test_30_undo_into_a_linked_category_moves_nothing(self):
        outside = Path(tempfile.mkdtemp(dir=HERE, prefix=".test-")).resolve()
        self.addCleanup(shutil.rmtree, outside, True)
        self.run_apply()
        (self.dir / "Food").rename(self.dir / "Kept")
        os.symlink(outside, self.dir / "Food")
        with self.assertRaises(engine.Refused):
            engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "Kept" / "a.jpg").exists())

    def test_31_log_cut_mid_line_still_undoes_everything_before_it(self):
        before = self.run_apply()
        log = self.log_path()
        data = log.read_bytes()
        log.write_bytes(data + b'{"kind":"move","name":"half')
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 2)
        self.assertEqual(self.tree(), before)

    def test_31_an_unreadable_line_is_reported_and_undo_stays_on_offer(self):
        self.run_apply()
        log = self.log_path()
        lines = log.read_bytes().splitlines(keepends=True)
        log.write_bytes(lines[0] + b"garbled\n")
        result = engine.undo(self.dir, TOOL)
        self.assertEqual((result.restored, len(result.left)), (1, 1))
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 1)

    def test_31_log_is_cut_inside_the_real_lines(self):
        self.run_apply()
        log = self.log_path()
        lines = log.read_bytes().splitlines(keepends=True)
        log.write_bytes(lines[0] + lines[1][:10])
        result = engine.undo(self.dir, TOOL)
        self.assertEqual(result.restored, 1)
        self.assertEqual(sorted(self.tree()), ["Nature/b.jpg", "a.jpg"])

    @unittest.skipIf(sys.platform == "win32", "Windows can't create file names containing a line break")
    def test_31_file_name_with_a_line_break_survives_the_log(self):
        name = "new\nline.jpg"
        self.put(name, b"x")
        before = self.tree()
        engine.apply(self.dir, self.sorted_items({name: "Food"}), TOOL)
        self.assertEqual(len(self.log_path().read_bytes().strip().split(b"\n")), 1)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 1)
        self.assertEqual(self.tree(), before)

    def test_37_offer_and_newest_run_rule(self):
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 0)
        self.put("a.jpg")
        engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        (self.dir / "Food" / "a.jpg").rename(self.dir / "a2.jpg")
        self.put("b.jpg")
        engine.apply(self.dir, self.sorted_items({"a2.jpg": "Nature", "b.jpg": "Nature"}), TOOL)
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 2)
        self.assertEqual(engine.undo(self.dir, TOOL).restored, 2)
        self.assertEqual(sorted(self.tree()), ["a2.jpg", "b.jpg"])
        self.assertEqual(engine.pending_undo(self.dir, TOOL), 1)

    def test_undo_with_nothing_to_undo_is_a_plain_refusal(self):
        with self.assertRaises(engine.Refused):
            engine.undo(self.dir, TOOL)

    @unittest.skipIf(sys.platform == "win32", "Windows can't open or fsync a folder, so there is no second sync")
    def test_a_log_flush_is_forced_before_the_first_move(self):
        self.put("a.jpg")
        order = []
        real_sync, real_move = engine.full_sync, engine.rename_excl
        with mock.patch("engine.full_sync", side_effect=lambda f: (order.append("sync"), real_sync(f))), \
             mock.patch("engine.rename_excl", side_effect=lambda a, b: (order.append(Path(a).suffix), real_move(a, b))):
            engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual(order, ["sync", ".new", "sync", ".jpg"])

    @unittest.skipUnless(sys.platform == "win32", "Windows-only: the Mac also syncs the run-history folder")
    def test_a_log_flush_is_forced_before_the_first_move_on_windows(self):
        self.put("a.jpg")
        order = []
        real_sync, real_move = engine.full_sync, engine.rename_excl
        with mock.patch("engine.full_sync", side_effect=lambda f: (order.append("sync"), real_sync(f))), \
             mock.patch("engine.rename_excl", side_effect=lambda a, b: (order.append(Path(a).suffix), real_move(a, b))):
            engine.apply(self.dir, self.sorted_items({"a.jpg": "Food"}), TOOL)
        self.assertEqual(order, ["sync", ".new", ".jpg"])


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys, time
d = os.environ.get("HOME")  # the fake's notes go to a file named in its own source
notes = {notes!r}
raw = sys.stdin.read()
msg = json.loads(raw)
content = msg["message"]["content"]
ids = [int(b["text"].split()[1]) for b in content if b["type"] == "text" and b["text"].startswith("id ")]
with open(notes, "a") as f:
    f.write(json.dumps(dict(argv=sys.argv[1:], env=dict(os.environ), stdin=raw, cwd=os.getcwd())) + "\\n")
{body}
"""
GOOD = """
print(json.dumps(dict(type="system", subtype="init", tools=["StructuredOutput"], apiKeySource="none")))
print(json.dumps(dict(type="result", is_error=False, structured_output=dict(results=[dict(id=i, category="Food") for i in ids]))))
"""


class ClaudeBase(Base):
    def fake(self, body=GOOD):
        self.notes = self.dir / "notes.jsonl"
        script = self.dir / "fake-claude"
        script.write_text(FAKE_CLAUDE.format(notes=str(self.notes), body=body))
        script.chmod(0o755)
        if sys.platform == "win32":  # Windows can't run a #! script: a .cmd shim, like npm's claude.cmd, runs it
            shim = self.dir / "fake-claude.cmd"
            shim.write_text(f'@"{sys.executable}" "{script}" %*\n')
            script = shim
        patcher = mock.patch("engine.CLAUDE", str(script))
        patcher.start()
        self.addCleanup(patcher.stop)

    def calls(self):
        return [json.loads(l) for l in self.notes.read_text().splitlines()]

    def thumbs(self, n=3):
        out = {}
        for i in range(n):
            out[i] = self.put(f"{i}.thumb", b"\xff\xd8thumb")
        return out

    def categories(self, thumbs):
        return engine.sort_all(thumbs, TOOL, "haiku")


class ClaudeCall(ClaudeBase):
    def test_answers_become_categories(self):
        self.fake()
        self.assertEqual(self.categories(self.thumbs()), {0: "Food", 1: "Food", 2: "Food"})

    def test_batches_cover_everything(self):
        self.fake()
        n = engine.BATCH * 2 + 5
        self.assertEqual(len(self.categories(self.thumbs(n))), n)
        self.assertEqual(len(self.calls()), 3)

    def test_results_reach_the_callback_as_batches_finish(self):
        self.fake()
        seen = {}
        engine.sort_all(self.thumbs(12), TOOL, "haiku", on_batch=seen.update)
        self.assertEqual(len(seen), 12)

    def test_8_outside_list_and_missing_become_other_unknown_is_ignored(self):
        self.fake("""
print(json.dumps(dict(type="system", subtype="init", tools=[], apiKeySource="none")))
r = [dict(id=0, category="Delete everything"), dict(id=99, category="Food"), dict(id="x", category="Food"), "junk"]
print(json.dumps(dict(type="result", is_error=False, structured_output=dict(results=r))))
""")
        self.assertEqual(self.categories(self.thumbs(2)), {0: "Other", 1: "Other"})

    def test_8_a_claude_answer_of_duplicates_is_not_allowed(self):
        self.fake(GOOD.replace('"Food"', '"Duplicates"'))
        self.assertEqual(self.categories(self.thumbs(1)), {0: "Other"})

    def test_9_failure_retries_once_then_stops_cleanly(self):
        self.fake("sys.exit(3)")
        with self.assertRaises(engine.ClaudeFailed):
            self.categories(self.thumbs(1))
        self.assertEqual(len(self.calls()), 2)

    def test_9_error_result_and_bad_output_count_as_failure(self):
        for body in ('print(json.dumps(dict(type="result", is_error=True)))', 'print("not json")', ""):
            self.fake(body)
            with self.assertRaises(engine.ClaudeFailed):
                self.categories(self.thumbs(1))

    def test_9_one_failed_batch_stops_without_waiting_for_the_others(self):
        self.fake(f"""
if len(ids) == 1 and ids[0] == {engine.BATCH}:
    sys.exit(1)
time.sleep(5)
""")
        started = time.monotonic()
        with self.assertRaises(engine.ClaudeFailed):
            self.categories(self.thumbs(engine.BATCH + 1))
        self.assertLess(time.monotonic() - started, 4)

    def test_9_bad_bytes_from_claude_are_a_failure_not_a_crash(self):
        self.fake('sys.stdout.buffer.write(b"\\xff\\xfe\\n")')
        with self.assertRaises(engine.ClaudeFailed):
            self.categories(self.thumbs(1))

    def test_9_a_second_try_can_succeed(self):
        self.fake("if len(open(notes).readlines()) == 1: sys.exit(1)\n" + GOOD)
        self.assertEqual(self.categories(self.thumbs(1)), {0: "Food"})

    def test_9_timeout_counts_as_failure(self):
        self.fake("time.sleep(30)")
        with mock.patch("engine.CLAUDE_TIMEOUT", 0.3), self.assertRaises(engine.ClaudeSlow):
            self.categories(self.thumbs(1))

    def test_recording_extras_are_off_unless_asked_for(self):
        self.assertIs(engine.App(TOOL).view()["film"], False)

    def test_two_running_copies_get_their_own_thumbnail_folders(self):
        self.assertIn(str(os.getpid()), engine.THUMBS.name)
        self.assertIn(str(os.getpid()), engine.EMPTY.name)

    def test_marking_undo_never_writes_through_a_link(self):
        target = self.dir / "elsewhere.txt"
        target.write_bytes(b"keep")
        log = self.dir / "run.jsonl"
        os.symlink(target, log)
        engine._mark(log, "undone")
        self.assertEqual(target.read_bytes(), b"keep")

    def test_a_preview_that_fails_once_is_retried(self):
        calls = []
        def flaky(folder, index, item):
            calls.append(index)
            return None if calls.count(index) == 1 and index == 1 else self.dir / f"{index}.jpg"
        items = [engine.Item(f"{i}.jpg", i, 1, 1, False) for i in range(3)]
        with mock.patch("engine._thumb_if_unchanged", flaky):
            made, skipped = engine.make_thumbs(self.dir, items)
        self.assertEqual(sorted(made), [0, 1, 2])
        self.assertEqual(skipped, [])

    def test_9_a_slow_claude_says_busy_not_signed_out(self):
        app = engine.App(TOOL)
        with mock.patch("engine.THUMBS", self.dir / "thumbs"):  # never the running app's thumbnails
            app.fail(engine.ClaudeSlow("timed out"))
        self.assertIn("busy", app.message)
        self.assertNotIn("signed in", app.message)

    def test_13_runs_with_no_tools_on_a_short_allow_list(self):
        self.fake()
        extra = {"ANTHROPIC_API_KEY": "k", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "http://evil",
                 "CLAUDE_CODE_USE_BEDROCK": "1", "SOME_SECRET": "s"}
        with mock.patch.dict(os.environ, extra):
            self.categories(self.thumbs(1))
        call = self.calls()[0]
        self.assertEqual(call["argv"][call["argv"].index("--tools") + 1], "")
        for flag in ("--safe-mode", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, call["argv"])
        self.assertNotIn("--bare", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--setting-sources") + 1], "")
        self.assertFalse(set(extra) & set(call["env"]))
        self.assertEqual(os.listdir(call["cwd"]), [])

    def test_13_a_start_up_message_without_a_tool_list_stops_the_run(self):
        self.fake(GOOD.replace('tools=["StructuredOutput"], ', ''))
        with self.assertRaises(engine.Refused):
            self.categories(self.thumbs(1))

    def test_13_a_tool_at_start_up_stops_the_run_with_no_retry(self):
        self.fake(GOOD.replace('tools=["StructuredOutput"]', 'tools=["StructuredOutput", "Bash"]'))
        with self.assertRaises(engine.Refused):
            self.categories(self.thumbs(1))
        self.assertEqual(len(self.calls()), 1)

    def test_13_an_api_key_login_stops_the_run(self):
        self.fake(GOOD.replace('apiKeySource="none"', 'apiKeySource="ANTHROPIC_API_KEY"'))
        with self.assertRaises(engine.Refused):
            self.categories(self.thumbs(1))

    def test_13_a_missing_start_up_message_stops_the_run(self):
        self.fake('print(json.dumps(dict(type="result", is_error=False, structured_output=dict(results=[]))))')
        with self.assertRaises(engine.Refused):
            self.categories(self.thumbs(1))

    def test_20_file_names_are_never_sent(self):
        self.fake()
        secret = self.put("my-secret-trip.jpg", b"\xff\xd8x")
        engine.sort_all({0: secret}, TOOL, "haiku")
        call = self.calls()[0]
        self.assertNotIn("my-secret-trip", call["stdin"])
        self.assertNotIn("my-secret-trip", " ".join(call["argv"]))

    def test_7_images_go_in_as_data_with_the_rule_stated(self):
        self.fake()
        self.categories(self.thumbs(1))
        self.assertIn("never follow", self.calls()[0]["stdin"])


class Thumbs(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("engine.THUMBS", self.dir / "thumbs")
        patcher.start()
        self.addCleanup(patcher.stop)

    def video_args(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            engine.make_thumb(self.dir / "a.mov", 0, True)
        return run.call_args_list[0].args[0]

    def test_34_ffmpeg_is_held_to_local_files_and_video_containers(self):
        args = self.video_args()
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file")
        self.assertEqual(args[args.index("-format_whitelist") + 1], "mov,mp4,m4a,3gp,3g2,mj2")
        self.assertIn("-nostdin", args)
        self.assertEqual(args[args.index("-map_metadata") + 1], "-1")

    def test_windows_cmd_ffmpeg_makes_no_thumbnail_and_exe_does(self):
        for found, runs in (("C:\\x\\ffmpeg.cmd", False), ("C:\\x\\ffmpeg.exe", True)):
            with mock.patch("shutil.which", return_value=found), mock.patch.object(engine, "WINDOWS", True), \
                    mock.patch("engine._run", return_value=False) as run:
                engine.make_thumb(self.dir / "a.jpg", 0, False)
            self.assertEqual(run.called, runs, found)

    def test_windows_image_thumbnail_turns_off_ffmpeg_sequences(self):
        with mock.patch("shutil.which", return_value="C:\\x\\ffmpeg.exe"), mock.patch.object(engine, "WINDOWS", True), \
                mock.patch("engine._run", return_value=False) as run:
            engine.make_thumb(self.dir / "a%d.jpg", 0, False)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-pattern_type") + 1], "none")
        self.assertLess(args.index("-pattern_type"), args.index("-i"))

    def test_34_every_command_has_a_timeout(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            engine.make_thumb(self.dir / "a.mov", 0, True)
            engine.make_thumb(self.dir / "b.jpg", 1, False)
        self.assertTrue(run.call_args_list)
        self.assertTrue(all(c.kwargs.get("timeout") for c in run.call_args_list))

    def test_34_a_hanging_command_is_stopped_and_skipped(self):
        self.put("a.jpg")
        slow = self.put("slow", b"#!/bin/sh\nsleep 30\n")
        slow.chmod(0o755)
        with mock.patch("engine.SIPS", str(slow)), mock.patch("engine.THUMB_TIMEOUT", 0.3):
            self.assertIsNone(engine.make_thumb(self.dir / "a.jpg", 0, False))

    def test_27_failed_thumbs_are_listed_not_fatal(self):
        self.put("a.jpg")
        self.put("b.jpg")
        items = engine.scan(self.dir, TOOL).items
        with mock.patch("engine.make_thumb", side_effect=lambda src, i, v: Path("x") if i == 0 else None):
            made, skipped = engine.make_thumbs(self.dir, items)
        self.assertEqual((list(made), skipped), ([0], ["b.jpg"]))

    def test_16_a_file_swapped_for_a_link_after_the_scan_is_not_previewed(self):
        self.put("a.jpg")
        items = engine.scan(self.dir, TOOL).items
        os.unlink(self.dir / "a.jpg")
        os.symlink("/etc/hosts", self.dir / "a.jpg")
        with mock.patch("engine.make_thumb") as make:
            made, skipped = engine.make_thumbs(self.dir, items)
        make.assert_not_called()
        self.assertEqual((made, skipped), ({}, ["a.jpg"]))

    @unittest.skipIf(sys.platform == "win32", "Windows has no POSIX mode bits, so chmod 0o755 can't open the folder to others")
    def test_11_a_private_folder_with_open_permissions_is_refused(self):
        engine.THUMBS.mkdir(mode=0o755)
        engine.THUMBS.chmod(0o755)
        with self.assertRaises(engine.Refused):
            engine._thumbs_dir()

    def test_11_clear_removes_only_thumbs_and_the_start_sweep_uses_it(self):
        engine.THUMBS.mkdir(mode=0o700)
        (engine.THUMBS / "1.jpg").write_bytes(b"x")
        (engine.THUMBS / "keep.txt").write_bytes(b"x")
        engine.clear_thumbs()
        self.assertEqual(os.listdir(engine.THUMBS), ["keep.txt"])

    def test_11_a_linked_thumbs_folder_is_refused(self):
        os.symlink(self.dir, engine.THUMBS)
        self.put("victim.jpg")
        with self.assertRaises(engine.Refused):
            engine.clear_thumbs()
        self.assertTrue((self.dir / "victim.jpg").exists())


def exif_gps_jpeg(jpeg):
    """A JPEG with a real EXIF GPS block planted after its start marker."""
    import struct
    gps = struct.pack(">H", 1) + struct.pack(">HHII", 1, 2, 2, 0x4E000000) + struct.pack(">I", 0)
    gps_offset = 8 + 2 + 12 + 4
    ifd0 = struct.pack(">H", 1) + struct.pack(">HHII", 0x8825, 4, 1, gps_offset) + struct.pack(">I", 0)
    tiff = b"MM\x00*" + struct.pack(">I", 8) + ifd0 + gps
    body = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + jpeg[2:]


@unittest.skipUnless(shutil.which(engine.FFMPEG), "real ffmpeg proof runs in the local Mac run")
class RealThumbs(Thumbs):
    def ffmpeg(self, out, *source):
        subprocess.run([engine.FFMPEG, "-loglevel", "error", "-y", *source, str(self.dir / out)], check=True)
        return self.dir / out

    @unittest.skipIf(sys.platform == "win32", "sips, the macOS image tool, isn't on Windows")
    def test_33_planted_gps_is_gone_from_the_thumbnail(self):
        src = self.ffmpeg("plain.jpg", "-f", "lavfi", "-i", "testsrc=s=600x400", "-frames:v", "1")
        planted = self.put("gps.jpg", exif_gps_jpeg(src.read_bytes()))
        probe = self.dir / "probe.jpg"
        subprocess.run([engine.SIPS, "-s", "format", "jpeg", "-Z", "256", str(planted), "--out", str(probe)],
                       check=True, capture_output=True)
        self.assertIn(b"Exif", probe.read_bytes(), "sips should keep it, or this test proves nothing")
        thumb = engine.make_thumb(planted, 0, False)
        self.assertNotIn(b"Exif", thumb.read_bytes())
        self.assertNotIn(b"Lavc", thumb.read_bytes())
        self.assertEqual(os.listdir(engine.THUMBS), ["0.jpg"])

    @unittest.skipIf(sys.platform == "win32", "sips, the macOS image tool, isn't on Windows")
    def test_4_thumbnail_is_256_on_the_long_side(self):
        src = self.ffmpeg("big.jpg", "-f", "lavfi", "-i", "testsrc=s=800x400", "-frames:v", "1")
        thumb = engine.make_thumb(src, 0, False)
        out = subprocess.run(["sips", "-g", "pixelWidth", str(thumb)], capture_output=True, text=True).stdout
        self.assertIn("256", out)

    def test_34_a_real_mov_works(self):
        src = self.ffmpeg("a.mov", "-f", "lavfi", "-i", "testsrc=s=600x400:d=1")
        thumb = engine.make_thumb(src, 0, True)
        self.assertEqual(thumb.read_bytes()[:2], b"\xff\xd8")
        self.assertNotIn(b"Lavf", thumb.read_bytes()[:64])

    def test_34_a_playlist_named_mov_is_skipped(self):
        secret = self.put("secret.txt", b"CANARY")
        fake = self.put("fake.mov", f"#EXTM3U\n#EXTINF:1,\nfile://{secret}\n#EXT-X-ENDLIST\n".encode())
        self.assertIsNone(engine.make_thumb(fake, 0, True))

    def test_27_a_file_that_cannot_be_read_is_skipped(self):
        self.assertIsNone(engine.make_thumb(self.put("bad.jpg", b"not an image"), 0, False))

    def test_28_a_hostile_name_makes_a_thumbnail(self):
        src = self.ffmpeg("x.jpg", "-f", "lavfi", "-i", "testsrc=s=64x64", "-frames:v", "1")
        src.rename(self.dir / "-rf.jpg")
        self.assertIsNotNone(engine.make_thumb(self.dir / "-rf.jpg", 0, False))


class ServerBase(ClaudeBase):
    tool = TOOL

    def setUp(self):
        super().setUp()
        self.fake()
        self.addCleanup(mock.patch.stopall)
        self.photos = self.dir / "photos"
        self.photos.mkdir()
        mock.patch("engine.THUMBS", self.dir / "thumbs").start()

        def thumbs(folder, items, tick=None):
            engine._thumbs_dir()
            made = {i: engine.THUMBS / f"{i}.jpg" for i in range(len(items))}
            for path in made.values():
                path.write_bytes(b"\xff\xd8thumb")
            return made, []

        mock.patch("engine.make_thumbs", thumbs).start()
        self.app = engine.App(self.tool)
        self.server = engine.start_server(self.app)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, headers=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Token"] = self.app.token
        h.update(headers or {})
        conn.request(method, path, None if body is None else json.dumps(body), h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def state(self):
        return json.loads(self.call("GET", "/api/state")[1])

    def wait_for(self, *phases):
        for _ in range(500):
            s = self.state()
            if s["phase"] in phases:
                return s
            time.sleep(0.01)
        self.fail(f"stuck in {s['phase']}")

    def add(self, *names):
        for n in names:
            (self.photos / n).write_bytes(n.encode())

    def scanned(self, *names):
        self.add(*names)
        r, _ = self.call("POST", "/api/scan", {"path": str(self.photos)})
        self.assertEqual(r.status, 200)
        return self.wait_for("scanned")

    def scanned_any(self):
        self.call("POST", "/api/scan", {"path": str(self.photos)})
        return self.wait_for("scanned", "empty")

    def reviewed(self, *names):
        self.scanned(*names)
        self.assertEqual(self.call("POST", "/api/sort", {})[0].status, 200)
        return self.wait_for("review")


class Server(ServerBase):
    def test_10_answers_only_on_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertNotEqual(self.port, 0)

    def test_17_wrong_or_missing_token_is_refused(self):
        self.assertEqual(self.call("GET", "/api/state", token=False)[0].status, 403)
        self.assertEqual(self.call("GET", "/api/state", headers={"X-Token": "wrong"})[0].status, 403)
        self.assertEqual(self.call("POST", "/api/apply", {}, token=False)[0].status, 403)

    def test_17_other_websites_are_refused(self):
        self.assertEqual(self.call("GET", "/api/state", headers={"Origin": "https://evil.example"})[0].status, 403)
        self.assertEqual(self.call("POST", "/api/apply", {}, headers={"Origin": "null"})[0].status, 403)

    def test_17_wrong_host_is_refused_even_for_the_page(self):
        self.assertEqual(self.call("GET", "/", token=False, headers={"Host": "evil.example"})[0].status, 403)
        self.assertEqual(self.call("GET", "/api/state", headers={"Host": f"evil.example:{self.port}"})[0].status, 403)

    def test_own_origin_is_allowed(self):
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.call("GET", "/api/state", headers={"Origin": origin})[0].status, 200)

    def test_35_thumbnail_needs_the_token(self):
        self.scanned("a.jpg")
        self.assertEqual(self.call("GET", "/api/thumb/0", token=False)[0].status, 403)
        r, data = self.call("GET", "/api/thumb/0")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "image/jpeg"))

    def test_35_thumbnail_route_takes_only_a_number(self):
        for path in ("/api/thumb/../../etc/passwd", "/api/thumb/x", "/api/thumb/999", "/api/thumb/-1"):
            self.assertEqual(self.call("GET", path)[0].status, 404, path)

    def test_35_page_blocks_framing_and_type_guessing(self):
        r, _ = self.call("GET", "/", token=False)
        self.assertEqual(r.status, 200)
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_35_only_the_fixed_routes_exist(self):
        for path in ("/engine.py", "/.git/config", "/../engine.py", "/api/nope"):
            self.assertEqual(self.call("GET", path)[0].status, 404, path)

    def test_35_actions_are_post_only_and_need_json(self):
        self.assertEqual(self.call("GET", "/api/apply")[0].status, 405)
        self.assertEqual(self.call("POST", "/api/apply", {}, headers={"Content-Type": "text/plain"})[0].status, 415)

    def test_35_second_apply_is_refused(self):
        self.reviewed("a.jpg")
        with self.app.busy:
            self.assertEqual(self.call("POST", "/api/apply", {})[0].status, 409)
        self.assertEqual(self.call("POST", "/api/apply", {})[0].status, 200)
        self.assertEqual(self.call("POST", "/api/apply", {})[0].status, 409)

    def test_26_typed_path_loads_a_folder(self):
        s = self.scanned("a.jpg", "b.png")
        self.assertEqual(s["names"], ["a.jpg", "b.png"])
        self.assertEqual(s["folder"], str(self.photos))

    def test_26_picker_returns_the_chosen_path_or_nothing(self):
        with mock.patch("engine.choose_folder", return_value="/some/folder"):
            self.assertEqual(json.loads(self.call("POST", "/api/pick", {})[1]), {"path": "/some/folder"})
        with mock.patch("engine.choose_folder", return_value=None):
            self.assertEqual(json.loads(self.call("POST", "/api/pick", {})[1]), {"path": None})

    def test_19_a_refused_folder_is_a_plain_message(self):
        r, data = self.call("POST", "/api/scan", {"path": "/System"})
        self.assertEqual(r.status, 400)
        self.assertIn("error", json.loads(data))

    def test_bad_path_values_are_refused(self):
        for body in ({"path": 5}, {}, {"path": None}):
            self.assertEqual(self.call("POST", "/api/scan", body)[0].status, 400)

    def test_3_review_moves_nothing_until_apply(self):
        self.reviewed("a.jpg")
        self.assertTrue((self.photos / "a.jpg").exists())

    def test_2_extra_copies_go_to_duplicates_and_skip_claude(self):
        (self.photos / "a.jpg").write_bytes(b"same")
        (self.photos / "b.jpg").write_bytes(b"same")
        s = self.reviewed()
        self.assertEqual(s["duplicates"], {"1": 0})
        self.assertEqual(s["results"], {"0": "Food", "1": "Duplicates"})
        self.assertNotIn('"id 1"', self.calls()[0]["stdin"])

    def test_27_skipped_files_stay_put_and_are_listed(self):
        def some(folder, items, tick=None):
            engine._thumbs_dir()
            (engine.THUMBS / "0.jpg").write_bytes(b"\xff\xd8")
            return {0: engine.THUMBS / "0.jpg"}, ["b.jpg"]

        with mock.patch("engine.make_thumbs", some):
            s = self.reviewed("a.jpg", "b.jpg")
        self.assertEqual(s["skipped"], ["b.jpg"])
        self.call("POST", "/api/apply", {})
        self.assertTrue((self.photos / "b.jpg").exists())
        self.assertTrue((self.photos / "Food" / "a.jpg").exists())

    def test_6_and_11_full_flow_clears_thumbs_and_undo_restores(self):
        self.reviewed("a.jpg", "b.jpg")
        self.assertEqual(self.call("POST", "/api/apply", {})[0].status, 200)
        s = self.state()
        self.assertEqual((s["phase"], s["moved"]), ("applied", 2))
        self.assertEqual(list(engine.THUMBS.glob("*.jpg")), [])
        self.assertTrue((self.photos / "Food" / "a.jpg").exists())
        self.assertEqual(self.call("POST", "/api/undo", {})[0].status, 200)
        s = self.state()
        self.assertEqual((s["phase"], s["restored"], s["left"]), ("undone", 2, []))
        self.assertTrue((self.photos / "a.jpg").exists())

    def test_9_and_11_claude_failure_stops_cleanly(self):
        self.fake("sys.exit(1)")
        self.scanned("a.jpg")
        self.call("POST", "/api/sort", {})
        s = self.wait_for("problem")
        self.assertIn("no file", s["message"].lower())
        self.assertTrue((self.photos / "a.jpg").exists())
        self.assertEqual(list(engine.THUMBS.glob("*.jpg")), [])

    def test_14_stopped_partway_keeps_undo(self):
        self.reviewed("a.jpg", "b.jpg")
        real = engine.rename_excl

        def fail_b(src, dst):
            if str(src).endswith("b.jpg"):
                raise OSError(errno.EIO, "disk error")
            real(src, dst)

        with mock.patch("engine.rename_excl", fail_b):
            self.call("POST", "/api/apply", {})
        s = self.state()
        self.assertEqual((s["phase"], s["moved"], s["failed"]), ("stopped", 1, "b.jpg"))
        self.assertEqual(self.call("POST", "/api/undo", {})[0].status, 200)
        self.assertEqual(self.state()["restored"], 1)

    def test_37_picking_a_folder_with_a_pending_run_offers_undo(self):
        self.reviewed("a.jpg")
        self.call("POST", "/api/apply", {})
        self.app.__init__(self.tool)  # as if the app had restarted, keeping the old token out of the way
        s = self.scanned_any()
        self.assertEqual(s["pending_undo"], 1)

    def test_reset_returns_to_the_start_and_clears_thumbs(self):
        self.scanned("a.jpg")
        self.assertEqual(self.call("POST", "/api/reset", {})[0].status, 200)
        s = self.state()
        self.assertEqual((s["phase"], s["names"], s["folder"]), ("start", [], None))
        self.assertEqual(list(engine.THUMBS.glob("*.jpg")), [])

    def test_a_refused_post_ends_the_connection(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        inner = f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n"
        sock.sendall(f"POST /api/undo HTTP/1.1\r\nHost: evil\r\nContent-Length: {len(inner)}\r\n\r\n{inner}".encode())
        data = b""
        while chunk := sock.recv(65536):
            data += chunk
        sock.close()
        self.assertEqual(data.count(b"HTTP/1.1 "), 1)
        self.assertIn(b"403", data[:20])

    def test_odd_header_values_are_refused_cleanly(self):
        self.assertEqual(self.call("GET", "/api/state", headers={"X-Token": "caf\u00e9".encode("latin-1")})[0].status, 403)
        self.assertEqual(self.call("POST", "/api/scan", {}, headers={"Content-Length": "x"})[0].status, 400)
        self.assertEqual(self.call("POST", "/api/scan", {}, headers={"Content-Length": "-1"})[0].status, 400)
        self.assertEqual(self.call("GET", "/api/thumb/" + "9" * 5000)[0].status, 404)

    def test_empty_folder_says_so(self):
        s = self.scanned_any()
        self.assertEqual(s["phase"], "empty")

    def test_nothing_previewable_is_a_problem_not_a_success(self):
        with mock.patch("engine.make_thumbs", lambda f, i, tick=None: ({}, [x.name for x in i])):
            self.add("a.jpg")
            self.call("POST", "/api/scan", {"path": str(self.photos)})
            s = self.wait_for("problem")
        self.assertIn("previewed", s["message"])

    def test_scan_counts_progress(self):
        def counted(folder, items, tick=None):
            engine._thumbs_dir()
            (engine.THUMBS / "0.jpg").write_bytes(b"x")
            tick()
            return {0: engine.THUMBS / "0.jpg"}, []

        with mock.patch("engine.make_thumbs", counted):
            self.assertEqual(self.scanned("a.jpg")["previewed"], 1)

    def test_9_unexpected_claude_output_cannot_stick_the_screen(self):
        for extra in ('print(json.dumps(dict(type="system", subtype="init", tools=[dict(a=1)], apiKeySource="none")))',
                      'print(json.dumps(dict(type="result", is_error=False, structured_output=[1])))'):
            self.fake(extra)
            self.scanned("a.jpg")
            self.call("POST", "/api/sort", {})
            self.wait_for("problem")
            self.assertEqual(list(engine.THUMBS.glob("*.jpg")), [])

    def test_a_plain_file_named_like_the_run_folder_keeps_the_app_usable(self):
        (self.photos / engine.RUNS).write_bytes(b"x")
        r, data = self.call("POST", "/api/scan", {"path": str(self.photos)})
        self.assertEqual(r.status, 400)
        self.assertEqual(self.state()["phase"], "start")

    def test_a_scan_while_busy_is_refused(self):
        with self.app.busy:
            self.assertEqual(self.call("POST", "/api/scan", {"path": str(self.photos)})[0].status, 409)

    def test_a_sort_before_a_scan_is_refused(self):
        self.assertEqual(self.call("POST", "/api/sort", {})[0].status, 409)

    @unittest.skipIf(sys.platform == "win32", "Windows can't create file names containing < or >")
    def test_18_and_20_state_carries_names_only_as_data(self):
        s = self.scanned("<img src=x onerror=alert(1)>.jpg")
        r, _ = self.call("GET", "/api/state")
        self.assertEqual(r.getheader("Content-Type"), "application/json")
        self.assertEqual(len(s["names"]), 1)


class Startup(unittest.TestCase):
    def test_missing_programs_are_named_with_the_install_step(self):
        with mock.patch("shutil.which", lambda name: None if name == "ffmpeg" else "/x"), \
                mock.patch.object(engine, "WINDOWS", False):
            self.assertIn("brew install ffmpeg", engine.missing_setup())

    def test_windows_cmd_ffmpeg_is_reported_missing_and_exe_is_accepted(self):
        for found, ok in (("C:\\x\\ffmpeg.CMD", False), ("C:\\x\\ffmpeg.bat", False), ("C:\\x\\ffmpeg.exe", True)):
            with mock.patch("shutil.which", lambda name, found=found: found), mock.patch.object(engine, "WINDOWS", True):
                self.assertEqual("ffmpeg" not in engine.missing_setup(), ok, found)

    def test_windows_setup_steps_use_winget_and_say_this_computer(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("sys.version_info", (3, 9, 0)), \
                mock.patch.object(engine, "WINDOWS", True):
            needs = engine.missing_setup()
            self.assertIn("winget install Python.Python.3.12", needs)
            self.assertIn("winget install Gyan.FFmpeg", needs)
            self.assertNotIn("brew", needs)
            with self.assertRaises(SystemExit) as stop:
                engine.serve(FILES)
        self.assertTrue(str(stop.exception.code).startswith("Before this can run, this computer needs:\n"))

    def test_old_python_is_named(self):
        with mock.patch("sys.version_info", (3, 9, 0)), mock.patch("shutil.which", return_value="/x"):
            self.assertIn("Python 3.11", engine.missing_setup())

    def test_all_present_needs_nothing(self):
        with mock.patch("shutil.which", return_value="/x"):
            self.assertEqual(engine.missing_setup(), "")


class SecondTool(ServerBase):
    tool = engine.Tool("Other tool", (".png",), (), ("Red", "Blue"), "Sort by colour.")

    def test_24_a_second_tool_runs_the_whole_engine(self):
        self.fake(GOOD.replace('"Food"', '"Red"'))
        (self.photos / "a.png").write_bytes(b"x")
        (self.photos / "ignored.jpg").write_bytes(b"y")
        s = self.reviewed()
        self.assertEqual(s["folders"], ["Red", "Blue", "Duplicates"])
        self.assertEqual(s["title"], "Other tool")
        self.call("POST", "/api/apply", {})
        self.assertTrue((self.photos / "Red" / "a.png").exists())
        self.call("POST", "/api/undo", {})
        self.assertTrue((self.photos / "a.png").exists())


# File Sorter.
import file_sorter  # noqa: E402

FILES = file_sorter.TOOL
REAL_MAKE_THUMBS = engine.make_thumbs  # kept before any test replaces it
VIDEOS = "Videos" if sys.platform == "win32" else "Movies"  # the home folder for films on each system


def other_drive_folder():
    """A folder the checks allow, likely on another drive from the test folder: the Mac's own temporary folder,
    or on Windows the home folder (C: on GitHub's runner, whose checkout is on D:), since AppData\\Local\\Temp is refused."""
    return Path(tempfile.mkdtemp(dir=Path.home() if engine.WINDOWS else None, prefix="organizer-test-")).resolve()


class FilesBase(Base):
    def setUp(self):
        super().setUp()
        self.home = self.dir / "home"
        for part in engine.SWEEP:
            (self.home / part).mkdir(parents=True)
        self.dest = self.dir / "Tidy"
        self.dest.mkdir()
        mock.patch("engine._ask", side_effect=AssertionError("Claude was called")).start()
        mock.patch("engine.make_thumbs", side_effect=AssertionError("Photo Sorter thumbnails were made")).start()
        mock.patch("engine.PREVIEWS", self.dir / "previews").start()
        self.previews = mock.patch("engine.make_previews", return_value={}).start()
        self.addCleanup(mock.patch.stopall)

    def at(self, rel, data=None):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode() if data is None else data)
        return path

    def app(self, dest=True):
        app = engine.App(FILES, home=self.home)
        if dest:
            app.set_dest(str(self.dest))
        return app

    def go(self, app, start):
        job = start()
        if job:
            job()
        return app

    def swept(self, app=None):
        app = app or self.app()
        self.go(app, app.sweep)
        app.sort()
        return app

    def everything(self):
        out = {}
        for p in self.dir.rglob("*"):
            if p.is_file() and engine.RUNS not in p.parts:
                out.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(str(p.relative_to(self.dir)))
        return out


class Files(FilesBase):
    def test_F1_every_type_listed_except_hidden_links_folders_and_half_downloads(self):
        for n in ("a.pdf", "b.XLSX", "c.zip", "noext", "d.weird", ".hidden.pdf", "e.crdownload", "f.part"):
            self.at(f"Downloads/{n}")
        (self.home / "Downloads" / "sub").mkdir()
        os.symlink(self.home / "Downloads" / "a.pdf", self.home / "Downloads" / "link.pdf")
        names = [i.name for i in engine.scan(self.home / "Downloads", FILES).items]
        self.assertEqual(names, sorted(["a.pdf", "b.XLSX", "c.zip", "noext", "d.weird"]))

    @unittest.skipIf(sys.platform == "win32", "Windows has no os.chflags or iCloud dataless flag")
    def test_F1_icloud_files_not_on_this_mac_are_skipped(self):
        self.at("Desktop/here.pdf")
        away = self.at("Desktop/away.pdf")
        os.chflags(away, stat.UF_NODUMP)  # a flag a test can set, standing in for "dataless"
        with mock.patch("engine.SF_DATALESS", stat.UF_NODUMP):
            self.assertEqual([i.name for i in engine.scan(self.home / "Desktop", FILES).items], ["here.pdf"])

    def test_F2_each_type_lands_in_its_category_and_copies_go_to_duplicates_across_folders(self):
        self.at("Desktop/report.pdf", b"same")
        self.at("Downloads/report copy.pdf", b"same")
        self.at("Downloads/Screenshot 2026-01-02 at 10.00.00.png", b"s")
        self.at("Documents/budget.xlsx", b"x")
        self.at("Music/song.mp3", b"m")
        self.at("Downloads/thing.qqq", b"q")
        app = self.swept()
        got = {app.items[int(k)].name: c for k, c in app.results.items()}
        self.assertEqual(got, {"report copy.pdf": "Documents", "report.pdf": "Duplicates",
                               "Screenshot 2026-01-02 at 10.00.00.png": "Screenshots", "budget.xlsx": "Spreadsheets",
                               "song.mp3": "Audio", "thing.qqq": "Other"})

    def test_F3_no_claude_through_a_whole_run(self):
        self.at("Desktop/a.png")
        app = self.swept()
        app.apply(str(self.dest))
        self.assertEqual(app.phase, "applied")
        self.assertEqual(engine.missing_setup(FILES) if sys.version_info >= (3, 11) else "", "")

    def test_F4_sweep_reads_only_the_top_of_the_six_folders(self):
        for rel in ("Desktop/a.pdf", "Downloads/b.zip", "Documents/c.docx", "Pictures/d.jpg", f"{VIDEOS}/e.mov", "Music/f.mp3",
                    "Downloads/Old/deep.pdf", "Library/Preferences/x.pdf", "Applications/Fake.app/Contents/y.pdf",
                    "Pictures/Photos Library.photoslibrary/z.jpg", "Desktop/.secret.txt", "loose-in-home.pdf",
                    "Documents/project/.git/HEAD", "Documents/project/main.py"):
            self.at(rel)
        app = self.app()
        self.go(app, app.sweep)
        self.assertEqual(sorted(i.name for i in app.items), ["a.pdf", "b.zip", "c.docx", "d.jpg", "e.mov", "f.mp3"])
        self.assertEqual([Path(s).name for s in app.sources], list(engine.SWEEP))

    def test_F5_a_code_project_is_refused_in_folder_mode_and_skipped_by_the_sweep(self):
        self.at("Documents/.git/HEAD")
        self.at("Documents/notes.txt")
        self.at("Desktop/a.pdf")
        app = self.app()
        with self.assertRaises(engine.Refused):
            app.scan(str(self.home / "Documents"))
        self.go(app, app.sweep)
        self.assertEqual([i.name for i in app.items], ["a.pdf"])
        self.assertIn("Documents", [n for n, _ in app.left_folders])

    def test_F6_files_move_only_into_the_destination_and_nothing_is_lost(self):
        self.at("Desktop/notes.txt", b"one")
        self.at("Downloads/notes.txt", b"two")
        (self.dest / "Documents").mkdir()
        (self.dest / "Documents" / "notes.txt").write_bytes(b"already there")
        before = self.everything()
        app = self.swept()
        app.apply(str(self.dest))
        self.assertEqual(sorted(p.name for p in (self.dest / "Documents").iterdir()), ["notes (2).txt", "notes (3).txt", "notes.txt"])
        self.assertEqual(list((self.home / "Desktop").iterdir()), [])
        self.assertEqual(sorted(before), sorted(self.everything()))  # same contents, none lost

    def test_F7_another_drive_never_moves(self):
        other = other_drive_folder()
        self.addCleanup(shutil.rmtree, other, True)
        if os.lstat(other).st_dev == os.lstat(self.dir).st_dev:
            self.skipTest("needs two drives")
        # The Mac's own temporary folder is the second drive here; let the folder check accept it for this test.
        mock.patch("engine._SYSTEM", tuple(p for p in engine._SYSTEM if p != "/private/var/folders")).start()
        (other / "x.pdf").write_bytes(b"x")
        app = engine.App(FILES, home=self.home)
        app.set_dest(str(other))
        with self.assertRaises(engine.Refused):
            app.scan(str(self.home / "Desktop"))
        self.at("Desktop/a.pdf")
        with self.assertRaises(engine.Refused):
            app.sweep()  # every folder is on the other drive
        item = engine.scan(other, FILES).items[0]
        item.category = "Documents"
        with self.assertRaises(engine.Refused):
            engine.apply(self.dest, [item], FILES, [other])
        self.assertTrue((other / "x.pdf").exists())

    def test_F8_apply_needs_the_confirmed_destination_and_sweep_needs_one(self):
        self.at("Desktop/a.pdf")
        with self.assertRaises(engine.Conflict):
            self.app(dest=False).sweep()
        app = self.swept()
        for wrong in (None, "", str(self.dir), str(self.dest) + "/"):
            with self.assertRaises(engine.Conflict):
                app.apply(wrong)
        self.assertTrue((self.home / "Desktop" / "a.pdf").exists())
        app.apply(str(self.dest))
        self.assertTrue((self.dest / "Documents" / "a.pdf").exists())

    def test_F9_undo_puts_each_file_back_in_its_own_folder_after_a_restart(self):
        self.at("Desktop/a.pdf", b"A")
        self.at("Downloads/a.pdf", b"B")
        self.at("Music/c.mp3", b"C")
        before = self.everything()
        self.swept().apply(str(self.dest))
        later = self.app()  # a new app, as after a restart
        self.assertEqual(later.pending, 3)
        later.undo()
        self.assertEqual(later.restored, 3)
        self.assertEqual(before, self.everything())
        self.assertFalse((self.dest / "Documents").exists())

    def test_F9_folder_mode_with_the_same_folder_writes_photo_sorter_style_logs(self):
        folder = self.home / "Desktop"
        self.at("Desktop/a.pdf")
        app = engine.App(FILES, home=self.home)
        self.go(app, lambda: app.scan(str(folder)))
        app.sort()
        app.apply(str(folder))
        self.assertTrue((folder / "Documents" / "a.pdf").exists())
        log = next((folder / engine.RUNS).glob("*.jsonl"))
        self.assertNotIn('"from"', log.read_text())
        app.undo()
        self.assertTrue((folder / "a.pdf").exists())

    def forge(self, **change):
        self.at("Desktop/a.pdf", b"A")
        self.swept().apply(str(self.dest))
        log = next((self.dest / engine.RUNS).glob("*.jsonl"))
        row = json.loads(log.read_text().splitlines()[0])
        row.update(change)
        log.write_text(json.dumps(row) + "\n")
        return log

    def test_F10_forged_from_folders_move_nothing(self):
        outside = self.dir / "outside"
        outside.mkdir()
        os.symlink(outside, self.dir / "sneaky")
        other = other_drive_folder()
        self.addCleanup(shutil.rmtree, other, True)
        mock.patch("engine._SYSTEM", tuple(p for p in engine._SYSTEM if p != "/private/var/folders")).start()
        bad_ones = ["Desktop", str(self.dir / "sneaky"), "/System/Library", str(Path.home() / ("AppData" if engine.WINDOWS else "Library")), 7,
                    str(self.home / "Desktop") + "/../Desktop"]
        if os.lstat(other).st_dev != os.lstat(self.dir).st_dev:
            bad_ones.append(str(other))
        for bad in bad_ones:
            with self.subTest(bad=bad):
                for p in (self.dest / engine.RUNS).glob("*") if (self.dest / engine.RUNS).exists() else ():
                    p.unlink()
                for p in self.dest.rglob("*.pdf"):
                    p.unlink()
                for p in (self.home / "Desktop").glob("*.pdf"):
                    p.unlink()
                self.forge(**{"from": bad})
                before = self.everything()
                self.assertEqual(engine.pending_undo(self.dest, FILES), 0)
                with self.assertRaises(engine.Refused):
                    engine.undo(self.dest, FILES)
                self.assertEqual(before, self.everything())

    @unittest.skipIf(sys.platform == "win32", "chmod 0o666/0o777 can't open run history to others on Windows; there it is checked by owner SID")
    def test_F10_run_history_others_can_write_is_not_trusted(self):
        self.at("Desktop/a.pdf", b"A")
        self.swept().apply(str(self.dest))
        log = next((self.dest / engine.RUNS).glob("*.jsonl"))
        os.chmod(log, 0o666)
        self.assertEqual(engine.pending_undo(self.dest, FILES), 0)
        os.chmod(log, 0o600)
        os.chmod(self.dest / engine.RUNS, 0o777)
        with self.assertRaises(engine.Refused):
            engine.undo(self.dest, FILES)
        os.chmod(self.dest / engine.RUNS, 0o755)
        self.assertEqual(engine.undo(self.dest, FILES).restored, 1)

    def test_F10_photo_sorter_history_with_a_forged_from_moves_nothing(self):
        self.put("a.jpg", b"A")
        items = engine.scan(self.dir, TOOL).items
        items[0].category = "Food"
        engine.apply(self.dir, items, TOOL)
        log = next((self.dir / engine.RUNS).glob("*.jsonl"))
        row = json.loads(log.read_text())
        row["from"] = "/System/Library"
        log.write_text(json.dumps(row) + "\n")
        with self.assertRaises(engine.Refused):
            engine.undo(self.dir, TOOL)
        self.assertTrue((self.dir / "Food" / "a.jpg").exists())

    def test_F10_a_from_folder_that_has_gone_keeps_the_run_and_leaves_its_files(self):
        self.at("Desktop/a.pdf", b"A")
        self.at("Music/b.mp3", b"B")
        self.swept().apply(str(self.dest))
        (self.home / "Music").rename(self.home / "Music old")
        self.assertEqual(engine.pending_undo(self.dest, FILES), 2)  # the run is still the one on offer
        result = engine.undo(self.dest, FILES)
        self.assertEqual(result.restored, 1)
        self.assertEqual([x.name for x in result.left], ["b.mp3"])
        self.assertTrue((self.home / "Desktop" / "a.pdf").exists())
        self.assertTrue((self.dest / "Audio" / "b.mp3").exists())

    @unittest.skipIf(sys.platform == "win32", "Windows can't create the name Icon\\r (a carriage return) and has no os.chflags")
    def test_F1_files_macos_marks_hidden_are_never_read(self):
        self.at("Downloads/report.pdf")
        icon = self.at("Downloads/Icon\r")
        os.chflags(icon, stat.UF_HIDDEN)
        self.assertEqual([i.name for i in engine.scan(self.home / "Downloads", FILES).items], ["report.pdf"])

    def test_F4_a_top_folder_that_is_a_link_is_left_out(self):
        (self.home / "Pictures").rmdir()
        os.symlink(self.home / "Desktop", self.home / "Pictures")
        self.at("Desktop/a.pdf")
        app = self.app()
        self.go(app, app.sweep)
        self.assertNotIn("Pictures", [Path(s).name for s in app.sources])
        self.assertIn("Pictures", [n for n, _ in app.left_folders])
        self.assertEqual([i.name for i in app.items], ["a.pdf"])

    @unittest.skipIf(sys.platform == "win32", "chmod 0o311 can't make a folder unlistable on Windows")
    def test_F4_a_folder_this_mac_wont_list_is_left_out_and_the_rest_still_sorts(self):
        self.at("Desktop/a.pdf")
        self.at("Documents/b.pdf")
        os.chmod(self.home / "Documents", 0o311)  # can enter, can't list
        self.addCleanup(os.chmod, self.home / "Documents", 0o755)
        app = self.app()
        self.go(app, app.sweep)
        self.assertEqual([i.name for i in app.items], ["a.pdf"])
        self.assertIn("Documents", [n for n, _ in app.left_folders])

    @unittest.skipIf(sys.platform == "win32", "Windows has no os.mkfifo")
    def test_F10_a_pipe_posing_as_run_history_cannot_hang_undo(self):
        runs = self.dest / engine.RUNS
        runs.mkdir(mode=0o755)
        os.mkfifo(runs / "20990101T000000000000-ffff.jsonl", 0o600)
        done = []
        t = threading.Thread(target=lambda: done.append(engine.pending_undo(self.dest, FILES)), daemon=True)
        t.start(); t.join(5)
        self.assertEqual(done, [0])

    def test_a_destination_that_was_deleted_is_a_plain_refusal(self):
        self.at("Desktop/a.pdf")
        app = self.app()
        self.dest.rmdir()
        with self.assertRaises(engine.Refused):
            app.scan(str(self.home / "Desktop"))


class FilesPreviews(Base):
    @unittest.skipUnless(sys.platform == "win32", "Windows-only: the Mac makes Quick Look previews")
    def test_F3_windows_makes_no_previews_and_raises_nothing(self):
        mock.patch("engine.PREVIEWS", self.dir / "previews").start()
        self.addCleanup(mock.patch.stopall)
        src = self.dir / "src"
        src.mkdir()
        (src / "notes.txt").write_text("notes")
        items = engine.scan(src, FILES).items
        self.assertEqual(engine.make_previews([src], items, {}), {})
        self.assertFalse((self.dir / "previews").exists())

    @unittest.skipIf(sys.platform == "win32", "no Quick Look on Windows by design: File Sorter shows type badges instead")
    def test_F3_quick_look_previews_stay_in_the_private_folder_numbered_not_named(self):
        mock.patch("engine.PREVIEWS", self.dir / "previews").start()
        self.addCleanup(mock.patch.stopall)
        src = self.dir / "src"
        src.mkdir()
        (src / "-rf $(touch p).txt").write_text("hello")
        (src / "notes.txt").write_text("notes")
        items = engine.scan(src, FILES).items
        got = engine.make_previews([src], items, {})
        self.assertEqual(sorted(got), [0, 1])
        self.assertEqual(sorted(p.name for p in (self.dir / "previews").iterdir()), ["0.png", "1.png"])
        self.assertFalse((src / "p").exists())
        self.assertEqual(sorted(p.name for p in src.iterdir()), ["-rf $(touch p).txt", "notes.txt"])
        engine.clear_previews()
        self.assertEqual(list((self.dir / "previews").iterdir()), [])

    def test_F3_file_sorter_never_clears_photo_sorter_thumbnails(self):
        mock.patch("engine.PREVIEWS", self.dir / "previews").start()
        mock.patch("engine.THUMBS", self.dir / "thumbs").start()
        self.addCleanup(mock.patch.stopall)
        engine._private_dir(engine.THUMBS)
        (self.dir / "thumbs" / "0.jpg").write_bytes(b"photo sorter thumbnail")
        engine._private_dir(engine.PREVIEWS)
        (self.dir / "previews" / "0.png").write_bytes(b"file sorter preview")
        (self.dir / "previews" / "tmpleft").mkdir()
        (self.dir / "previews" / "tmpleft" / "x.png").write_bytes(b"left by a stopped run")
        app = engine.App(FILES)
        app.restart()
        self.assertTrue((self.dir / "thumbs" / "0.jpg").exists())
        self.assertEqual(list((self.dir / "previews").iterdir()), [])


class PhotosSweep(FilesBase):
    """The whole-computer button and a chosen destination, in Photo Sorter."""
    def photo_app(self):
        app = engine.App(TOOL, home=self.home)
        app.set_dest(str(self.dest))
        return app

    def test_sweep_takes_only_loose_photos_and_videos_and_undo_puts_them_back(self):
        mock.patch("engine.make_thumbs", lambda folders, items, tick=None: ({n: Path("x.jpg") for n in range(len(items))}, [])).start()
        mock.patch("engine.sort_all", lambda thumbs, tool, model, on_batch=None: on_batch({i: "Food" for i in thumbs})).start()
        for rel in ("Desktop/a.jpg", "Pictures/b.png", f"{VIDEOS}/c.mov", "Downloads/notes.txt", "Pictures/Old/d.jpg",
                    "Pictures/Photos Library.photoslibrary/e.jpg", "Desktop/.f.jpg"):
            self.at(rel)
        before = self.everything()
        app = self.photo_app()
        self.go(app, app.sweep)
        self.assertEqual(sorted(i.name for i in app.items), ["a.jpg", "b.png", "c.mov"])
        self.go(app, app.sort)
        with self.assertRaises(engine.Conflict):
            app.apply(None)  # the confirm step names the destination
        app.apply(str(self.dest))
        self.assertEqual(sorted(p.name for p in (self.dest / "Food").iterdir()), ["a.jpg", "b.png", "c.mov"])
        later = self.photo_app()
        later.undo()
        self.assertEqual(before, self.everything())

    def test_thumbnails_come_from_each_photos_own_folder(self):
        seen = []

        def thumb(src, index, is_video):
            seen.append(Path(src))
            out = self.dir / f"t{index}.jpg"
            out.write_bytes(b"\xff\xd8thumb")
            return out
        mock.patch("engine.make_thumb", thumb).start()
        mock.patch("engine.make_thumbs", REAL_MAKE_THUMBS).start()
        self.at("Desktop/a.jpg")
        self.at("Pictures/b.png")
        app = self.photo_app()
        self.go(app, app.sweep)
        self.assertEqual(sorted(seen), sorted([self.home / "Desktop" / "a.jpg", self.home / "Pictures" / "b.png"]))
        self.assertEqual(app.skipped, [])

    def test_one_folder_into_a_chosen_destination_needs_the_confirm(self):
        mock.patch("engine.make_thumbs", lambda folders, items, tick=None: ({n: Path("x.jpg") for n in range(len(items))}, [])).start()
        mock.patch("engine.sort_all", lambda thumbs, tool, model, on_batch=None: on_batch({i: "Food" for i in thumbs})).start()
        self.at("Desktop/a.jpg")
        app = self.photo_app()
        self.go(app, lambda: app.scan(str(self.home / "Desktop")))
        self.go(app, app.sort)
        with self.assertRaises(engine.Conflict):
            app.apply(None)
        self.assertTrue((self.home / "Desktop" / "a.jpg").exists())
        app.apply(str(self.dest))
        self.assertTrue((self.dest / "Food" / "a.jpg").exists())

    def test_folder_mode_without_a_destination_still_needs_no_confirm(self):
        mock.patch("engine.make_thumbs", lambda folders, items, tick=None: ({n: Path("x.jpg") for n in range(len(items))}, [])).start()
        mock.patch("engine.sort_all", lambda thumbs, tool, model, on_batch=None: on_batch({i: "Nature" for i in thumbs})).start()
        self.at("Desktop/a.jpg")
        app = engine.App(TOOL, home=self.home)
        self.go(app, lambda: app.scan(str(self.home / "Desktop")))
        self.go(app, app.sort)
        app.apply(None)
        self.assertTrue((self.home / "Desktop" / "Nature" / "a.jpg").exists())


class FilesServer(ServerBase):
    tool = FILES

    def test_F8_whole_flow_through_the_page_routes(self):
        mock.patch("engine.make_previews", return_value={}).start()
        home = self.dir / "home"
        (home / "Desktop").mkdir(parents=True)
        (home / "Desktop" / "a.pdf").write_bytes(b"A")
        self.app.home = home
        dest = self.dir / "Tidy"
        dest.mkdir()
        self.assertEqual(self.call("POST", "/api/sweep", {})[0].status, 409)
        self.assertEqual(self.call("POST", "/api/dest", {"path": str(dest)})[0].status, 200)
        self.assertEqual(self.call("POST", "/api/sweep", {}, token=False)[0].status, 403)
        self.assertEqual(self.call("POST", "/api/sweep", {})[0].status, 200)
        s = self.wait_for("scanned")
        self.assertEqual((s["mode"], s["names"], s["dest"]), ("sweep", ["a.pdf"], str(dest)))
        self.call("POST", "/api/sort", {})
        s = self.wait_for("review")
        self.assertEqual(s["results"], {"0": "Documents"})
        self.assertEqual(self.call("POST", "/api/apply", {})[0].status, 409)
        self.assertEqual(self.call("POST", "/api/apply", {"path": str(dest)})[0].status, 200)
        self.assertTrue((dest / "Documents" / "a.pdf").exists())
        self.call("POST", "/api/undo", {})
        self.assertTrue((home / "Desktop" / "a.pdf").exists())
        r, body = self.call("GET", "/")
        self.assertIn(b"Find files all over my computer", body)


if __name__ == "__main__":
    unittest.main()


class WindowsLogic(Base):
    """Windows rules that are plain logic, so they also run on a Mac."""
    HOME = "C:\\Users\\me"

    def refusal(self, path, env=None):
        return engine.windows_refusal(path, env={} if env is None else env, home=self.HOME)

    def test_W1_drive_roots_users_and_home_are_refused(self):
        for path in ("C:\\", "D:\\", "c:", "\\\\server\\share", "C:\\Users", "c:\\USERS\\", "C:\\Users\\me", "C:\\users\\ME"):
            self.assertIn("whole disk", self.refusal(path) or "", path)

    def test_W2_system_folders_and_everything_under_them_are_refused(self):
        for path in ("C:\\Windows", "c:\\windows\\System32", "C:\\Program Files\\App", "C:\\PROGRAM FILES (X86)",
                     "C:\\ProgramData\\x", "C:\\Users\\me\\AppData", "c:\\users\\me\\appdata\\Local\\Temp"):
            self.assertIn("System folders", self.refusal(path) or "", path)
        self.assertIn("System folders", self.refusal("E:\\Win\\Logs", {"SystemRoot": "E:\\Win"}) or "")

    def test_W3_ordinary_folders_pass(self):
        for path in ("C:\\Users\\me\\Pictures", "C:\\Users\\me\\Pictures\\Trip", "D:\\Photos", "C:\\Program Files Old",
                     "C:\\Users\\me\\AppDataBackup"):
            self.assertIsNone(self.refusal(path), path)

    def test_W5_extended_and_network_paths_are_refused(self):
        for path in ("\\\\?\\C:\\Program Files\\X", "\\\\localhost\\C$\\ProgramData\\X", "\\\\server\\share\\X"):
            self.assertIn("Network and special", self.refusal(path) or "", path)
        self.assertIsNone(self.refusal("C:\\Users\\me\\Pictures\\Mess"))

    def test_W4_a_reparse_point_anywhere_in_the_path_is_refused(self):
        bad = self.dir / "link"
        real_lstat = os.lstat

        def lstat(p):
            if Path(p) == bad:
                return mock.Mock(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return real_lstat(p)
        with mock.patch("engine.os.lstat", side_effect=lstat):
            with self.assertRaises(OSError):
                engine._check_no_reparse(bad / "Food" / "a.jpg")
            engine._check_no_reparse(self.dir / "plain" / "a.jpg")

    def test_W5_the_windows_move_checks_both_paths_before_moving(self):
        self.put("a.jpg")
        with mock.patch("engine.WINDOWS", True), \
             mock.patch("engine._check_no_reparse", side_effect=OSError(errno.ELOOP, "link")) as check, \
             mock.patch("engine.os.rename") as rename:
            with self.assertRaises(OSError):
                engine.rename_excl(self.dir / "a.jpg", self.dir / "b.jpg")
        rename.assert_not_called()
        check.assert_called_once()

    def test_W6_claude_on_windows_gets_what_it_needs_to_start_and_no_api_key(self):
        need = {"USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "TEMP", "TMP", "PATHEXT", "COMSPEC",
                "HOMEDRIVE", "HOMEPATH"}
        self.assertEqual(set(engine._WINDOWS_ENV), need)
        self.assertFalse([k for k in (*engine.CLAUDE_ENV, *engine._WINDOWS_ENV)
                          if "KEY" in k or "TOKEN" in k or "ANTHROPIC" in k or "BASE_URL" in k])

    def test_W7_pages_take_a_folder_name_from_windows_and_mac_paths(self):
        for page in ("page.html", "file_page.html"):
            self.assertIn('const lastPart = p => (p || "").replace(/[\\/\\\\]+$/, "").split(/[\\/\\\\]/).pop();',
                          (HERE / page).read_text(), page)

    def test_W8_windows_names_with_a_separator_drive_or_stream_are_not_bare(self):
        with mock.patch("engine.WINDOWS", True):
            for name in ("..\\x.jpg", "a\\b.jpg", "C:x.jpg", "a.jpg:stream"):
                self.assertFalse(engine._is_bare(name), name)
            self.assertTrue(engine._is_bare("a.jpg"))


    def test_W11_run_history_is_yours_on_windows_only_when_the_owner_lookup_says_so(self):
        st = os.lstat(self.dir)
        with mock.patch("engine.WINDOWS", True):
            with mock.patch("engine._owner_is_me", return_value=False):
                self.assertFalse(engine._yours(st, self.dir))
            with mock.patch("engine._owner_is_me", return_value=True):
                self.assertTrue(engine._yours(st, self.dir))

    def test_W12_a_failed_or_mismatched_owner_lookup_is_not_yours(self):
        failed = mock.Mock()
        failed.windll.advapi32.GetNamedSecurityInfoW.return_value = 5  # ERROR_ACCESS_DENIED
        mismatch = mock.Mock()
        mismatch.windll.advapi32.GetNamedSecurityInfoW.return_value = 0
        mismatch.windll.advapi32.EqualSid.return_value = 0
        broken = mock.Mock()
        broken.windll = mock.PropertyMock(side_effect=AttributeError)
        del broken.windll
        for fake in (failed, mismatch, broken):
            with mock.patch("engine.ctypes", fake):
                self.assertIs(engine._owner_is_me(self.dir), False)


@unittest.skipUnless(sys.platform == "win32", "the real Windows move runs on the Windows CI job")
class WindowsMoves(Base):
    def test_W13_a_folder_you_just_made_is_yours(self):
        made = self.dir / "mine"
        made.mkdir()
        self.assertTrue(engine._owner_is_me(made))
        self.assertTrue(engine._yours(os.lstat(made), made))

    def test_W9_a_move_never_overwrites(self):
        a, b = self.put("a.jpg"), self.put("b.jpg")
        with self.assertRaises(FileExistsError):
            engine.rename_excl(a, b)
        self.assertEqual((a.read_bytes(), b.read_bytes()), (b"a.jpg", b"b.jpg"))

    def test_W10_a_move_through_a_junction_is_refused(self):
        (self.dir / "real").mkdir()
        self.put("a.jpg")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.dir / "jn"), str(self.dir / "real")],
                              capture_output=True)
        self.assertEqual(made.returncode, 0, made.stderr)
        with self.assertRaises(OSError):
            engine.rename_excl(self.dir / "a.jpg", self.dir / "jn" / "a.jpg")
        self.assertTrue((self.dir / "a.jpg").exists())
        self.assertEqual(list((self.dir / "real").iterdir()), [])
