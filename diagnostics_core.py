import ast
import os
import zipfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
import re
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


def _is_ignorable_top_level(name: str) -> bool:
    return name in {"__MACOSX", ".DS_Store", "Thumbs.db"}


def _single_root_wrapper(names: List[str], marker_name: str) -> Optional[str]:
    """Return wrapper folder when marker exists only below one top-level root.

    Example: repo-main/my_addon/__init__.py -> returns "repo-main/my_addon"
    """
    roots = _marker_roots(names, marker_name)
    if not roots:
        return None

    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    top_dirs = {d for d in top_dirs if not _is_ignorable_top_level(d)}
    if len(top_dirs) != 1:
        return None

    top = next(iter(top_dirs))
    candidates = [r for r in roots if r != "." and r.startswith(f"{top}/")]
    if len(candidates) == 1:
        return candidates[0]
    return None


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

    # Accept common real-world variants from Blender/UI/docs:
    # - v5.0.1
    # - 5.0.0-alpha
    # - 4.2 (LTS)
    # - Blender 5.0.0
    raw = str(value).strip()
    m = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", raw)
    if not m:
        return None

    out = [int(g) for g in m.groups() if g is not None]
    return tuple(out) if out else None


def _fmt_version(v: Tuple[int, ...]) -> str:
    return ".".join(str(p) for p in v)


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


def _extract_legacy_blender_min_from_source(py_source: str) -> Tuple[Optional[Tuple[int, ...]], Optional[str]]:
    try:
        module = ast.parse(py_source)
    except Exception as e:
        return None, f"Could not parse __init__.py: {e}"

    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "bl_info":
                    try:
                        data = ast.literal_eval(node.value)
                    except Exception as e:
                        return None, f"Could not evaluate bl_info dictionary: {e}"

                    if not isinstance(data, dict):
                        return None, "bl_info exists but is not a dictionary literal"

                    blender_value = data.get("blender")
                    if blender_value is None:
                        return None, "bl_info missing 'blender' compatibility tuple"

                    if isinstance(blender_value, (tuple, list)) and blender_value:
                        out = []
                        for p in blender_value:
                            if not isinstance(p, int):
                                return None, "bl_info['blender'] should contain integers"
                            out.append(p)
                        return tuple(out), None

                    return None, "bl_info['blender'] is not a tuple/list"

    return None, "bl_info assignment not found"


def _read_legacy_blender_min(zf: zipfile.ZipFile) -> Tuple[Optional[Tuple[int, ...]], Optional[str]]:
    init_candidates = [n for n in zf.namelist() if n.endswith("/__init__.py") or n == "__init__.py"]
    if not init_candidates:
        return None, "No __init__.py found for legacy add-on analysis"

    init_path = sorted(init_candidates, key=lambda n: (n.count("/"), n))[0]
    try:
        source = zf.read(init_path).decode("utf-8", errors="replace")
    except Exception as e:
        return None, f"Could not read legacy __init__.py: {e}"

    return _extract_legacy_blender_min_from_source(source)




def _find_legacy_single_file_addons(zf: zipfile.ZipFile) -> List[Tuple[str, Optional[Tuple[int, ...]], Optional[str]]]:
    """Return python files that look like legacy single-file addons (contain bl_info)."""
    out: List[Tuple[str, Optional[Tuple[int, ...]], Optional[str]]] = []
    py_candidates = [n for n in zf.namelist() if n.endswith(".py") and not n.endswith("/__init__.py")]
    for path in sorted(py_candidates, key=lambda n: (n.count("/"), n)):
        try:
            source = zf.read(path).decode("utf-8", errors="replace")
        except Exception as e:
            out.append((path, None, f"Could not read Python file: {e}"))
            continue

        min_v, err = _extract_legacy_blender_min_from_source(source)
        if min_v is not None:
            out.append((path, min_v, None))
        elif err and "bl_info assignment not found" not in err:
            # Keep parse/eval problems visible because they often explain install failures.
            out.append((path, None, err))

    return out

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
            top_dirs = {d for d in top_dirs if not _is_ignorable_top_level(d)}
            init_depths = _marker_depths(names, "__init__.py")
            manifest_depths = _marker_depths(names, "blender_manifest.toml")

            init_roots = _marker_roots(names, "__init__.py")
            manifest_roots = _marker_roots(names, "blender_manifest.toml")

            single_file_addons = _find_legacy_single_file_addons(zf)
            single_file_paths = [p for p, _v, _e in single_file_addons]
            single_file_depths = [len([p for p in n.split("/") if p]) - 1 for n in single_file_paths]

            has_init = bool(init_depths)
            has_manifest = bool(manifest_depths)
            has_single_file_addon = bool(single_file_paths)

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
                    wrapper_target = _single_root_wrapper(names, "blender_manifest.toml")
                    if wrapper_target:
                        report.add(
                            "INFO",
                            f"Quick fix target: create a new ZIP from '{wrapper_target}' contents (not the outer repository folder).",
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
                    wrapper_target = _single_root_wrapper(names, "__init__.py")
                    if wrapper_target:
                        report.add(
                            "INFO",
                            f"Quick fix target: create a new ZIP from '{wrapper_target}' (folder containing __init__.py) instead of the outer repository ZIP.",
                        )
                    if _looks_like_source_archive_name(zip_path):
                        report.add(
                            "INFO",
                            "This looks like a source archive (e.g. GitHub/GitLab download ZIP). If a Releases install ZIP exists, use that instead.",
                        )
                else:
                    report.add("OK", "Legacy add-on packaging depth looks installable")

                legacy_min_v, legacy_err = _read_legacy_blender_min(zf)
                if legacy_min_v is not None:
                    report.add("INFO", f"Legacy bl_info minimum Blender version: {'.'.join(str(p) for p in legacy_min_v)}")
                    if current_blender_version:
                        current_v = _parse_version_tuple(current_blender_version)
                        if current_v is None:
                            report.add(
                                "INFO",
                                f"Could not parse current Blender version '{current_blender_version}' for legacy compatibility check.",
                            )
                        elif current_v < legacy_min_v:
                            report.add(
                                "ERROR",
                                "Current Blender version is lower than legacy bl_info['blender'] minimum; add-on is likely incompatible.",
                            )
                            report.add(
                                "INFO",
                                f"Pinning hint: this legacy add-on declares minimum Blender {_fmt_version(legacy_min_v)}. Upgrade Blender to >= {_fmt_version(legacy_min_v)} or install an older add-on release for your current Blender.",
                            )
                        else:
                            report.add("OK", "Current Blender version satisfies legacy bl_info minimum")
                else:
                    report.add("INFO", f"Legacy compatibility check skipped: {legacy_err}")

            if has_single_file_addon and not has_manifest:
                min_depth = min(single_file_depths)
                if min_depth > 0:
                    report.add(
                        "WARNING",
                        "Legacy single-file add-on (.py with bl_info) is nested in subfolder(s). Re-zip so the .py file is at ZIP root.",
                    )
                    report.add(
                        "INFO",
                        f"Detected single-file add-on candidate(s): {', '.join(single_file_paths)}",
                    )
                    if _looks_like_source_archive_name(zip_path):
                        report.add(
                            "INFO",
                            "This looks like a source archive. Prefer a release/install ZIP when available.",
                        )
                else:
                    report.add("OK", "Legacy single-file add-on packaging depth looks installable")

                report.add(
                    "INFO",
                    "Recommended install path: Edit > Preferences > Add-ons > Install from Disk (legacy add-on).",
                )

                for path, min_v, err in single_file_addons:
                    if min_v is not None:
                        report.add("INFO", f"Single-file add-on '{path}' minimum Blender version: {_fmt_version(min_v)}")
                        if current_blender_version:
                            current_v = _parse_version_tuple(current_blender_version)
                            if current_v is None:
                                report.add(
                                    "INFO",
                                    f"Could not parse current Blender version '{current_blender_version}' for single-file compatibility check.",
                                )
                            elif current_v < min_v:
                                report.add(
                                    "ERROR",
                                    f"Current Blender version is lower than '{path}' bl_info['blender'] minimum; add-on is likely incompatible.",
                                )
                    elif err:
                        report.add("WARNING", f"Single-file add-on candidate '{path}' has bl_info parse issue: {err}")

            if has_manifest and has_init:
                report.add(
                    "WARNING",
                    "Both extension manifest and legacy __init__.py detected. Ensure you install through the intended path to avoid confusion.",
                )

            if not has_manifest and not has_init and not has_single_file_addon:
                report.add(
                    "ERROR",
                    "Could not find blender_manifest.toml, __init__.py, or a single-file add-on module with bl_info. This ZIP likely is source/docs, not an installable package.",
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
                            report.add(
                                "INFO",
                                f"Pinning hint: this package targets Blender >= {_fmt_version(min_v)}. Upgrade Blender or use an older add-on release compatible with {_fmt_version(current_v)}.",
                            )
                        if max_v is not None and current_v > max_v:
                            report.add(
                                "WARNING",
                                "Current Blender version is higher than manifest blender_version_max; addon may be unsupported.",
                            )
                            report.add(
                                "INFO",
                                f"Pinning hint: declared compatible Blender range is {_fmt_version(min_v) if min_v else '?'} to {_fmt_version(max_v)}. Use a Blender version <= {_fmt_version(max_v)} or find a newer add-on release.",
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
