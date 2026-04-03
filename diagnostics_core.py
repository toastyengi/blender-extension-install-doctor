import ast
import io
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


def _detect_unsupported_archive(path: str) -> Optional[str]:
    name = Path(path).name.lower()
    unsupported_exts = [".rar", ".7z", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz"]
    for ext in unsupported_exts:
        if name.endswith(ext):
            return ext
    return None


def _embedded_zip_candidates(names: List[str]) -> List[str]:
    return sorted([n for n in names if n.lower().endswith(".zip")])


@dataclass
class EmbeddedZipSignal:
    path: str
    has_manifest: bool = False
    has_init: bool = False
    has_single_file_addon: bool = False
    error: Optional[str] = None

    @property
    def installable(self) -> bool:
        return self.has_manifest or self.has_init or self.has_single_file_addon


def _classify_zip_names(names: List[str]) -> Tuple[bool, bool, bool]:
    has_manifest = any(n.endswith("blender_manifest.toml") for n in names)
    has_init = any(n.endswith("/__init__.py") or n == "__init__.py" for n in names)
    has_single_file = any(n.endswith(".py") and not n.endswith("/__init__.py") for n in names)
    return has_manifest, has_init, has_single_file


def _scan_embedded_zip_signals(zf: zipfile.ZipFile, embedded_zip_paths: List[str]) -> List[EmbeddedZipSignal]:
    signals: List[EmbeddedZipSignal] = []
    for path in embedded_zip_paths:
        try:
            data = zf.read(path)
            with zipfile.ZipFile(io.BytesIO(data), "r") as inner:
                names = inner.namelist()
                has_manifest, has_init, has_single_file = _classify_zip_names(names)
                if has_single_file:
                    # avoid counting random .py files unless they look like single-file add-ons
                    candidates = _find_legacy_single_file_addons(inner)
                    has_single_file = any(min_v is not None for _p, min_v, _e in candidates)
                signals.append(
                    EmbeddedZipSignal(
                        path=path,
                        has_manifest=has_manifest,
                        has_init=has_init,
                        has_single_file_addon=has_single_file,
                    )
                )
        except Exception as e:
            signals.append(EmbeddedZipSignal(path=path, error=str(e)))
    return signals


RUNTIME_RISK_SIGNATURES = [
    {
        "pattern": re.compile(r"(^|\n)\s*(from\s+bgl\s+import\b|import\s+bgl\b)", re.MULTILINE),
        "message": "Detected 'bgl' import(s). Blender 4.x/5.x commonly breaks legacy bgl-based drawing code.",
        "hint": "Prefer gpu module + gpu_extras replacement APIs (or install an addon release updated for Blender 4/5).",
    },
    {
        "pattern": re.compile(r"(^|\n)\s*(from\s+distutils\s+import\b|import\s+distutils\b)", re.MULTILINE),
        "message": "Detected 'distutils' import(s). Python 3.12+ removed distutils and Blender 5.x builds may fail at runtime.",
        "hint": "Replace distutils usage with setuptools/packaging equivalents or use a Blender-compatible addon release.",
    },
    {
        "pattern": re.compile(r"(^|\n)\s*(from\s+imp\s+import\b|import\s+imp\b)", re.MULTILINE),
        "message": "Detected deprecated 'imp' module import(s). Modern Python versions removed imp, causing addon startup errors.",
        "hint": "Switch to importlib APIs (importlib.util/importlib.machinery) in updated addon code.",
    },
]


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _top_level_blocking_call_hits(source: str) -> List[Tuple[str, str]]:
    risky_calls = {
        "time.sleep": "Detected top-level 'time.sleep(...)' call during module import. This can freeze Blender while enabling the add-on.",
        "requests.get": "Detected top-level HTTP request call during module import. Network stalls can freeze Blender while enabling the add-on.",
        "requests.post": "Detected top-level HTTP request call during module import. Network stalls can freeze Blender while enabling the add-on.",
        "urllib.request.urlopen": "Detected top-level URL fetch during module import. Slow connections can freeze Blender while enabling the add-on.",
        "urllib.request.urlretrieve": "Detected top-level URL download during module import. Network stalls can freeze Blender while enabling the add-on.",
        "socket.create_connection": "Detected top-level socket connection attempt during module import. Network timeouts can freeze Blender while enabling the add-on.",
        "subprocess.run": "Detected top-level subprocess call during module import. This can block Blender startup/enable flow.",
        "subprocess.call": "Detected top-level subprocess call during module import. This can block Blender startup/enable flow.",
        "subprocess.check_output": "Detected top-level subprocess call during module import. This can block Blender startup/enable flow.",
        "subprocess.Popen": "Detected top-level subprocess launch during module import. This may cause enable-time instability.",
        "thread.join": "Detected top-level thread join during module import. Waiting for threads at import time can freeze add-on enable.",
        "asyncio.run": "Detected top-level asyncio.run(...) call during module import. Long async startup tasks can block add-on enable.",
    }
    hint = "Move blocking work into operators/timers/background tasks and keep module import + register() fast/non-blocking."
    context_hint = "Avoid bpy.ops calls during module import. Move context-dependent operator calls into register(), operators, or UI callbacks after Blender context is ready."
    bpy_context_hint = "Avoid touching bpy.context during module import. Read context inside operators/panels/register callbacks after Blender UI context exists."

    try:
        tree = ast.parse(source)
    except Exception:
        return []

    hits: List[Tuple[str, str]] = []
    seen = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in risky_calls and name not in seen:
                    seen.add(name)
                    hits.append((risky_calls[name], hint))
                if name and name.startswith("bpy.ops.") and "bpy.ops.*" not in seen:
                    seen.add("bpy.ops.*")
                    hits.append((
                        "Detected top-level 'bpy.ops.*' call during module import. Operator calls at import-time often fail with context errors or break add-on enable.",
                        context_hint,
                    ))
            elif isinstance(node, ast.Attribute):
                attr_name = _call_name(node)
                if attr_name and (attr_name == "bpy.context" or attr_name.startswith("bpy.context.")) and "bpy.context.*" not in seen:
                    seen.add("bpy.context.*")
                    hits.append((
                        "Detected top-level 'bpy.context' access during module import. Context can be incomplete at import-time and may break add-on enable/startup.",
                        bpy_context_hint,
                    ))
    return hits


def _legacy_register_hook_risk_in_source(source: str) -> List[Tuple[str, str]]:
    """Warn when a legacy addon declares bl_info but no register/unregister hooks.

    This catches a common "installs but doesn't show/work" failure mode.
    """
    try:
        tree = ast.parse(source)
    except Exception:
        return []

    has_bl_info = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "bl_info":
                    has_bl_info = True
                    break
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "bl_info":
                has_bl_info = True
        if has_bl_info:
            break

    if not has_bl_info:
        return []

    has_register = False
    has_unregister = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "register":
                has_register = True
            elif node.name == "unregister":
                has_unregister = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "register":
                    has_register = True
                elif isinstance(target, ast.Name) and target.id == "unregister":
                    has_unregister = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "register":
                has_register = True
            elif isinstance(node.target, ast.Name) and node.target.id == "unregister":
                has_unregister = True
        elif isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            if "register" in imported:
                has_register = True
            if "unregister" in imported:
                has_unregister = True

    findings: List[Tuple[str, str]] = []
    if not has_register:
        findings.append((
            "Legacy add-on metadata (bl_info) detected, but no module-level register() hook was found.",
            "Ensure the add-on exposes register()/unregister() at module level (or imports/re-exports them) so Blender can enable it.",
        ))
    if not has_unregister:
        findings.append((
            "Legacy add-on metadata (bl_info) detected, but no module-level unregister() hook was found.",
            "Add an unregister() hook to avoid disable/reload issues in Blender Preferences > Add-ons.",
        ))
    return findings


def _runtime_risk_hits_in_source(source: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    for sig in RUNTIME_RISK_SIGNATURES:
        if sig["pattern"].search(source):
            hits.append((sig["message"], sig["hint"]))
    hits.extend(_top_level_blocking_call_hits(source))
    hits.extend(_legacy_register_hook_risk_in_source(source))
    return hits


def _scan_zip_runtime_risks(zf: zipfile.ZipFile) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    seen = set()
    py_names = [n for n in zf.namelist() if n.endswith(".py") and not n.endswith("/")]
    for path in py_names[:300]:
        try:
            source = zf.read(path).decode("utf-8", errors="replace")
        except Exception:
            continue
        for msg, hint in _runtime_risk_hits_in_source(source):
            key = (msg, hint)
            if key not in seen:
                seen.add(key)
                findings.append(key)
    return findings


def _scan_directory_runtime_risks(dir_path: str) -> List[Tuple[str, str]]:
    findings: List[Tuple[str, str]] = []
    seen = set()
    base = Path(dir_path)
    for idx, path in enumerate(sorted(base.rglob("*.py"))):
        if idx >= 300:
            break
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for msg, hint in _runtime_risk_hits_in_source(source):
            key = (msg, hint)
            if key not in seen:
                seen.add(key)
                findings.append(key)
    return findings


def _add_runtime_risk_guidance(report: Report, findings: List[Tuple[str, str]]):
    for msg, hint in findings:
        report.add("WARNING", msg)
        report.add("INFO", f"Runtime compatibility hint: {hint}")


_NATIVE_BINARY_EXTS = (".pyd", ".so", ".dylib")


def _scan_zip_native_binaries(zf: zipfile.ZipFile) -> List[str]:
    return [
        n for n in zf.namelist()
        if n.lower().endswith(_NATIVE_BINARY_EXTS) and not n.endswith("/")
    ]


def _scan_directory_native_binaries(dir_path: str) -> List[str]:
    base = Path(dir_path)
    out: List[str] = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in _NATIVE_BINARY_EXTS:
            out.append(p.relative_to(base).as_posix())
    return out


def _add_native_binary_guidance(report: Report, binary_paths: List[str], manifest: Optional[dict] = None):
    if not binary_paths:
        return

    sample = ", ".join(binary_paths[:3])
    more = f" (+{len(binary_paths) - 3} more)" if len(binary_paths) > 3 else ""
    report.add(
        "WARNING",
        f"Detected native binary module(s) in package: {sample}{more}. Cross-platform/version mismatches can cause install-success-but-enable/runtime failures.",
    )
    report.add(
        "INFO",
        "Runtime compatibility hint: verify add-on build matches your OS/CPU and Blender Python ABI; if unsure, use a release explicitly built for your Blender version.",
    )

    if manifest is not None:
        platforms = manifest.get("platforms") if isinstance(manifest, dict) else None
        if not isinstance(platforms, list) or not platforms:
            report.add(
                "WARNING",
                "Native binaries detected but manifest has no explicit 'platforms' list. Users on other systems may install a package that fails to enable.",
            )
            report.add(
                "INFO",
                "Runtime compatibility hint: declare supported platforms in blender_manifest.toml and publish per-platform builds when binaries are bundled.",
            )
        else:
            report.add("INFO", f"Manifest platforms declared: {', '.join(str(p) for p in platforms)}")


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


def _looks_like_semver(value: str) -> bool:
    # Accept SemVer core with optional pre-release/build metadata.
    # Examples: 1.0.0, 2.4.1-beta.2, 0.9.0+build5
    return bool(re.match(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$", str(value).strip()))


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


def _validate_manifest(report: Report, manifest: dict, current_blender_version: Optional[str] = None):
    required_fields = ["id", "version", "name"]
    missing = [k for k in required_fields if k not in manifest]
    if missing:
        report.add("ERROR", f"Manifest missing required fields: {', '.join(missing)}")
    else:
        report.add("OK", "Manifest has required base fields (id, version, name)")

        addon_id = str(manifest.get("id", "")).strip()
        if not addon_id or any(ch.isspace() for ch in addon_id):
            report.add("ERROR", "Manifest 'id' should be a non-empty slug without spaces.")

        manifest_version = manifest.get("version")
        if not _looks_like_semver(str(manifest_version)):
            report.add(
                "ERROR",
                f"Manifest 'version' should be SemVer (x.y.z). Got: {manifest_version}",
            )
        else:
            report.add("OK", f"Manifest version format looks valid: {manifest_version}")

    blender_version_min = manifest.get("blender_version_min")
    min_v = _parse_version_tuple(str(blender_version_min)) if blender_version_min else None
    if not blender_version_min:
        report.add("WARNING", "Manifest missing blender_version_min")
    elif min_v is None:
        report.add("ERROR", f"Manifest blender_version_min is not parseable: {blender_version_min}")
    else:
        report.add("OK", f"blender_version_min = {blender_version_min}")

    blender_version_max = manifest.get("blender_version_max")
    max_v = _parse_version_tuple(str(blender_version_max)) if blender_version_max else None
    if blender_version_max:
        if max_v is None:
            report.add("ERROR", f"Manifest blender_version_max is not parseable: {blender_version_max}")
        else:
            report.add("OK", f"blender_version_max = {blender_version_max}")

    if min_v is not None and max_v is not None and max_v < min_v:
        report.add(
            "ERROR",
            "Manifest declares blender_version_max lower than blender_version_min; compatibility range is invalid.",
        )

    if min_v is not None and max_v is not None and min_v == max_v:
        report.add("INFO", "Manifest targets exactly one Blender version (min == max).")

    if current_blender_version:
        current_v = _parse_version_tuple(current_blender_version)
        if current_v is None:
            report.add(
                "INFO",
                f"Could not parse current Blender version '{current_blender_version}' for compatibility check.",
            )
            return

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


def _extract_legacy_blender_min_from_source(py_source: str) -> Tuple[Optional[Tuple[int, ...]], Optional[str]]:
    try:
        module = ast.parse(py_source)
    except Exception as e:
        return None, f"Could not parse __init__.py: {e}"

    for node in module.body:
        value_node = None

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "bl_info":
                    value_node = node.value
                    break

        # Support modern annotated style used in some add-ons:
        # bl_info: dict = {...}
        if value_node is None and isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "bl_info":
                value_node = node.value

        if value_node is None:
            continue

        try:
            data = ast.literal_eval(value_node)
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



def _diagnose_single_file_addon(py_path: str, current_blender_version: Optional[str] = None) -> Report:
    report = Report()

    try:
        source = Path(py_path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        report.add("ERROR", f"Could not read Python file: {e}")
        return report

    min_v, err = _extract_legacy_blender_min_from_source(source)
    if min_v is None:
        if err:
            if "bl_info assignment not found" in err:
                report.add("ERROR", "Selected .py file does not declare bl_info, so Blender will not treat it as an installable legacy add-on.")
            else:
                report.add("WARNING", f"Single-file add-on metadata parse issue: {err}")
        else:
            report.add("ERROR", "Could not detect single-file add-on metadata (bl_info).")
        report.add("INFO", "Fix hint: install a .py add-on file that contains a valid bl_info dictionary at module level.")
        return report

    report.add("OK", "Detected legacy single-file add-on (.py with bl_info)")
    report.add("INFO", f"Single-file add-on minimum Blender version: {_fmt_version(min_v)}")
    report.add(
        "INFO",
        "Recommended install path: Edit > Preferences > Add-ons > Install from Disk (select the .py file directly).",
    )

    if current_blender_version:
        current_v = _parse_version_tuple(current_blender_version)
        if current_v is None:
            report.add(
                "INFO",
                f"Could not parse current Blender version '{current_blender_version}' for compatibility check.",
            )
        elif current_v < min_v:
            report.add(
                "ERROR",
                "Current Blender version is lower than bl_info['blender'] minimum; add-on is likely incompatible.",
            )
            report.add(
                "INFO",
                f"Pinning hint: this single-file add-on declares minimum Blender {_fmt_version(min_v)}. Upgrade Blender or use an older add-on variant.",
            )
        else:
            report.add("OK", "Current Blender version satisfies single-file bl_info minimum")

    _add_runtime_risk_guidance(report, _runtime_risk_hits_in_source(source))
    return report


def _find_legacy_single_file_addons_in_dir(root: str) -> List[Tuple[str, Optional[Tuple[int, ...]], Optional[str]]]:
    out: List[Tuple[str, Optional[Tuple[int, ...]], Optional[str]]] = []
    base = Path(root)
    for path in sorted(base.rglob('*.py')):
        if path.name == '__init__.py':
            continue
        try:
            source = path.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            out.append((path.relative_to(base).as_posix(), None, f"Could not read Python file: {e}"))
            continue

        min_v, err = _extract_legacy_blender_min_from_source(source)
        rel = path.relative_to(base).as_posix()
        if min_v is not None:
            out.append((rel, min_v, None))
        elif err and 'bl_info assignment not found' not in err:
            out.append((rel, None, err))

    return out


def _diagnose_directory_addon(dir_path: str, current_blender_version: Optional[str] = None) -> Report:
    report = Report()
    base = Path(dir_path)

    try:
        all_files = [p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()]
    except Exception as e:
        report.add('ERROR', f"Could not read selected folder: {e}")
        return report

    if not all_files:
        report.add('ERROR', 'Selected folder is empty.')
        return report

    manifest_paths = sorted([n for n in all_files if n.endswith('blender_manifest.toml')])
    init_paths = sorted([n for n in all_files if n == '__init__.py' or n.endswith('/__init__.py')])
    single_file_addons = _find_legacy_single_file_addons_in_dir(dir_path)

    report.add('WARNING', 'Selected path is a folder. Blender Install from Disk expects a .zip package (or a single .py add-on file).')

    has_manifest = bool(manifest_paths)
    has_init = bool(init_paths)
    has_single = any(v is not None for _p, v, _e in single_file_addons)
    manifest_for_native: Optional[dict] = None

    if has_manifest:
        root_manifest = 'blender_manifest.toml' in manifest_paths
        if root_manifest:
            report.add('INFO', 'Folder appears to be an extension source root (blender_manifest.toml at folder root).')
            report.add('INFO', 'Quick fix: zip this folder CONTENTS (not the parent folder), then install via Preferences > Extensions > Install from Disk.')
            if tomllib is None:
                report.add('WARNING', 'tomllib not available in this Blender Python build; manifest content checks skipped.')
            else:
                try:
                    manifest_data = (base / 'blender_manifest.toml').read_text(encoding='utf-8', errors='replace')
                    manifest = tomllib.loads(manifest_data)
                    manifest_for_native = manifest
                    report.add('OK', 'Found and parsed blender_manifest.toml')
                    _validate_manifest(report, manifest, current_blender_version=current_blender_version)
                except Exception as e:
                    report.add('ERROR', f"Manifest parse error: {e}")
        else:
            report.add('WARNING', 'Found blender_manifest.toml only in subfolder(s), not folder root.')
            report.add('INFO', f"Manifest location(s): {', '.join(manifest_paths)}")
            report.add('INFO', 'Quick fix: zip the specific subfolder that contains blender_manifest.toml at its root level.')

    if has_init and not has_manifest:
        root_init = '__init__.py' in init_paths
        if root_init:
            report.add('INFO', 'Folder appears to be a legacy add-on root (__init__.py at folder root).')
            report.add('INFO', 'Quick fix: zip this folder (so __init__.py is inside addon root), then install via Preferences > Add-ons > Install from Disk.')
        else:
            report.add('WARNING', 'Legacy __init__.py found only in subfolder(s).')
            report.add('INFO', f"Add-on root candidate(s): {', '.join(sorted({str(Path(p).parent) for p in init_paths}))}")
            report.add('INFO', 'Quick fix: zip the folder that directly contains __init__.py.')

        preferred_init = '__init__.py' if root_init else init_paths[0]
        try:
            source = (base / preferred_init).read_text(encoding='utf-8', errors='replace')
            min_v, err = _extract_legacy_blender_min_from_source(source)
            if min_v is not None:
                report.add('OK', f"Legacy bl_info minimum Blender version: {_fmt_version(min_v)}")
                if current_blender_version:
                    current_v = _parse_version_tuple(current_blender_version)
                    if current_v is not None and current_v < min_v:
                        report.add('ERROR', "Current Blender version is lower than legacy bl_info['blender'] minimum; add-on is likely incompatible.")
                    elif current_v is not None:
                        report.add('OK', 'Current Blender version satisfies legacy bl_info minimum')
            elif err and 'bl_info assignment not found' in err:
                report.add('ERROR', "Legacy __init__.py appears to be missing bl_info metadata (required for Blender add-ons).")
            elif err and "missing 'blender' compatibility tuple" in err:
                report.add('WARNING', "Legacy bl_info found but missing 'blender' compatibility tuple.")
            elif err:
                report.add('WARNING', f"Could not fully parse legacy __init__.py metadata: {err}")
        except Exception as e:
            report.add('WARNING', f"Could not read legacy __init__.py for metadata checks: {e}")

    if has_single and not has_manifest:
        valid_single = [p for p, v, _e in single_file_addons if v is not None]
        report.add('INFO', f"Detected single-file add-on candidate(s): {', '.join(valid_single)}")
        if len(valid_single) == 1 and '/' not in valid_single[0]:
            report.add('INFO', 'You can install this .py directly via Preferences > Add-ons > Install from Disk (no ZIP needed).')

    if not has_manifest and not has_init and not has_single:
        report.add('ERROR', 'Could not find install markers in this folder (blender_manifest.toml, __init__.py, or .py with bl_info).')

    if current_blender_version and has_single:
        current_v = _parse_version_tuple(current_blender_version)
        if current_v is not None:
            for path, min_v, _err in single_file_addons:
                if min_v is not None and current_v < min_v:
                    report.add('ERROR', f"Current Blender version is lower than '{path}' bl_info['blender'] minimum.")

    _add_runtime_risk_guidance(report, _scan_directory_runtime_risks(dir_path))
    _add_native_binary_guidance(report, _scan_directory_native_binaries(dir_path), manifest=manifest_for_native)
    return report

def diagnose_zip(zip_path: str, current_blender_version: Optional[str] = None) -> Report:
    report = Report()

    if not zip_path:
        report.add("ERROR", "No file selected")
        return report

    if not os.path.exists(zip_path):
        report.add("ERROR", f"File does not exist: {zip_path}")
        return report

    if os.path.isdir(zip_path):
        return _diagnose_directory_addon(zip_path, current_blender_version=current_blender_version)

    lower_path = zip_path.lower()
    if lower_path.endswith(".py"):
        return _diagnose_single_file_addon(zip_path, current_blender_version=current_blender_version)

    unsupported_ext = _detect_unsupported_archive(lower_path)
    if unsupported_ext:
        report.add(
            "ERROR",
            f"Selected package uses unsupported archive format '{unsupported_ext}'. Blender Install from Disk expects a .zip package (or a single .py add-on file).",
        )
        report.add(
            "INFO",
            "Fix hint: extract this archive and create a .zip where blender_manifest.toml or add-on __init__.py/.py is in the expected install location.",
        )
        return report

    if not lower_path.endswith(".zip"):
        report.add("ERROR", "Selected file is not a .zip or .py add-on package")
        report.add("INFO", "Choose an extension ZIP or a legacy single-file .py add-on.")
        return report

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
            embedded_zip_paths = _embedded_zip_candidates(names)
            embedded_zip_signals = _scan_embedded_zip_signals(zf, embedded_zip_paths) if embedded_zip_paths else []

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
                    if legacy_err == "bl_info assignment not found":
                        report.add(
                            "ERROR",
                            "Legacy add-on __init__.py is missing bl_info, so Blender may not recognize it as an installable add-on.",
                        )
                        report.add(
                            "INFO",
                            "Fix hint: add a valid bl_info dictionary near the top of __init__.py (including at least name/version/blender).",
                        )
                    elif legacy_err and "Could not parse __init__.py" in legacy_err:
                        report.add("ERROR", f"Legacy add-on metadata parse failed: {legacy_err}")
                    elif legacy_err:
                        report.add("WARNING", f"Legacy compatibility check skipped: {legacy_err}")
                    else:
                        report.add("INFO", "Legacy compatibility check skipped.")

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

            installable_inner: List[EmbeddedZipSignal] = []
            if embedded_zip_paths and not has_manifest and not has_init and not has_single_file_addon:
                report.add(
                    "WARNING",
                    "This ZIP contains another ZIP but no install markers at the current level. You likely selected an outer wrapper archive.",
                )
                report.add("INFO", f"Embedded ZIP candidate(s): {', '.join(embedded_zip_paths)}")

                installable_inner = [s for s in embedded_zip_signals if s.installable]
                if len(installable_inner) == 1:
                    s = installable_inner[0]
                    report.add("OK", f"Inner ZIP '{s.path}' looks installable.")
                    if s.has_manifest:
                        report.add(
                            "INFO",
                            "Install hint: use Extensions > Install from Disk and select the inner ZIP.",
                        )
                    elif s.has_init or s.has_single_file_addon:
                        report.add(
                            "INFO",
                            "Install hint: use Add-ons > Install from Disk and select the inner ZIP.",
                        )
                elif len(installable_inner) > 1:
                    report.add(
                        "WARNING",
                        "Multiple inner ZIPs look potentially installable. Choose the one matching your intended package type.",
                    )
                    for s in installable_inner:
                        kinds = []
                        if s.has_manifest:
                            kinds.append("extension")
                        if s.has_init or s.has_single_file_addon:
                            kinds.append("legacy")
                        report.add("INFO", f"- {s.path}: {', '.join(kinds)} markers")
                else:
                    unreadable = [s for s in embedded_zip_signals if s.error]
                    if unreadable:
                        report.add("INFO", f"Could not inspect embedded ZIP(s): {', '.join(f'{s.path} ({s.error})' for s in unreadable)}")

                report.add(
                    "INFO",
                    "Fix hint: extract this archive, then install the inner ZIP that contains blender_manifest.toml or the add-on __init__.py/.py file.",
                )

            if not has_manifest and not has_init and not has_single_file_addon:
                if installable_inner:
                    report.add(
                        "INFO",
                        "Outer ZIP is not directly installable, but embedded installable ZIP candidate(s) were found.",
                    )
                else:
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
                if has_manifest:
                    report.add("WARNING", f"Extension manifest issue: {manifest_err}")

                if has_init or has_single_file_addon:
                    report.add(
                        "INFO",
                        "Recommended install path: Edit > Preferences > Add-ons > Install from Disk (legacy add-on).",
                    )
                elif not has_manifest:
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

                _validate_manifest(report, manifest, current_blender_version=current_blender_version)

            _add_runtime_risk_guidance(report, _scan_zip_runtime_risks(zf))
            _add_native_binary_guidance(report, _scan_zip_native_binaries(zf), manifest=manifest)

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
