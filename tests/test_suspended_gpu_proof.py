from __future__ import annotations

import unittest
from runpy import run_path
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROOF_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "issue7-suspended-gpu-proof"
)
LIMINE_HELPER = PROOF_SCRIPT.with_name("issue7-limine-parameter")
LIMINE = run_path(str(LIMINE_HELPER))
LimineProofError = LIMINE["LimineProofError"]
classify_config = LIMINE["classify_config"]
patch_config = LIMINE["patch_config"]
LIMINE_GLOBALS = patch_config.__globals__

LIMINE_CONFIG = b"""/+Omarchy
  //linux-t2
  comment: kernel-id=linux-t2
  protocol: efi
  path: boot():/EFI/Linux/omarchy_linux-t2.efi
  cmdline: quiet splash apple_gmux.force_igd=1

     //Snapshots
     ////linux-t2
     comment: kernel-id=linux-t2
     protocol: efi
     cmdline: quiet splash rootflags=subvol=/@/.snapshots/1/snapshot
"""


class SuspendedGpuProofInstructionsTests(unittest.TestCase):
    def test_boot_instructions_do_not_require_a_touch_bar_function_key(self) -> None:
        instructions = PROOF_SCRIPT.read_text(encoding="utf-8")
        forced_boot = instructions.split("  awaiting_forced_boot)", 1)[1].split(
            "  awaiting_igpu_login)",
            1,
        )[0]

        self.assertNotIn("Press F10", instructions)
        self.assertIn('sudo "$LIMINE_HELPER" prepare', instructions)
        self.assertIn("restore_limine_config", forced_boot)
        self.assertLess(
            forced_boot.index("restore_limine_config"),
            forced_boot.index("write_dynamic_igpu_environment"),
        )


class LimineProofHelperTests(unittest.TestCase):
    def test_only_top_level_linux_t2_cmdline_receives_forced_parameter(self) -> None:
        patched = patch_config(LIMINE_CONFIG)

        self.assertIn(
            b"cmdline: quiet splash apple_gmux.force_igd=1 amdgpu.runpm=1\n",
            patched,
        )
        self.assertIn(
            b"cmdline: quiet splash rootflags=subvol=/@/.snapshots/1/snapshot\n",
            patched,
        )
        self.assertEqual(patched.count(b"amdgpu.runpm=1"), 1)
        self.assertEqual(classify_config(patched, LIMINE_CONFIG), "prepared")
        self.assertEqual(classify_config(LIMINE_CONFIG, LIMINE_CONFIG), "restored")

    def test_ambiguous_top_level_kernel_entries_are_rejected(self) -> None:
        ambiguous = LIMINE_CONFIG + b"""
  //linux-t2-copy
  comment: kernel-id=linux-t2
  cmdline: quiet splash
"""

        with self.assertRaises(LimineProofError):
            patch_config(ambiguous)

    def test_unrelated_active_config_is_classified_as_mismatch(self) -> None:
        unrelated = LIMINE_CONFIG.replace(b"quiet splash", b"debug", 1)

        self.assertEqual(classify_config(unrelated, LIMINE_CONFIG), "mismatch")

    def test_prepare_restore_verify_and_cleanup_are_idempotent(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "limine.conf"
            backup = root / "state" / "limine.backup"
            config.write_bytes(LIMINE_CONFIG)

            def replace_config(value: bytes) -> None:
                config.write_bytes(value)

            with patch.dict(
                LIMINE_GLOBALS,
                {
                    "CONFIG_PATH": config,
                    "BACKUP_PATH": backup,
                    "_replace_config": replace_config,
                },
            ):
                first = LIMINE["prepare"]()
                second = LIMINE["prepare"]()
                self.assertTrue(first["prepared"])
                self.assertTrue(second["prepared"])
                self.assertEqual(backup.read_bytes(), LIMINE_CONFIG)
                self.assertEqual(config.read_bytes().count(b"amdgpu.runpm=1"), 1)

                restored = LIMINE["restore"]()
                verified = LIMINE["verify"]()
                self.assertTrue(restored["restored"])
                self.assertTrue(verified["restored"])
                self.assertEqual(config.read_bytes(), LIMINE_CONFIG)

                cleaned = LIMINE["cleanup"]()
                self.assertTrue(cleaned["backup_removed"])
                self.assertFalse(backup.exists())

    def test_restore_refuses_to_overwrite_an_unrelated_active_config(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = root / "limine.conf"
            backup = root / "limine.backup"
            config.write_bytes(LIMINE_CONFIG.replace(b"quiet splash", b"debug", 1))
            backup.write_bytes(LIMINE_CONFIG)

            with patch.dict(
                LIMINE_GLOBALS,
                {
                    "CONFIG_PATH": config,
                    "BACKUP_PATH": backup,
                },
            ):
                with self.assertRaises(LimineProofError):
                    LIMINE["restore"]()

            self.assertTrue(backup.exists())
            self.assertIn(b"cmdline: debug", config.read_bytes())

    def test_config_write_restores_every_original_mount_option(self) -> None:
        with TemporaryDirectory() as raw:
            config = Path(raw) / "limine.conf"
            config.write_bytes(LIMINE_CONFIG)
            original_options = ("ro", "nosuid", "nodev", "relatime")
            remounts: list[tuple[str, ...]] = []

            def remount(options: tuple[str, ...]) -> None:
                remounts.append(options)

            with patch.dict(
                LIMINE_GLOBALS,
                {
                    "CONFIG_PATH": config,
                    "_mount_info": lambda: ("vfat", original_options),
                    "_remount": remount,
                },
            ):
                LIMINE["_replace_config"](patch_config(LIMINE_CONFIG))

            self.assertEqual(
                remounts,
                [
                    ("rw", "nosuid", "nodev", "relatime"),
                    original_options,
                ],
            )


if __name__ == "__main__":
    unittest.main()
