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

    def test_does_not_show_missing_manifest_warning_for_valid_legacy_addon(self):
        zpath = self._zip_with({"my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\n'})
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertFalse(any("Extension manifest issue" in m for m in warnings))

    def test_errors_when_no_install_markers_found(self):
        zpath = self._zip_with({"README.md": "not an addon"})
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Could not find blender_manifest.toml" in m for m in messages))

    def test_detects_embedded_zip_wrapper_archives(self):
        zpath = self._zip_with(
            {
                "my-addon-package.zip": "binary-ish",
                "README.txt": "install notes",
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("contains another ZIP" in m for m in messages))
        self.assertTrue(any("Embedded ZIP candidate(s): my-addon-package.zip" in m for m in messages))

    def test_identifies_single_installable_inner_zip_and_install_path(self):
        inner = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        inner.close()
        inner_path = Path(inner.name)
        with zipfile.ZipFile(inner_path, "w") as zf:
            zf.writestr("blender_manifest.toml", 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\n')

        outer = self._zip_with(
            {
                "bundle/installable.zip": inner_path.read_bytes(),
                "bundle/README.txt": "notes",
            }
        )
        report = diagnose_zip(str(outer))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Inner ZIP 'bundle/installable.zip' looks installable" in m for m in messages))
        self.assertTrue(any("Extensions > Install from Disk" in m for m in messages))

    def test_warns_when_multiple_installable_inner_zips_found(self):
        ext_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        ext_zip.close()
        ext_path = Path(ext_zip.name)
        with zipfile.ZipFile(ext_path, "w") as zf:
            zf.writestr("blender_manifest.toml", 'id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\n')

        legacy_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        legacy_zip.close()
        legacy_path = Path(legacy_zip.name)
        with zipfile.ZipFile(legacy_path, "w") as zf:
            zf.writestr("my_addon/__init__.py", "bl_info = {}\n")

        outer = self._zip_with(
            {
                "bundle/ext.zip": ext_path.read_bytes(),
                "bundle/legacy.zip": legacy_path.read_bytes(),
            }
        )
        report = diagnose_zip(str(outer))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Multiple inner ZIPs look potentially installable" in m for m in messages))
        self.assertTrue(any("bundle/ext.zip: extension markers" in m for m in messages))
        self.assertTrue(any("bundle/legacy.zip: legacy markers" in m for m in messages))

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

    def test_errors_when_manifest_version_is_not_semver(self):
        zpath = self._zip_with(
            {
                "blender_manifest.toml": 'id="my-addon"\nname="A"\nversion="v1"\nblender_version_min="4.2.0"\n',
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Manifest 'version' should be SemVer" in m for m in messages))

    def test_errors_when_manifest_blender_range_is_inverted(self):
        zpath = self._zip_with(
            {
                "blender_manifest.toml": 'id="my-addon"\nname="A"\nversion="1.2.3"\nblender_version_min="5.0.0"\nblender_version_max="4.2.0"\n',
            }
        )
        report = diagnose_zip(str(zpath))
        messages = [e.message for e in report.entries]
        self.assertTrue(any("max lower than blender_version_min" in m for m in messages))

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


    def test_accepts_direct_single_file_addon_path(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp.write(b'bl_info = {"name": "A", "blender": (4, 0, 0)}\n')
        tmp.close()

        report = diagnose_zip(tmp.name, current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Detected legacy single-file add-on" in m for m in messages))
        self.assertTrue(any("select the .py file directly" in m.lower() for m in messages))

    def test_errors_for_direct_py_without_bl_info(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp.write(b'print("hello")\n')
        tmp.close()

        report = diagnose_zip(tmp.name)
        messages = [e.message for e in report.entries]
        self.assertTrue(any("does not declare bl_info" in m for m in messages))

    def test_errors_for_non_zip_non_py_selection(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp.write(b'not an addon')
        tmp.close()

        report = diagnose_zip(tmp.name)
        messages = [e.message for e in report.entries]
        self.assertTrue(any("not a .zip or .py" in m for m in messages))

    def test_reports_actionable_error_for_rar_archive(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".rar", delete=False)
        tmp.write(b'not really rar data')
        tmp.close()

        report = diagnose_zip(tmp.name)
        messages = [e.message for e in report.entries]
        self.assertTrue(any("unsupported archive format '.rar'" in m for m in messages))
        self.assertTrue(any("extract this archive and create a .zip" in m.lower() for m in messages))

    def test_reports_actionable_error_for_tar_gz_archive(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        tmp.write(b'not really tar data')
        tmp.close()

        report = diagnose_zip(tmp.name)
        messages = [e.message for e in report.entries]
        self.assertTrue(any("unsupported archive format '.tar.gz'" in m for m in messages))

    def test_errors_when_current_blender_below_single_file_bl_info_min(self):
        zpath = self._zip_with(
            {
                "my_addon.py": 'bl_info = {"name": "A", "blender": (5, 0, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("lower than 'my_addon.py' bl_info['blender'] minimum" in m for m in messages))

    def test_directory_path_reports_folder_packaging_guidance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'blender_manifest.toml').write_text('id="a"\nname="A"\nversion="1.0.0"\nblender_version_min="4.2.0"\n')
            report = diagnose_zip(td)
            messages = [e.message for e in report.entries]
            self.assertTrue(any('Selected path is a folder' in m for m in messages))
            self.assertTrue(any('extension source root' in m.lower() for m in messages))
            self.assertTrue(any('zip this folder CONTENTS'.lower() in m.lower() for m in messages))

    def test_directory_path_without_markers_errors_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'README.md').write_text('docs')
            report = diagnose_zip(td)
            messages = [e.message for e in report.entries]
            self.assertTrue(any('Selected path is a folder' in m for m in messages))
            self.assertTrue(any('Could not find install markers' in m for m in messages))

    def test_errors_when_legacy_init_missing_bl_info(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": "print('no addon metadata')\n",
            }
        )
        report = diagnose_zip(str(zpath))
        errors = [e.message for e in report.entries if e.level == "ERROR"]
        self.assertTrue(any("missing bl_info" in m for m in errors))

    def test_warns_when_legacy_bl_info_missing_blender_tuple(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "version": (1, 0, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("missing 'blender' compatibility tuple" in m for m in warnings))

    def test_accepts_annotated_bl_info_in_legacy_init(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info: dict = {"name": "A", "blender": (4, 1, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath), current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Legacy bl_info minimum Blender version: 4.1.0" in m for m in messages))
        self.assertFalse(any("missing bl_info" in m.lower() for m in messages))

    def test_accepts_annotated_bl_info_in_direct_single_file_addon(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp.write(b'bl_info: dict = {"name": "A", "blender": (4, 0, 0)}\n')
        tmp.close()

        report = diagnose_zip(tmp.name, current_blender_version="4.2.0")
        messages = [e.message for e in report.entries]
        self.assertTrue(any("Detected legacy single-file add-on" in m for m in messages))
        self.assertTrue(any("minimum Blender version: 4.0.0" in m for m in messages))

    def test_directory_root_manifest_gets_schema_and_version_checks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'blender_manifest.toml').write_text('id="my addon"\nname="A"\nversion="bad"\nblender_version_min="5.1.0"\n')
            report = diagnose_zip(td, current_blender_version="5.0.0")
            messages = [e.message for e in report.entries]
            self.assertTrue(any("Found and parsed blender_manifest.toml" in m for m in messages))
            self.assertTrue(any("Manifest 'id' should be a non-empty slug without spaces" in m for m in messages))
            self.assertTrue(any("Manifest 'version' should be SemVer" in m for m in messages))
            self.assertTrue(any("lower than manifest blender_version_min" in m for m in messages))

    def test_directory_legacy_root_init_missing_bl_info_reports_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / '__init__.py').write_text("print('no metadata')\n")
            report = diagnose_zip(td)
            errors = [e.message for e in report.entries if e.level == "ERROR"]
            self.assertTrue(any("missing bl_info metadata" in m for m in errors))

    def test_flags_runtime_risk_for_bgl_import_in_zip(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport bgl\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("Detected 'bgl' import" in m for m in warnings))

    def test_flags_runtime_risk_for_distutils_in_direct_single_file(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp.write(b'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport distutils\n')
        tmp.close()

        report = diagnose_zip(tmp.name)
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("Detected 'distutils' import" in m for m in warnings))

    def test_flags_top_level_sleep_as_enable_freeze_risk(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport time\ntime.sleep(2)\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("time.sleep" in m and "module import" in m for m in warnings))

    def test_does_not_flag_sleep_inside_function(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport time\n\ndef later():\n    time.sleep(2)\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertFalse(any("time.sleep" in m and "module import" in m for m in warnings))

    def test_flags_top_level_socket_connection_as_enable_freeze_risk(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport socket\nsocket.create_connection(("example.com", 443), timeout=5)\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("socket connection attempt" in m and "module import" in m for m in warnings))

    def test_flags_top_level_asyncio_run_as_enable_blocking_risk(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\nimport asyncio\nasyncio.run(asyncio.sleep(0.1))\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("asyncio.run" in m and "module import" in m for m in warnings))

    def test_flags_top_level_bpy_ops_call_as_context_risk(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": (
                    'bl_info = {"name": "A", "blender": (4, 0, 0)}\n'
                    'import bpy\n'
                    'bpy.ops.object.select_all(action="SELECT")\n'
                ),
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("bpy.ops" in m and "module import" in m for m in warnings))

    def test_does_not_flag_bpy_ops_when_called_inside_function(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": (
                    'bl_info = {"name": "A", "blender": (4, 0, 0)}\n'
                    'import bpy\n\n'
                    'def run_later():\n'
                    '    bpy.ops.object.select_all(action="SELECT")\n'
                ),
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertFalse(any("bpy.ops" in m and "module import" in m for m in warnings))

    def test_flags_missing_register_unregister_hooks_in_legacy_addon(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": 'bl_info = {"name": "A", "blender": (4, 0, 0)}\n',
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertTrue(any("no module-level register()" in m for m in warnings))
        self.assertTrue(any("no module-level unregister()" in m for m in warnings))

    def test_does_not_flag_missing_register_when_hooks_imported(self):
        zpath = self._zip_with(
            {
                "my_addon/__init__.py": (
                    'bl_info = {"name": "A", "blender": (4, 0, 0)}\n'
                    'from .ops import register, unregister\n'
                ),
                "my_addon/ops.py": "def register():\n    pass\n\ndef unregister():\n    pass\n",
            }
        )
        report = diagnose_zip(str(zpath))
        warnings = [e.message for e in report.entries if e.level == "WARNING"]
        self.assertFalse(any("no module-level register()" in m for m in warnings))
        self.assertFalse(any("no module-level unregister()" in m for m in warnings))


if __name__ == "__main__":
    unittest.main()
