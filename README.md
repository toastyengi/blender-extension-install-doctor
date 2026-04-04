# Blender Extension Install Doctor (v0.1)

First MVP of a Blender plugin that diagnoses extension/add-on install ZIP issues.

## What it does

- Analyze a selected extension/add-on package (`.zip` or legacy single-file `.py`)
- Diagnose unpacked add-on/extension folders and tell users exactly what to zip (common "selected folder instead of install ZIP" mistake)
- Detect if package looks like:
  - Blender extension (`blender_manifest.toml` present)
  - Legacy add-on (`__init__.py` present)
  - Malformed/unknown package
- Validate packaging depth to catch common install-path confusion:
  - warns if `blender_manifest.toml` is not at ZIP root (common GitHub/GitLab source ZIP mistake)
  - warns if `__init__.py` is nested too deep for legacy add-ons
  - warns when multiple add-on roots or multiple manifests exist in one ZIP
  - detects wrapper archives that only contain inner ZIPs, inspects them, and points to the actual install candidate
  - shows detected marker root candidates to help users re-zip the correct folder
  - warns on mixed extension + legacy markers
- Gives explicit install-path recommendation:
  - **Extensions > Install from Disk** for extension packages
  - **Add-ons > Install from Disk** for legacy add-ons (including direct single-file `.py` add-ons)
- Validate some manifest basics:
  - required keys (`id`, `version`, `name`)
  - `blender_version_min` presence
  - compatibility warning/error against current Blender version when possible
- For legacy add-ons, reads `bl_info["blender"]` from `__init__.py` **and single-file `.py` add-ons**, then checks minimum Blender compatibility when current Blender version is known
- Scans addon Python files for high-risk runtime breakage signatures (`bgl`, `distutils`, `imp`) and provides compatibility migration hints
- Show clear findings in a panel (ERROR / WARNING / OK / INFO)

## Install

1. Put this folder in a zip named `blender_extension_install_doctor.zip`.
2. In Blender: **Edit → Preferences → Add-ons → Install from Disk...**
3. Enable **Extension Install Doctor**.
4. Open View3D sidebar (`N`) → **Doctor** tab.

## Usage

1. Select a ZIP in the panel.
2. Click **Diagnose ZIP**.
3. Read findings.

## Current limitations

- Basic checks only (no auto-fix writing yet)
- No full extension schema validation yet
- No direct hook into Blender install logs yet

## Changelog

### v0.2 (unreleased)
- Added folder-path diagnostics for unpacked add-ons/extensions: if users select a directory instead of an install ZIP, Doctor now explains why install fails and points to the exact folder contents to zip.
- Added manifest schema sanity checks for install-critical fields: validates SemVer format for `version`, flags malformed `blender_version_min/max`, and detects invalid/inverted Blender compatibility ranges.
- Added `id` slug hygiene check (non-empty, no spaces) to catch common manifest mistakes before install.
- Added packaging-depth diagnostics to catch extra ZIP nesting (common with GitHub/GitLab source downloads).
- Tightened extension check: warns whenever `blender_manifest.toml` is not at ZIP root, with re-zip guidance.
- Added marker-root candidate hints so users can quickly identify which folder should be re-zipped.
- Added explicit install-path guidance (Extensions vs Add-ons) based on detected package type.
- Added source-archive detection hints (`*-main.zip` / `*-master.zip` / archive-style names) to reduce GitHub/GitLab download confusion.
- Added ambiguity diagnostics for ZIPs that contain multiple add-on roots / multiple manifests.
- Added targeted "Quick fix target" hints for wrapped source archives (points to the exact folder users should re-zip).
- Reduced false-positive packaging warnings by ignoring common archive noise folders/files (e.g. `__MACOSX`, `.DS_Store`).
- Added manifest-vs-current-Blender compatibility check (min/max version signal) to reduce install/version confusion.
- Added legacy add-on compatibility check using `bl_info["blender"]` from `__init__.py`, with explicit guidance when version mismatch is detected.
- Improved version-mismatch fix hints to better support Blender/add-on version pinning decisions.
- Added explicit compatibility range pinning hints (e.g. target Blender <= declared max, or >= declared min) for faster downgrade/upgrade decisions.
- Made version parsing more robust for real-world strings like `Blender v5.0.1-alpha` so compatibility checks run more reliably.
- Split diagnosis engine into reusable `diagnostics_core.py` for easier testing and future CLI use.
- Added detection for legacy **single-file add-ons** (`.py` with `bl_info`) so valid packages are no longer misclassified as malformed ZIPs.
- Added nested-depth diagnostics for single-file add-ons and compatibility checks against current Blender version.
- Added wrapper-archive diagnostics: if a ZIP only contains another ZIP (common double-compressed download pattern), Doctor now points users to the inner install ZIP.
- Added embedded-inner-ZIP inspection to identify which inner ZIP is actually installable and which install path to use (Extensions vs Add-ons).
- Reduced false-error noise for wrapper archives: when an outer ZIP has no markers but contains installable inner ZIP(s), Doctor now avoids the generic "not installable" hard error and points directly to the usable inner package.
- Added direct `.py` analysis so users can diagnose/install legacy single-file add-ons without zipping first.
- Added clear error guidance when a selected `.py` lacks `bl_info`, reducing false "invalid ZIP" confusion.
- Added explicit unsupported-archive diagnostics (`.rar`, `.7z`, `.tar.gz`, etc.) with re-packaging guidance, reducing dead-end install attempts when users select non-ZIP downloads.
- Reduced false-positive warnings for valid legacy add-ons by suppressing "missing extension manifest" noise when legacy markers are present.
- Added explicit legacy metadata diagnostics: raises an error when `__init__.py` lacks `bl_info` and warns when `bl_info` exists but omits Blender compatibility tuple.
- Upgraded folder-mode diagnosis (when users select an unpacked directory): now parses root `blender_manifest.toml` for schema/compatibility checks and validates root legacy `__init__.py` metadata (`bl_info`) with clearer compatibility errors.
- Added support for annotated legacy metadata declarations (`bl_info: dict = {...}`) in both zipped add-ons and direct single-file `.py` add-ons, reducing false “missing bl_info” errors.
- Added runtime-compatibility risk scanning for common Blender/Python breakpoints (`import bgl`, `import distutils`, `import imp`) with actionable migration hints, helping users diagnose install-success-but-runtime-fail scenarios.
- Added import-time freeze-risk diagnostics for top-level blocking calls (`time.sleep`, network fetches, subprocess calls) that can hang Blender while enabling an add-on.
- Expanded enable-freeze diagnostics to catch more real-world startup hangs: top-level `socket.create_connection`, `urllib.request.urlretrieve`, `thread.join`, and `asyncio.run` calls now raise targeted warnings with non-blocking migration guidance.
- Added legacy lifecycle-hook diagnostics: when a `.py`/`__init__.py` declares `bl_info` but omits `register()` and/or `unregister()`, Doctor now warns with fix guidance for the common "installs but doesn't enable/work" failure mode.
- Added import-time context-risk detection for top-level `bpy.ops.*` calls, a common cause of "installed but fails to enable" errors due to missing Blender context during module import.
- Added import-time context-risk detection for top-level `bpy.context` access, which often breaks enable/startup when add-ons assume an active UI context too early.
- Added native-binary packaging diagnostics (`.pyd`/`.so`/`.dylib`) to flag cross-platform runtime risk; warns when bundled binaries are present but manifest `platforms` is not declared.
- Added third-party dependency risk diagnostics (`import requests`, etc.) to flag likely `ModuleNotFoundError` enable failures when packages rely on unbundled external modules.

## Next milestones

- Add a "Fix Plan" section with step-by-step recipes
- Add manifest scaffold generator
- Add packaging structure lint with concrete rewrite suggestions
- Optional: "Export repaired zip copy"
