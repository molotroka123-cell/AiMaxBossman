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
	# two centred rows: the shorter half of the props in front, the taller half behind
	var items := []
	var stamped := ""
	for name in names:
		var data := VS.load_structure("res://structures/%s.json" % name)
		var size: Array = data.get("size", [1, 1, 1])
		items.append({"name": name, "w": float(size[0]), "h": float(size[1]), "d": float(size[2])})
		if stamped == "" and bool(data.get("fits_world_at_origin", false)):
			VS.stamp(game, data, Vector3i(0, 0, 4))
			stamped = name
	items.sort_custom(func(a, b): return a["h"] < b["h"])
	var half := int(ceil(items.size() / 2.0))
	var rows := [items.slice(0, half), items.slice(half)]
	var z := -2.0
	var tallest := 0.0
	var widest := 0.0
	for row in rows:
		var width := 0.0
		var depth := 0.0
		for it in row:
			width += it["w"] + 2.0
			depth = maxf(depth, it["d"])
			tallest = maxf(tallest, it["h"])
		widest = maxf(widest, width)
		var x := -width / 2.0 + 1.0
		for it in row:
			VS.spawn_prop(game, "res://assets/voxel/%s.glb" % it["name"], Vector3(x + it["w"] / 2.0, -0.5, z - depth / 2.0))
			x += it["w"] + 2.0
		z -= depth + 4.0
	var cam := Camera3D.new()
	root.add_child(cam)
	cam.position = Vector3(0, tallest * 0.9 + 6.0, 13.0 + widest * 0.45)
	cam.look_at(Vector3(0, tallest * 0.3, z * 0.5))
	cam.fov = 55.0
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
