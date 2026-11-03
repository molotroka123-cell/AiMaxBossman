"""mesh (obj/glb) -> centred, upright-scaled, decimated (<=max_faces) GLB with a flat/vertex colour; prints JSON stats."""
import sys, json, numpy as np, trimesh, pymeshlab
def process(src, dst, color=(200, 200, 200), height=6.0, max_faces=18000):
    m = trimesh.load(src, force="mesh")
    ms = pymeshlab.MeshSet(); ms.add_mesh(pymeshlab.Mesh(vertex_matrix=np.asarray(m.vertices, float), face_matrix=np.asarray(m.faces, int)))
    if len(m.faces) > max_faces:
        ms.meshing_decimation_quadric_edge_collapse(targetfacenum=max_faces, preserveboundary=True, preservenormal=True, qualitythr=0.5)
    ms.meshing_remove_duplicate_vertices(); ms.meshing_remove_unreferenced_vertices()
    cm = ms.current_mesh(); v = cm.vertex_matrix(); f = cm.face_matrix()
    out = trimesh.Trimesh(v, f, process=False)
    b = out.bounds; ext = b[1] - b[0]; up = int(np.argmax(ext)) if height else 1
    s = height / ext[1] if height else 1.0
    out.apply_translation(-np.array([(b[0][0] + b[1][0]) / 2, b[0][1], (b[0][2] + b[1][2]) / 2])); out.apply_scale(s)
    out.visual.vertex_colors = np.tile(np.array(list(color) + [255], np.uint8), (len(out.vertices), 1))
    out.export(dst)
    chk = trimesh.load(dst, force="mesh")
    return {"file": dst, "faces": int(len(chk.faces)), "verts": int(len(chk.vertices)), "bytes": __import__("os").path.getsize(dst), "size": [round(x, 2) for x in chk.extents]}
if __name__ == "__main__":
    c = tuple(int(x) for x in sys.argv[3].split(",")) if len(sys.argv) > 3 else (200, 200, 200)
    print(json.dumps(process(sys.argv[1], sys.argv[2], c, float(sys.argv[4]) if len(sys.argv) > 4 else 6.0)))
