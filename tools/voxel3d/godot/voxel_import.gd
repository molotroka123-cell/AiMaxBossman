extends SceneTree
## Headless check for Bossman voxel3d objects in BossBlocks (run after `godot --headless --import`).
## 1. every res://assets/voxel/*.glb loads as a PackedScene, instances inside the running game,
##    and its mesh bounds equal the structure's block bounds (1 voxel = 1 block);
## 2. every structure that fits the world is stamped through main.gd, saved by the game and
##    restored by a second game instance through the game's own loader.

const VS := preload("res://scripts/voxel_structures.gd")
const TEST_SAVE := "user://bossblocks-voxel-import.json"


func _initialize() -> void:
	call_deferred("_run")


func _names() -> Array[String]:
	var out: Array[String] = []
	for file in DirAccess.get_files_at("res://structures"):
		if file.ends_with(".json"):
			out.append(file.get_basename())
	out.sort()
	return out


func _fresh_game(clear: bool):
	if clear and FileAccess.file_exists(TEST_SAVE):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(TEST_SAVE))
	var game = load("res://scenes/main.tscn").instantiate()
	game.save_path = TEST_SAVE
	root.add_child(game)
	return game


func _run() -> void:
	var names := _names()
	if names.is_empty():
		return _fail("no structures in res://structures")
	var game = _fresh_game(true)
	await process_frame
	var props := 0
	for name in names:
		var data := VS.load_structure("res://structures/%s.json" % name)
		if data.is_empty():
			return _fail("structure parse " + name)
		var glb := "res://assets/voxel/%s.glb" % name
		var prop := VS.spawn_prop(game, glb, Vector3(0, -0.5, -20 - 40 * props))
		if prop == null:
			return _fail("glb load " + glb)
		await process_frame
		var box := VS.prop_aabb(prop)
		var lo := Vector3i(1 << 20, 1 << 20, 1 << 20)
		var hi := -lo
		var colors := {}
		for c in VS.cells(data, Vector3i.ZERO):
			lo = Vector3i(mini(lo.x, c.x), mini(lo.y, c.y), mini(lo.z, c.z))
			hi = Vector3i(maxi(hi.x, c.x), maxi(hi.y, c.y), maxi(hi.z, c.z))
		for record in data["blocks"]:
			colors[int(record.get("color", 0))] = true
		var expect := Vector3(hi - lo + Vector3i.ONE)
		if not box.size.is_equal_approx(expect):
			return _fail("prop %s bounds %s != blocks %s" % [name, box.size, expect])
		var surfaces := 0
		for node in prop.find_children("*", "MeshInstance3D", true, false):
			surfaces += (node as MeshInstance3D).mesh.get_surface_count()
		if surfaces != colors.size():
			return _fail("prop %s surfaces %d != colors %d" % [name, surfaces, colors.size()])
		print("VOXEL_PROP ", name, " size=", box.size, " surfaces=", surfaces, " blocks=", data["blocks"].size())
		props += 1
	game.queue_free()
	await process_frame
	var stamped := 0
	for name in names:
		var data := VS.load_structure("res://structures/%s.json" % name)
		if not bool(data.get("fits_world_at_origin", false)):
			print("VOXEL_STRUCTURE ", name, " skipped: larger than the game world bounds")
			continue
		var writer = _fresh_game(true)
		await process_frame
		var before: int = writer._placeable_count()
		var placed := VS.stamp(writer, data, Vector3i.ZERO)
		if placed != data["blocks"].size():
			return _fail("stamp %s placed %d of %d" % [name, placed, data["blocks"].size()])
		if writer._placeable_count() != before + placed:
			return _fail("placeable count " + name)
		writer._save_world()
		writer.queue_free()
		await process_frame
		var reader = _fresh_game(false)
		await process_frame
		for record in data["blocks"]:
			var cell := Vector3i(int(record["x"]), int(record["y"]), int(record["z"]))
			if not reader.blocks.has(cell) or int(reader.blocks[cell].get_meta("kind")) != int(record["kind"]):
				return _fail("reload %s lost %s" % [name, cell])
		print("VOXEL_STRUCTURE ", name, " stamped=", placed, " reloaded=OK")
		reader.queue_free()
		await process_frame
		stamped += 1
	if stamped == 0:
		return _fail("no structure fits the world")
	print("VOXEL_IMPORT_PASS props=%d structures=%d" % [props, stamped])
	quit(0)


func _fail(step: String) -> void:
	printerr("VOXEL_IMPORT_FAIL: ", step)
	quit(1)
