# blender -b -P render_preview.py -- model.glb out.png
import bpy, sys, math
src, out = sys.argv[sys.argv.index("--") + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
import mathutils
mn = mathutils.Vector((1e9,) * 3); mx = mathutils.Vector((-1e9,) * 3)
for o in objs:
    for c in o.bound_box:
        w = o.matrix_world @ mathutils.Vector(c); mn = mathutils.Vector(map(min, mn, w)); mx = mathutils.Vector(map(max, mx, w))
ctr = (mn + mx) / 2; size = max((mx - mn).length, 1)
cam = bpy.data.objects.new("c", bpy.data.cameras.new("c")); bpy.context.scene.collection.objects.link(cam)
cam.location = ctr + mathutils.Vector((size * 0.9, -size * 1.1, size * 0.45))
d = ctr - cam.location; cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler(); bpy.context.scene.camera = cam
for loc, e in (((4, -4, 6), 3.0), ((-5, -2, 3), 1.2)):
    l = bpy.data.objects.new("l", bpy.data.lights.new("l", "SUN")); l.data.energy = e; l.rotation_euler = (math.radians(50), 0, math.radians(30 if e > 2 else -60)); bpy.context.scene.collection.objects.link(l)
w = bpy.data.worlds.new("w"); w.use_nodes = True; w.node_tree.nodes["Background"].inputs[0].default_value = (0.82, 0.84, 0.88, 1); bpy.context.scene.world = w
s = bpy.context.scene; s.render.engine = "BLENDER_WORKBENCH"; s.display.shading.light = "STUDIO"; s.display.shading.color_type = "MATERIAL" if any(o.data.materials for o in objs) else "VERTEX"
s.display.shading.color_type = "VERTEX"
s.render.resolution_x = s.render.resolution_y = 768; s.render.filepath = out; bpy.ops.render.render(write_still=True)
