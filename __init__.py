bl_info = {
    "name": "Extension Install Doctor",
    "author": "Clawie + Toasty",
    "version": (0, 1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > Doctor",
    "description": "Diagnose Blender extension/add-on ZIP install issues",
    "category": "System",
}

import bpy

from .diagnostics_core import diagnose_zip

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
