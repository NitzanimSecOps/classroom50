import stat
import unittest
import tempfile
from pathlib import Path

from permission_auditor import PermissionAuditor


def rel(*parts: str) -> str:
    """Relative key exactly as audit() reports it (OS-native separator)."""
    return str(Path(*parts))


class TestPermissionAuditor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "warehouse"
        (self.root / "inventory").mkdir(parents=True)

        # Already safe: owner-writable, world-readable data. Nothing to fix.
        self.clean = self.root / "inventory" / "clean.invr"
        self.clean.write_bytes(b"INVR-data")
        self.clean.chmod(0o644)

        # Group-writable record: another account in the group can rewrite it.
        self.group_writable = self.root / "inventory" / "group_writable.invr"
        self.group_writable.write_bytes(b"INVR-data")
        self.group_writable.chmod(0o664)

        # World-writable manifest: literally anyone on the box can edit it.
        self.world_writable = self.root / "inventory" / "world_writable.csv"
        self.world_writable.write_bytes(b"sku,name")
        self.world_writable.chmod(0o646)

        # A data file carrying the execute bit (how a planted binary hides).
        self.executable = self.root / "inventory" / "planted.invr"
        self.executable.write_bytes(b"\x7fELF not really a record")
        self.executable.chmod(0o755)

        # A world-writable directory.
        self.loose_dir = self.root / "shipments"
        self.loose_dir.mkdir()
        self.loose_dir.chmod(0o777)

        self.auditor = PermissionAuditor(self.root)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_audit_flags_group_or_other_writable(self):
        report = self.auditor.audit()

        self.assertIn(
            rel("inventory", "group_writable.invr"), report,
            "audit() missed a group-writable record. If anyone but the owner can write a "
            "record file, they can alter inventory without owning it — audit() must report it.",
        )
        self.assertIn(
            rel("inventory", "world_writable.csv"), report,
            "audit() missed a world-writable file. World-writable means every account on the "
            "machine can overwrite it; that is the single most dangerous mode here.",
        )

    def test_audit_flags_executable_data_file(self):
        report = self.auditor.audit()

        self.assertIn(
            rel("inventory", "planted.invr"), report,
            "audit() missed an executable data file. A .invr is data, never a program; the "
            "execute bit on it is a red flag that audit() must surface.",
        )

    def test_audit_leaves_safe_file_alone(self):
        report = self.auditor.audit()

        self.assertNotIn(
            rel("inventory", "clean.invr"), report,
            "audit() flagged a 0o644 file. Owner-writable, world-readable data is already safe; "
            "flagging it would train students to 'fix' files that were never a problem.",
        )

    def test_harden_leaves_nothing_writable_by_group_or_other(self):
        self.auditor.harden()

        for path in self.root.rglob("*"):
            mode = path.stat().st_mode
            self.assertFalse(
                mode & (stat.S_IWGRP | stat.S_IWOTH),
                f"{path.relative_to(self.root)} is still writable by group or others after "
                "harden(). Any non-owner being able to write warehouse files lets an attacker "
                "silently overwrite a record or log — exactly the tampering the integrity "
                "checker exists to catch. After harden() only the owner may write.",
            )

    def test_harden_leaves_no_executable_data_files(self):
        self.auditor.harden()

        for path in self.root.rglob("*"):
            if path.is_dir():
                continue
            mode = path.stat().st_mode
            self.assertFalse(
                mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH),
                f"{path.relative_to(self.root)} is still executable after harden(). Warehouse "
                "files are data (records, manifests, logs) — never programs. Leaving the execute "
                "bit set means the OS is willing to run a planted binary that is posing as data.",
            )

    def test_harden_keeps_directories_traversable(self):
        self.auditor.harden()

        for path in self.root.rglob("*"):
            if not path.is_dir():
                continue
            mode = path.stat().st_mode
            self.assertTrue(
                mode & stat.S_IXUSR,
                f"Directory {path.relative_to(self.root)} lost its owner execute bit after "
                "harden(). On a directory 'execute' means 'may be entered', not 'may run' — "
                "strip it and the files inside become unreachable. Hardening must not break "
                "access to the warehouse it is protecting.",
            )

    def test_harden_is_complete(self):
        self.auditor.harden()

        self.assertEqual(
            self.auditor.audit(), {},
            "After harden(), a fresh audit() still reports problems. harden() must fix every "
            "issue audit() can find, so that re-auditing a hardened warehouse comes back clean.",
        )


if __name__ == "__main__":
    unittest.main()
