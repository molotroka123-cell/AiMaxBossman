"""Bossman Voxel3D: prompt -> voxel spec (local model) -> deterministic .vox / .glb / BossBlocks structure.

Same split as Motion Studio: the model only fills a small, validated JSON form (palette + shape
ops inside a bounded grid); everything that becomes a file is built deterministically here.
The product path (spec, grid, vox, mesh, bossblocks) uses only the standard library.
Pillow is needed only for preview PNGs; trimesh/pygltflib/py-vox-io only for verification.
"""

MAX_DIM = 32
