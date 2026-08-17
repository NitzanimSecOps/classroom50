"""Pins the generated challenge so it cannot drift when regenerated.

The exercise only works if the three-stage funnel narrows to exactly one file.
These tests assert the discrepancy budget and then walk the funnel end to end,
so a change to the generator (or to the exercise 1-4 classes it drives) that
breaks the intended solution fails here instead of in a classroom.
"""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from solutions.suite_3_1_0.exercise_3.inventory_db import InventoryDB
from solutions.suite_3_1_0.exercise_3.inventory_record import InventoryRecord
from solutions.suite_3_1_0.exercise_4.integrity_checker import IntegrityChecker
from solutions.suite_3_1_0.exercise_5 import generate_challenge
from solutions.suite_3_1_0.exercise_5.generate_challenge import (
    ARCHIVED_RECORDS,
    BASELINE_RECORDS,
    BENIGN_REWRITES,
    CHECKSUMS_NAME,
    FLAG,
    NEW_RECORDS,
    PAYLOAD_RECORDS,
    RECORD_SIZE,
    WAREHOUSE_DIRNAME,
    build_challenge,
    build_zip,
)

EXPECTED_CHANGED = BENIGN_REWRITES + PAYLOAD_RECORDS + 1  # records + payload + the log
EXPECTED_TOTAL = EXPECTED_CHANGED + NEW_RECORDS + ARCHIVED_RECORDS


class TestChallengeGeneration(unittest.TestCase):
    """Everything here shares one expensive build of the 1000-record warehouse."""

    @classmethod
    def setUpClass(cls):
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.stage = Path(cls._temp_dir.name) / "challenge"
        cls.stage.mkdir()
        cls.summary = build_challenge(cls.stage)

        cls.root = cls.stage / WAREHOUSE_DIRNAME
        cls.checksums = cls.stage / CHECKSUMS_NAME
        cls.db = InventoryDB(cls.root / generate_challenge.DB_RELPATH)

    @classmethod
    def tearDownClass(cls):
        cls._temp_dir.cleanup()

    def test_baseline_is_outside_the_hashed_root(self):
        # A checksums file inside the root would hash itself and show up as NEW.
        self.assertFalse(
            self.checksums.is_relative_to(self.root),
            "checksums.txt must live outside the warehouse root",
        )

    def test_record_count(self):
        on_disk = list(self.db.path.iterdir())
        self.assertEqual(
            len(on_disk),
            BASELINE_RECORDS - ARCHIVED_RECORDS + NEW_RECORDS,
            "record count on disk does not match the baseline plus drift",
        )

    def test_discrepancy_budget(self):
        verdicts = self.summary["verdicts"]

        self.assertEqual(verdicts["CHANGED"], EXPECTED_CHANGED, "wrong number of CHANGED files")
        self.assertEqual(verdicts["NEW"], NEW_RECORDS, "wrong number of NEW files")
        self.assertEqual(verdicts["MISSING"], ARCHIVED_RECORDS, "wrong number of MISSING files")
        self.assertEqual(
            sum(verdicts.values()), EXPECTED_TOTAL, "total discrepancy count drifted"
        )

    def test_discrepancy_count_is_too_large_to_eyeball(self):
        # The exercise's premise: enough noise that students must script the triage.
        self.assertGreater(
            sum(self.summary["verdicts"].values()), 10, "challenge is trivially small"
        )

    def test_payload_is_changed_not_new(self):
        # A NEW payload would be findable by sorting the verdict column alone.
        verdict = self._verdict_for(self.summary["payload_file"])
        self.assertEqual(verdict, "CHANGED", "the payload must overwrite an existing record")

    def test_funnel_narrows_to_exactly_one_file(self):
        """Stage 2 and 3 of the intended solution, run for real."""
        survivors = []
        for relpath, verdict in self.summary["discrepancies"].items():
            if verdict == "MISSING":
                continue  # nothing left on disk to inspect
            path = self.root / relpath
            if path.suffix != ".invr":
                continue
            if InventoryDB.detect_file_type(path) != "INVR":
                survivors.append(path.name)

        self.assertEqual(
            survivors,
            [self.summary["payload_file"]],
            "the file-type filter must leave exactly the payload",
        )

    def test_benign_changed_records_stay_valid(self):
        """Hashing alone cannot separate these from the payload -- that is the point."""
        for name in self.summary["rewritten"]:
            path = self.db.path / name
            data = path.read_bytes()

            self.assertEqual(len(data), RECORD_SIZE, f"{name} is no longer 36 bytes")
            self.assertEqual(
                InventoryDB.detect_file_type(path), "INVR", f"{name} lost its INVR magic"
            )
            record = self.db.open_record(path)
            self.assertIsInstance(record, InventoryRecord, f"{name} no longer parses")
            self.assertGreater(record.quantity, 0, f"{name} parsed to a nonsense quantity")

    def test_payload_lies_about_its_type(self):
        path = self.db.path / self.summary["payload_file"]

        self.assertEqual(
            InventoryDB.detect_file_type(path),
            "UNKNOWN",
            "the payload must not be detected as any known type",
        )
        self.assertNotEqual(
            path.stat().st_size, RECORD_SIZE, "size must be an independent second tell"
        )
        self.assertIn(
            FLAG.encode("ascii"),
            __import__("base64").b64decode(path.read_bytes().split(b"CFG1", 1)[1].strip()),
            "the payload's base64 blob must decode to the flag",
        )

    def test_skus_are_not_substrings_of_each_other(self):
        """find_record/remove_record match with `sku in file.name`.

        Overlapping SKUs would make removals delete the wrong record, so the
        fixed-width scheme is load-bearing, not cosmetic.
        """
        skus = sorted({path.name.split("_")[0] for path in self.db.path.iterdir()})
        widths = {len(sku) for sku in skus}

        self.assertEqual(len(widths), 1, f"SKUs must be fixed width, saw widths {widths}")
        self.assertLessEqual(max(widths), 16, "SKUs longer than 16 chars are silently truncated")

    def test_record_filenames_are_unique(self):
        # Filenames are keyed to the second; a collision silently overwrites.
        names = [path.name for path in self.db.path.iterdir()]
        self.assertEqual(len(names), len(set(names)), "record filenames collided")

    def test_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as other:
            second = Path(other) / "challenge"
            second.mkdir()
            summary = build_challenge(second)

        self.assertEqual(
            summary["payload_file"],
            self.summary["payload_file"],
            "the seeded RNG must pick the same payload target every run",
        )
        self.assertEqual(
            summary["discrepancies"],
            self.summary["discrepancies"],
            "the same seed must produce the same discrepancy set",
        )

    def _verdict_for(self, filename: str) -> str | None:
        for relpath, verdict in self.summary["discrepancies"].items():
            if Path(relpath).name == filename:
                return verdict
        return None


class TestShippedZip(unittest.TestCase):
    """Checks the artifact students actually receive, not just the staging dir."""

    @classmethod
    def setUpClass(cls):
        cls._temp_dir = tempfile.TemporaryDirectory()
        cls.extracted = Path(cls._temp_dir.name) / "extracted"
        zip_path = Path(cls._temp_dir.name) / "challenge.zip"
        cls.summary = build_zip(zip_path)

        with zipfile.ZipFile(zip_path) as archive:
            cls.members = archive.namelist()
            archive.extractall(cls.extracted)

    @classmethod
    def tearDownClass(cls):
        cls._temp_dir.cleanup()

    def test_zip_contains_warehouse_and_baseline(self):
        self.assertIn(CHECKSUMS_NAME, self.members, "checksums.txt is missing from the zip")
        self.assertTrue(
            any(name.startswith(f"{WAREHOUSE_DIRNAME}/") for name in self.members),
            "the zip has no warehouse/ directory",
        )

    def test_baseline_uses_posix_separators(self):
        """The zip is built on any OS but always unpacked on Kali.

        IntegrityChecker keys on str(relative_to(root)), so a Windows-generated
        baseline full of backslashes would report every file MISSING plus NEW.
        """
        text = (self.extracted / CHECKSUMS_NAME).read_text(encoding="utf-8")
        self.assertNotIn("\\", text, "checksums.txt must use forward slashes")

    def test_extracted_zip_reproduces_the_budget(self):
        """The end-to-end student experience: verify() against the shipped baseline.

        The shipped baseline is POSIX-keyed for Kali. IntegrityChecker.compute
        keys on str(relative_to(root)), which is separator-native, so on Windows
        we translate the baseline back to os.sep first -- otherwise every path
        mismatches and the run reports all-MISSING plus all-NEW. On Linux, where
        this challenge is actually used, the translation is a no-op.
        """
        shipped = (self.extracted / CHECKSUMS_NAME).read_text(encoding="utf-8")
        localized = self.extracted / "checksums.local.txt"
        localized.write_text(shipped.replace("/", os.sep), encoding="utf-8")

        discrepancies = IntegrityChecker(self.extracted / WAREHOUSE_DIRNAME).verify(localized)
        verdicts = {"CHANGED": 0, "MISSING": 0, "NEW": 0}
        for verdict in discrepancies.values():
            verdicts[verdict] += 1

        self.assertEqual(verdicts["CHANGED"], EXPECTED_CHANGED, "wrong CHANGED count from the zip")
        self.assertEqual(verdicts["NEW"], NEW_RECORDS, "wrong NEW count from the zip")
        self.assertEqual(verdicts["MISSING"], ARCHIVED_RECORDS, "wrong MISSING count from the zip")

    def test_no_flag_in_plaintext_anywhere(self):
        """The flag must require decoding the blob, not `grep -r NITZANIM`."""
        needle = FLAG.encode("ascii")
        for path in self.extracted.rglob("*"):
            if path.is_file():
                self.assertNotIn(
                    needle, path.read_bytes(), f"{path.name} leaks the flag in plaintext"
                )


if __name__ == "__main__":
    unittest.main()
