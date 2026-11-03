"""Hunyuan shape-only mesh has no texture (paint model needs CUDA custom_rasterizer). Approximation: auto-pick the projection axis whose
silhouette best matches the concept image, then colour each vertex from the concept pixel (mirrored for the back side, darker)."""
import sys, json, numpy as np, trimesh
from PIL import Image
def project(mesh_path, img_path, dst):
    m = trimesh.load(mesh_path, force="mesh"); im = Image.open(img_path).convert("RGB"); A = np.asarray(im).astype(float)
    bg = np.median(np.concatenate([A[:8].reshape(-1, 3), A[-8:].reshape(-1, 3)]), axis=0)
    mask = (np.abs(A - bg).sum(2) > 45); ys, xs = np.nonzero(mask); x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    V = m.vertices - m.vertices.min(0); best = None
    for ax_u, ax_v, flip in [(2, 1, 1), (2, 1, -1), (0, 1, 1), (0, 1, -1)]:
        u = V[:, ax_u]; v = V[:, ax_v]
        if flip < 0: u = u.max() - u
        px = x0 + u / max(u.max(), 1e-6) * (x1 - x0); py = y1 - v / max(v.max(), 1e-6) * (y1 - y0)
        g = np.zeros_like(mask); g[np.clip(py.astype(int), 0, mask.shape[0] - 1), np.clip(px.astype(int), 0, mask.shape[1] - 1)] = True
        from scipy.ndimage import binary_dilation
        g = binary_dilation(g, iterations=6); iou = (g & mask).sum() / max((g | mask).sum(), 1)
        if best is None or iou > best[0]: best = (iou, ax_u, ax_v, flip, px, py)
    iou, ax_u, ax_v, flip, px, py = best
    from scipy.ndimage import distance_transform_edt, binary_erosion
    _, (iy, ix) = distance_transform_edt(~binary_erosion(mask, iterations=3), return_indices=True)  # nearest foreground pixel for samples that land on the background
    yy = np.clip(py.astype(int), 0, A.shape[0] - 1); xx = np.clip(px.astype(int), 0, A.shape[1] - 1)
    yy, xx = iy[yy, xx], ix[yy, xx]
    col = A[yy, xx]
    nrm = m.vertex_normals; depth_axis = ({0, 1, 2} - {ax_u, ax_v}).pop()
    back = nrm[:, depth_axis] > 0.2  # rough: faces turned away from the camera get darker
    # decide which sign is the camera side by picking the one whose bright (lit) sample matches better: assume -axis faces camera
    col[back] *= 0.8
    m.visual.vertex_colors = np.c_[np.clip(col, 0, 255).astype(np.uint8), np.full(len(col), 255, np.uint8)]
    m.export(dst); return {"iou": round(float(iou), 3), "axes": [ax_u, ax_v, flip]}
if __name__ == "__main__":
    print(json.dumps(project(*sys.argv[1:4])))
