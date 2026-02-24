import tempfile
import unittest
import zipfile
from pathlib import Path

from diagnostics_core import diagnose_zip


class DiagnoseZipTests(unittest.TestCase):
    def _zip_with(self, members):
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
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


if __name__ == "__main__":
    unittest.main()
