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
        self.assertTrue(any("Could not find blender_manifest.toml or __init__.py" in m for m in messages))

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


if __name__ == "__main__":
    unittest.main()
