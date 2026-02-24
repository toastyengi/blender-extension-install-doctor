# Blender Extension Install Doctor (v0.1)

First MVP of a Blender plugin that diagnoses extension/add-on install ZIP issues.

## What it does

- Analyze a selected `.zip`
- Detect if package looks like:
  - Blender extension (`blender_manifest.toml` present)
  - Legacy add-on (`__init__.py` present)
  - Malformed/unknown package
- Validate packaging depth to catch common install-path confusion:
  - warns if `blender_manifest.toml` is not at ZIP root (common GitHub/GitLab source ZIP mistake)
  - warns if `__init__.py` is nested too deep for legacy add-ons
  - shows detected marker root candidates to help users re-zip the correct folder
  - warns on mixed extension + legacy markers
- Gives explicit install-path recommendation:
  - **Extensions > Install from Disk** for extension packages
  - **Add-ons > Install from Disk** for legacy add-ons
- Validate some manifest basics:
  - required keys (`id`, `version`, `name`)
  - `blender_version_min` presence
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
- Added packaging-depth diagnostics to catch extra ZIP nesting (common with GitHub/GitLab source downloads).
- Tightened extension check: warns whenever `blender_manifest.toml` is not at ZIP root, with re-zip guidance.
- Added marker-root candidate hints so users can quickly identify which folder should be re-zipped.
- Added explicit install-path guidance (Extensions vs Add-ons) based on detected package type.
- Split diagnosis engine into reusable `diagnostics_core.py` for easier testing and future CLI use.

## Next milestones

- Add a "Fix Plan" section with step-by-step recipes
- Add manifest scaffold generator
- Add packaging structure lint with concrete rewrite suggestions
- Optional: "Export repaired zip copy"
