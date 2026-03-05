import tempfile
import unittest
import zipfile
from pathlib import Path

from diagnostics_core import diagnose_zip


class DiagnoseZipTests(unittest.TestCase):
    def _zip_with(self, members, name_suffix=".zip"):
        tmp = tempfile.NamedTemporaryFile(suffix=name_suffix, delete=False)
        tmp.close()
        path = Path(tmp.name)
        with zipfile.ZipFile(path, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return path

    def test_warns_for_nested_manifest_even_one_level(self):
        zpath = self._zip_with(
            {
                "repo-main/blender_manifest.toml": 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\n',
                "repo-main/code.py": "pass\n",
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Manifest is not at ZIP root" in m for m in messages))

    def test_recommends_legacy_addon_install_path(self):
        zpath = self._zip_with({"my_addon/__init__.py": "bl_info = {}\n"})
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Add-ons > Install from Disk" in m for m in messages))

    def test_errors_when_no_install_markers_found(self):
        zpath = self._zip_with({"README.md": "not an addon"})
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Could not find blender_manifest.toml" in m for m in messages))

    def test_source_archive_hint_for_nested_manifest(self):
        zpath = self._zip_with(
            {
                "my-addon-main/blender_manifest.toml": 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\n',
            },
            name_suffix="-main.zip",
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("source archive" in m.lower() for m in messages))

    def test_source_archive_hint_when_no_markers(self):
        zpath = self._zip_with(
            {
                "repo-main/README.md": "docs",
                "repo-main/LICENSE": "MIT",
            },
            name_suffix="-master.zip",
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("repository source zip" in m.lower() for m in messages))

    def test_warns_for_multiple_addon_roots(self):
        zpath = self._zip_with(
            {
                "addon_a/__init__.py": "bl_info = {}\n",
                "addon_b/__init__.py": "bl_info = {}\n",
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Multiple add-on roots detected" in m for m in messages))

    def test_errors_when_current_blender_below_manifest_min(self):
        zpath = self._zip_with(
            {
                "blender_manifest.toml": 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="5.1.0"\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="5.0.1")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("lower than manifest blender_version_min" in m for m in messages))
        self.assertTrue(any("Pinning hint" in m for m in messages))

    def test_parses_current_blender_version_with_suffix(self):
        zpath = self._zip_with(
            {
                "blender_manifest.toml": 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="5.1.0"\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="Blender v5.0.1-alpha")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("lower than manifest blender_version_min" in m for m in messages))

    def test_warns_when_current_blender_above_manifest_max_with_pinning_hint(self):
        zpath = self._zip_with(
            {
                "blender_manifest.toml": 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\nblender_version_max="4.5.0"\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="5.0.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("higher than manifest blender_version_max" in m for m in messages))
        self.assertTrue(any("declared compatible Blender range is 4.2.0 to 4.5.0" in m for m in messages))

    def test_errors_when_current_blender_below_legacy_bl_info_min(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 2, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.1.9")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("lower than legacy bl_info['blender'] minimum" in m for m in messages))

    def test_ok_when_current_blender_meets_legacy_bl_info_min(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 2, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.2.1")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("satisfies legacy bl_info minimum" in m for m in messages))

    def test_adds_quick_fix_target_for_wrapped_source_zip(self):
        zpath = self._zip_with(
            {
                "repo-main/my_addon/__init__.py": "bl_info = {}\n",
                "repo-main/README.md": "docs",
            },
            name_suffix="-main.zip",
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Quick fix target" in m and "repo-main/my_addon" in m for m in messages))

    def test_ignores_macosx_noise_in_top_level_structure_warning(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": "bl_info = {}\n",
                "__MACOSX/._my_addon": "junk",
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertFalse(any("multiple top-level folders/files" in m.lower() for m in messages))


    def test_detects_single_file_addon_at_root(self):
        zpath = self._zip_with(
            {
                "my_addon.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("single-file add-on packaging depth looks installable" in m.lower() for m in messages))
        self.assertTrue(any("Single-file add-on 'my_addon.py' minimum Blender version: 4.0.0" in m for m in messages))

    def test_warns_for_nested_single_file_addon(self):
        zpath = self._zip_with(
            {
                "repo-main/my_addon.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\n',
            },
            name_suffix="-main.zip",
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("single-file add-on (.py with bl_info) is nested" in m.lower() for m in messages))

    def test_errors_when_current_blender_below_single_file_bl_info_min(self):
        zpath = self._zip_with(
            {
                "my_addon.py": 'bl_info = {"name": "A", "blender": (5, 0, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("lower than 'my_addon.py' bl_info['blender'] minimum" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
