# Blender Extension Install Doctor (v0.1)

First MVP of a Blender plugin that diagnoses extension/add-on install ZIP issues.

## What it does

- Analyze a selected `.zip`
- Detect if package looks like:
  - Blender extension (`blender_manifest.toml` present)
  - Legacy add-on (`__init__.py` present)
  - Malformed/unknown package
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

## Next milestones

- Add a "Fix Plan" section with step-by-step recipes
- Add manifest scaffold generator
- Add packaging structure lint with concrete rewrite suggestions
- Optional: "Export repaired zip copy"
