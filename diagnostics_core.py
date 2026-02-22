import os
import zipfile
import traceback
from dataclasses import dataclass, field
from typing import List

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None


@dataclass
class Diagnosis:
    level: str
    message: str


@dataclass
class Report:
    entries: List[Diagnosis] = field(default_factory=list)

    def add(self, level: str, message: str):
        self.entries.append(Diagnosis(level, message))


def _marker_depths(names: List[str], marker_name: str) -> List[int]:
    depths = []
    for n in names:
        if n.endswith(f"/{marker_name}") or n == marker_name:
            depths.append(len([p for p in n.split("/") if p]) - 1)
    return depths


def _read_manifest(zf: zipfile.ZipFile):
    manifest_candidates = [n for n in zf.namelist() if n.endswith("blender_manifest.toml")]
    if not manifest_candidates:
        return None, "No blender_manifest.toml found"

    manifest_path = manifest_candidates[0]
    try:
        data = zf.read(manifest_path)
    except Exception as e:
        return None, f"Unable to read manifest: {e}"

    if tomllib is None:
        return None, "tomllib not available in this Blender Python build"

    try:
        return tomllib.loads(data.decode("utf-8")), None
    except Exception as e:
        return None, f"Manifest parse error: {e}"


def diagnose_zip(zip_path: str) -> Report:
    report = Report()

    if not zip_path:
        report.add("ERROR", "No file selected")
        return report

    if not os.path.exists(zip_path):
        report.add("ERROR", f"File does not exist: {zip_path}")
        return report

    if not zip_path.lower().endswith(".zip"):
        report.add("WARNING", "Selected file is not a .zip archive")

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            if not names:
                report.add("ERROR", "ZIP is empty")
                return report

            top_dirs = {n.split("/")[0] for n in names if "/" in n}
            init_depths = _marker_depths(names, "__init__.py")
            manifest_depths = _marker_depths(names, "blender_manifest.toml")

            has_init = bool(init_depths)
            has_manifest = bool(manifest_depths)

            manifest, manifest_err = _read_manifest(zf)

            if has_manifest:
                min_depth = min(manifest_depths)
                if min_depth > 1:
                    report.add(
                        "WARNING",
                        "Manifest is nested too deep in ZIP (likely GitHub source ZIP). Re-zip so extension root contains blender_manifest.toml directly.",
                    )
                else:
                    report.add("OK", "Extension packaging depth looks installable")

            if has_init and not has_manifest:
                min_depth = min(init_depths)
                if min_depth > 1:
                    report.add(
                        "WARNING",
                        "Add-on __init__.py is nested too deep. Re-zip so addon folder (with __init__.py) is at ZIP root.",
                    )
                else:
                    report.add("OK", "Legacy add-on packaging depth looks installable")

            if has_manifest and has_init:
                report.add(
                    "WARNING",
                    "Both extension manifest and legacy __init__.py detected. Ensure you install through the intended path to avoid confusion.",
                )

            if manifest is None:
                report.add("WARNING", f"Extension manifest issue: {manifest_err}")
                if has_init:
                    report.add(
                        "INFO",
                        "Recommended install path: Edit > Preferences > Add-ons > Install from Disk (legacy add-on).",
                    )
                else:
                    report.add(
                        "INFO",
                        "Could not detect legacy add-on markers either. Package may be malformed.",
                    )
            else:
                report.add("OK", "Found and parsed blender_manifest.toml")
                report.add(
                    "INFO",
                    "Recommended install path: Edit > Preferences > Extensions > Install from Disk (extension package).",
                )

                required_fields = ["id", "version", "name"]
                missing = [k for k in required_fields if k not in manifest]
                if missing:
                    report.add("ERROR", f"Manifest missing required fields: {', '.join(missing)}")
                else:
                    report.add("OK", "Manifest has required base fields (id, version, name)")

                blender_version_min = manifest.get("blender_version_min")
                if not blender_version_min:
                    report.add("WARNING", "Manifest missing blender_version_min")
                else:
                    report.add("OK", f"blender_version_min = {blender_version_min}")

            if len(top_dirs) > 1:
                report.add(
                    "WARNING",
                    "ZIP has multiple top-level folders/files; installers often expect a cleaner package root.",
                )

    except zipfile.BadZipFile:
        report.add("ERROR", "Invalid ZIP file (BadZipFile)")
    except Exception as e:
        report.add("ERROR", f"Unexpected error analyzing ZIP: {e}")
        report.add("INFO", traceback.format_exc(limit=1))

    if not report.entries:
        report.add("INFO", "No findings")

    return report
