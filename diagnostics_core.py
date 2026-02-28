import os
import zipfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

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


def _marker_roots(names: List[str], marker_name: str) -> List[str]:
    roots = set()
    for n in names:
        if n == marker_name:
            roots.add(".")
        elif n.endswith(f"/{marker_name}"):
            root = n[: -len(marker_name)].rstrip("/")
            roots.add(root)
    return sorted(roots)


def _format_roots(roots: List[str]) -> str:
    if not roots:
        return "(none)"
    return ", ".join(roots)


def _looks_like_source_archive_name(zip_path: str) -> bool:
    name = Path(zip_path).name.lower()
    source_tokens = [
        "-main.zip",
        "-master.zip",
        "source code",
        "archive",
        "refs-heads",
    ]
    return any(token in name for token in source_tokens)


def _parse_version_tuple(value: str) -> Optional[Tuple[int, ...]]:
    if not value:
        return None

    parts = str(value).strip().split(".")
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            return None
    return tuple(out) if out else None


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


def diagnose_zip(zip_path: str, current_blender_version: Optional[str] = None) -> Report:
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

            init_roots = _marker_roots(names, "__init__.py")
            manifest_roots = _marker_roots(names, "blender_manifest.toml")

            has_init = bool(init_depths)
            has_manifest = bool(manifest_depths)

            if len(manifest_roots) > 1:
                report.add(
                    "WARNING",
                    "Multiple extension manifests detected in one ZIP. Blender expects a single install target package.",
                )
                report.add("INFO", f"Manifest root candidate(s): {_format_roots(manifest_roots)}")

            if has_init and not has_manifest and len(init_roots) > 1:
                report.add(
                    "WARNING",
                    "Multiple add-on roots detected (__init__.py in multiple folders). Install may fail or install the wrong package.",
                )
                report.add("INFO", f"Add-on root candidate(s): {_format_roots(init_roots)}")
                report.add(
                    "INFO",
                    "Fix hint: create a ZIP containing only one intended add-on folder at root.",
                )

            manifest, manifest_err = _read_manifest(zf)

            if has_manifest:
                min_depth = min(manifest_depths)
                if min_depth > 0:
                    report.add(
                        "WARNING",
                        "Manifest is not at ZIP root. Blender extension installers often require blender_manifest.toml directly at root.",
                    )
                    report.add(
                        "INFO",
                        f"Detected manifest root candidate(s): {_format_roots(manifest_roots)}",
                    )
                    report.add(
                        "INFO",
                        "Fix hint: re-zip the extension folder contents so blender_manifest.toml is the first-level file in the ZIP.",
                    )
                    if _looks_like_source_archive_name(zip_path):
                        report.add(
                            "INFO",
                            "This looks like a source archive (e.g. GitHub/GitLab download ZIP). Prefer a release/install ZIP from the add-on author when available.",
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
                    report.add(
                        "INFO",
                        f"Detected add-on root candidate(s): {_format_roots(init_roots)}",
                    )
                    if _looks_like_source_archive_name(zip_path):
                        report.add(
                            "INFO",
                            "This looks like a source archive (e.g. GitHub/GitLab download ZIP). If a Releases install ZIP exists, use that instead.",
                        )
                else:
                    report.add("OK", "Legacy add-on packaging depth looks installable")

            if has_manifest and has_init:
                report.add(
                    "WARNING",
                    "Both extension manifest and legacy __init__.py detected. Ensure you install through the intended path to avoid confusion.",
                )

            if not has_manifest and not has_init:
                report.add(
                    "ERROR",
                    "Could not find blender_manifest.toml or __init__.py. This ZIP likely is source/docs, not an installable package.",
                )
                if _looks_like_source_archive_name(zip_path):
                    report.add(
                        "INFO",
                        "Likely a repository source ZIP. In GitHub/GitLab, look for Releases assets or zip only the actual add-on folder before installing.",
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

                blender_version_max = manifest.get("blender_version_max")
                if blender_version_max:
                    report.add("OK", f"blender_version_max = {blender_version_max}")

                if current_blender_version:
                    current_v = _parse_version_tuple(current_blender_version)
                    min_v = _parse_version_tuple(str(blender_version_min)) if blender_version_min else None
                    max_v = _parse_version_tuple(str(blender_version_max)) if blender_version_max else None

                    if current_v is None:
                        report.add(
                            "INFO",
                            f"Could not parse current Blender version '{current_blender_version}' for compatibility check.",
                        )
                    else:
                        report.add("INFO", f"Current Blender version (for check): {current_blender_version}")
                        if min_v is not None and current_v < min_v:
                            report.add(
                                "ERROR",
                                "Current Blender version is lower than manifest blender_version_min; installation/runtime issues are likely.",
                            )
                        if max_v is not None and current_v > max_v:
                            report.add(
                                "WARNING",
                                "Current Blender version is higher than manifest blender_version_max; addon may be unsupported.",
                            )

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
