extends RefCounted
## Bossman voxel3d helpers for BossBlocks.
## A structure file (format "bossblocks.structure" v1) uses the game's own save-record schema
## {"x","y","z","kind"}; stamp() places it through the game's _add_block, so collision, saving
## and loading work exactly as for hand-placed blocks. spawn_prop() instances an imported GLB.


static func load_structure(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var data = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not (data is Dictionary) or str(data.get("format", "")) != "bossblocks.structure":
		return {}
	if not (data.get("blocks") is Array):
		return {}
	return data


static func cells(data: Dictionary, origin: Vector3i) -> Array[Vector3i]:
	var out: Array[Vector3i] = []
	for record in data["blocks"]:
		if record is Dictionary and record.has_all(["x", "y", "z", "kind"]):
			out.append(origin + Vector3i(int(record["x"]), int(record["y"]), int(record["z"])))
	return out


static func stamp(game, data: Dictionary, origin: Vector3i) -> int:
	var placed := 0
	for record in data["blocks"]:
		if not (record is Dictionary) or not record.has_all(["x", "y", "z", "kind"]):
			continue
		var cell := origin + Vector3i(int(record["x"]), int(record["y"]), int(record["z"]))
		if game.blocks.has(cell):
			continue
		game._add_block(cell, int(record["kind"]))
		if game.blocks.has(cell):
			placed += 1
	return placed


static func spawn_prop(parent: Node3D, glb_path: String, position: Vector3) -> Node3D:
	var scene := load(glb_path) as PackedScene
	if scene == null:
		return null
	var prop := scene.instantiate() as Node3D
	if prop == null:
		return null
	prop.position = position
	parent.add_child(prop)
	return prop


static func prop_aabb(prop: Node3D) -> AABB:
	var box := AABB()
	var first := true
	for node in prop.find_children("*", "MeshInstance3D", true, false):
		var mi := node as MeshInstance3D
		var local := prop.global_transform.affine_inverse() * mi.global_transform * mi.get_aabb()
		box = local if first else box.merge(local)
		first = false
	return box
