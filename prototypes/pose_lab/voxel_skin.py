"""Geodesic voxel skinning (free, self-contained) -- RBMN prototype.

Implements the interior-diffusion idea of Dionne & de Lasa 2013 ("Geodesic
voxel binding"): voxelize the mesh into a SOLID, seed each bone's voxels,
flood geodesic distances THROUGH the interior, convert distances to weights.
Because distance travels through the solid volume, an arm resting against a
fat torso stays independent of it -- the failure mode of surface/bone-heat.

Pure numpy + mathutils; no external deps beyond what the MIA venv has.
"""
import numpy as np


def compute(mesh_obj, arm_obj, res=110, k_influences=4, power=4.0, cutoff=2.0,
            smooth_iters=15, log=print):
    """Returns (bone_names, weights_csr-like dict vert->[(bone_i, w)...])."""
    import bmesh
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    mw = mesh_obj.matrix_world
    me = mesh_obj.data
    nv = len(me.vertices)
    verts = np.empty((nv, 3), dtype=np.float64)
    for i, v in enumerate(me.vertices):
        w = mw @ v.co
        verts[i] = (w.x, w.y, w.z)

    lo = verts.min(0) - 1e-4
    hi = verts.max(0) + 1e-4
    ext = hi - lo
    vox = float(ext.max()) / float(res)
    dims = np.maximum((ext / vox).astype(int) + 3, 2)
    log(f"VOXSKIN grid {tuple(dims)} vox={vox:.4f}")

    # --- solid fill: ray-parity along +X per (y,z) row --------------------
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.transform(bm, matrix=mw, verts=bm.verts[:])
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    solid = np.zeros(dims, dtype=bool)
    dirv = Vector((1.0, 0.0, 0.0))
    x0 = float(lo[0]) - 10.0 * vox
    for j in range(dims[1]):
        y = float(lo[1]) + (j + 0.5) * vox
        for k in range(dims[2]):
            z = float(lo[2]) + (k + 0.5) * vox
            o = Vector((x0, y, z))
            xs = []
            guard = 0
            while guard < 512:
                hit = bvh.ray_cast(o, dirv)
                if hit[0] is None:
                    break
                xs.append(hit[0].x)
                o = hit[0] + dirv * (vox * 1e-3)
                guard += 1
            if len(xs) < 2:
                continue
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                ia = max(int((a - lo[0]) / vox), 0)
                ib = min(int((b - lo[0]) / vox) + 1, dims[0])
                if ib > ia:
                    solid[ia:ib, j, k] = True
    n_solid = int(solid.sum())
    log(f"VOXSKIN solid voxels {n_solid} ({100.0*n_solid/solid.size:.1f}%)")
    if n_solid == 0:
        return None, None

    # --- bone seeds: voxels along each bone segment (world space) ---------
    amw = arm_obj.matrix_world
    bones = [b for b in arm_obj.data.bones if b.use_deform]
    bone_names = [b.name for b in bones]

    def to_idx(p):
        return np.clip(((p - lo) / vox).astype(int), 0, dims - 1)

    seeds = []
    for b in bones:
        h = amw @ b.head_local
        t = amw @ b.tail_local
        h = np.array((h.x, h.y, h.z))
        t = np.array((t.x, t.y, t.z))
        n = max(int(np.linalg.norm(t - h) / (vox * 0.5)), 2)
        pts = [h + (t - h) * (s / (n - 1.0)) for s in range(n)]
        idx = np.unique(np.array([to_idx(p) for p in pts]), axis=0)
        # snap seeds that fell outside the solid to the nearest solid voxel
        # within a small window (bones can run outside a thin limb's voxels)
        ok = []
        for q in idx:
            if solid[tuple(q)]:
                ok.append(tuple(q))
                continue
            found = None
            for r in range(1, 4):
                sl = tuple(slice(max(q[d]-r, 0), min(q[d]+r+1, dims[d])) for d in range(3))
                sub = np.argwhere(solid[sl])
                if len(sub):
                    off = np.array([s.start for s in sl])
                    d2 = ((sub + off - q) ** 2).sum(1)
                    found = tuple(sub[d2.argmin()] + off)
                    break
            if found:
                ok.append(found)
        seeds.append(ok)

    # --- FAT SEEDS for torso bones ----------------------------------------
    # The scan WELDS resting arms to the flanks, so interior geodesics leak
    # through the contact bridge: a flank voxel is geodesically closer to the
    # surface-hugging forearm bone than to the spine buried at the centre of a
    # huge torso -- so arm rotations dragged the flank (seen in renders 1-3).
    # Fix: seed torso bones as THICK CAPSULES (dilated within the solid), so
    # the torso volume is distance~0 to torso bones and out-competes the arms
    # everywhere except the true arm tube. This is the "support bones" trick
    # of production voxel binding, done volumetrically.
    TORSO_PAT = ("spine", "hips", "neck", "pelvis")
    torso_dilate = max(int(round(res * 0.07)), 4)   # ~7% of body height
    NB = len(bones)
    INF = np.iinfo(np.int32).max
    dists = np.full((NB,) + tuple(dims), INF, dtype=np.int32)

    def dilate(m):
        out = m.copy()
        out[1:, :, :] |= m[:-1, :, :]
        out[:-1, :, :] |= m[1:, :, :]
        out[:, 1:, :] |= m[:, :-1, :]
        out[:, :-1, :] |= m[:, 1:, :]
        out[:, :, 1:] |= m[:, :, :-1]
        out[:, :, :-1] |= m[:, :, 1:]
        return out

    for bi in range(NB):
        if not seeds[bi]:
            continue
        front = np.zeros(dims, dtype=bool)
        for q in seeds[bi]:
            front[q] = True
        front &= solid
        if any(t in bone_names[bi].lower() for t in TORSO_PAT):
            for _ in range(torso_dilate):
                front = dilate(front) & solid
        visited = front.copy()
        d = 0
        db = dists[bi]
        while front.any():
            db[front] = d
            front = dilate(front) & solid & ~visited
            visited |= front
            d += 1

    # --- per-vertex weights ----------------------------------------------
    vidx = np.clip(((verts - lo) / vox).astype(int), 0, dims - 1)
    # vertices land on the surface; nudge any non-solid cell to nearest solid
    # neighbour (cheap 3x3x3 scan)
    flat_solid = solid
    per_vert = np.empty((nv, NB), dtype=np.float64)
    for i in range(nv):
        q = vidx[i]
        if not flat_solid[tuple(q)]:
            best = None
            for r in range(1, 4):
                sl = tuple(slice(max(q[d]-r, 0), min(q[d]+r+1, dims[d])) for d in range(3))
                sub = np.argwhere(flat_solid[sl])
                if len(sub):
                    off = np.array([s.start for s in sl])
                    d2 = ((sub + off - q) ** 2).sum(1)
                    best = sub[d2.argmin()] + off
                    break
            if best is not None:
                q = best
        per_vert[i] = dists[(slice(None),) + tuple(q)]
    per_vert[per_vert >= INF] = 1e9

    # k nearest bones by geodesic distance -> inverse-power weights
    order = np.argsort(per_vert, axis=1)[:, :k_influences]
    rows = np.arange(nv)[:, None]
    dsel = per_vert[rows, order] + 1.0          # +1 voxel to avoid div0
    # RELATIVE falloff: weight_i = (d_nearest / d_i)^power, and cut any bone
    # farther than cutoff x the nearest. The absolute 1/d^2 falloff let arm
    # bones keep meaningful weight deep into the chest of a fat torso (arm
    # rotation dragged the whole upper body -- seen in the first render).
    dmin = dsel[:, :1]
    wsel = np.power(dmin / dsel, power)
    wsel[dsel > dmin * cutoff] = 0.0
    wsel[dsel >= 1e8] = 0.0
    ssum = wsel.sum(1, keepdims=True)
    ssum[ssum <= 0] = 1.0
    wsel /= ssum

    # --- surface Laplacian smoothing of the weights -----------------------
    # Hard geodesic boundaries deform like segmentation edges (creased
    # shoulders in the second render). The production voxel-binding workflow
    # always smooths the baked weights over the mesh; do the same here.
    try:
        import scipy.sparse as sp
        W = np.zeros((nv, NB), dtype=np.float32)
        W[rows, order] = wsel.astype(np.float32)
        ne = len(me.edges)
        e = np.empty((ne, 2), dtype=np.int64)
        me.edges.foreach_get("vertices", e.ravel())
        data = np.ones(ne * 2, dtype=np.float32)
        A = sp.coo_matrix((data, (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])),
                          shape=(nv, nv)).tocsr()
        deg = np.asarray(A.sum(1)).ravel()
        deg[deg == 0] = 1.0
        for _ in range(int(smooth_iters)):
            W = 0.5 * W + 0.5 * (A @ W) / deg[:, None]
        # prune back to k influences + renormalize
        order = np.argsort(-W, axis=1)[:, :k_influences]
        wsel = W[rows, order].astype(np.float64)
        ssum = wsel.sum(1, keepdims=True)
        ssum[ssum <= 0] = 1.0
        wsel /= ssum
        log(f"VOXSKIN smoothed weights ({smooth_iters} iters)")
    except Exception as se:  # noqa: BLE001
        log(f"VOXSKIN smoothing skipped ({se})")

    log("VOXSKIN weights done "
        f"(bones={NB} k={k_influences} orphan={(wsel.sum(1) <= 0).sum()})")
    return bone_names, (order, wsel)


def apply_to_object(mesh_obj, bone_names, packed):
    """Replace mesh_obj's vertex groups with the voxel weights."""
    order, wsel = packed
    mesh_obj.vertex_groups.clear()
    groups = [mesh_obj.vertex_groups.new(name=n) for n in bone_names]
    nv = order.shape[0]
    for i in range(nv):
        for j in range(order.shape[1]):
            w = float(wsel[i, j])
            if w > 1e-5:
                groups[int(order[i, j])].add([i], w, "REPLACE")
