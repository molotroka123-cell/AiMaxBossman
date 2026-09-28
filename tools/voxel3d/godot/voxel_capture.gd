extends SceneTree
## Windowed capture (real renderer, not headless): all voxel props placed in the BossBlocks world
## plus one stamped structure, saved to res://tests/voxel-props.png. Like tests/capture_game.gd.

const VS := preload("res://scripts/voxel_structures.gd")


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var game = load("res://scenes/main.tscn").instantiate()
	game.save_path = "user://bossblocks-voxel-capture.json"
	root.add_child(game)
	await process_frame
	var names: Array[String] = []
	for file in DirAccess.get_files_at("res://structures"):
		if file.ends_with(".json"):
			names.append(file.get_basename())
	names.sort()
	# lay the props out in rows on and behind the world, left to right
	var x := -8.0
	var z := -4.0
	var row_depth := 0.0
	var tallest := 0.0
	var stamped := ""
	for name in names:
		var data := VS.load_structure("res://structures/%s.json" % name)
		var size: Array = data.get("size", [1, 1, 1])
		if stamped == "" and bool(data.get("fits_world_at_origin", false)):
			VS.stamp(game, data, Vector3i(5, 0, 5))
			stamped = name
		if x + float(size[0]) > 9.0:
			x = -8.0
			z -= row_depth + 2.0
			row_depth = 0.0
		VS.spawn_prop(game, "res://assets/voxel/%s.glb" % name, Vector3(x + size[0] / 2.0, -0.5, z - size[2] / 2.0))
		x += float(size[0]) + 1.5
		row_depth = maxf(row_depth, float(size[2]))
		tallest = maxf(tallest, float(size[1]))
	var cam := Camera3D.new()
	root.add_child(cam)
	cam.position = Vector3(0, tallest + 6.0, 16.0)
	cam.look_at(Vector3(0, tallest * 0.35, z * 0.55))
	cam.fov = 62.0
	cam.current = true
	game.hud.text = "BossBlocks + Bossman voxel3d props (%d) · stamped structure: %s" % [names.size(), stamped]
	for i in range(20):
		await process_frame
	var path := "res://tests/voxel-props.png"
	var error := root.get_viewport().get_texture().get_image().save_png(path)
	if error != OK:
		printerr("VOXEL_CAPTURE_FAIL: ", error_string(error))
		quit(1)
		return
	print("VOXEL_CAPTURE_PASS: ", ProjectSettings.globalize_path(path))
	quit(0)
