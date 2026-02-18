bl_info = {
    "name": "Extension Install Doctor",
    "author": "Clawie + Toasty",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Doctor",
    "description": "Diagnose Blender extension/add-on ZIP install issues",
    "category": "System",
}

import os
import zipfile
import traceback
from dataclasses import dataclass, field
from typing import List

import bpy

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
            has_init = any(n.endswith("__init__.py") for n in names)

            manifest, manifest_err = _read_manifest(zf)

            if manifest is None:
                report.add("WARNING", f"Extension manifest issue: {manifest_err}")
                if has_init:
                    report.add(
                        "INFO",
                        "Looks like a legacy add-on ZIP (__init__.py found). Install via Add-ons, not Extensions.",
                    )
                else:
                    report.add(
                        "INFO",
                        "Could not detect legacy add-on markers either. Package may be malformed.",
                    )
            else:
                report.add("OK", "Found and parsed blender_manifest.toml")
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


class EID_Item(bpy.types.PropertyGroup):
    level: bpy.props.StringProperty(name="Level")
    message: bpy.props.StringProperty(name="Message")


class EID_Props(bpy.types.PropertyGroup):
    zip_path: bpy.props.StringProperty(
        name="ZIP Path",
        description="Path to extension/add-on ZIP",
        subtype='FILE_PATH',
    )


class EID_OT_diagnose(bpy.types.Operator):
    bl_idname = "eid.diagnose"
    bl_label = "Diagnose ZIP"
    bl_description = "Analyze selected ZIP for common install issues"

    def execute(self, context):
        props = context.scene.eid_props
        findings = diagnose_zip(props.zip_path)

        coll = context.scene.eid_findings
        coll.clear()

        level_priority = {"ERROR": 3, "WARNING": 2, "OK": 1, "INFO": 0}
        worst = 0

        for f in findings.entries:
            row = coll.add()
            row.level = f.level
            row.message = f.message
            worst = max(worst, level_priority.get(f.level, 0))

        if worst >= 3:
            self.report({'ERROR'}, "Diagnosis complete: critical issues found")
        elif worst == 2:
            self.report({'WARNING'}, "Diagnosis complete: warnings found")
        else:
            self.report({'INFO'}, "Diagnosis complete")

        return {'FINISHED'}


class EID_PT_panel(bpy.types.Panel):
    bl_label = "Extension Install Doctor"
    bl_idname = "EID_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Doctor"

    def draw(self, context):
        layout = self.layout
        props = context.scene.eid_props

        layout.prop(props, "zip_path")
        layout.operator("eid.diagnose", icon='CHECKMARK')

        layout.separator()
        layout.label(text="Findings:")

        findings = context.scene.eid_findings
        if not findings:
            layout.label(text="No report yet", icon='INFO')
            return

        for item in findings:
            icon = 'INFO'
            if item.level == "ERROR":
                icon = 'ERROR'
            elif item.level == "WARNING":
                icon = 'ERROR'
            elif item.level == "OK":
                icon = 'CHECKMARK'

            box = layout.box()
            box.label(text=f"[{item.level}]", icon=icon)
            box.label(text=item.message)


classes = (
    EID_Item,
    EID_Props,
    EID_OT_diagnose,
    EID_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)

    bpy.types.Scene.eid_props = bpy.props.PointerProperty(type=EID_Props)
    bpy.types.Scene.eid_findings = bpy.props.CollectionProperty(type=EID_Item)


def unregister():
    del bpy.types.Scene.eid_findings
    del bpy.types.Scene.eid_props

    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
