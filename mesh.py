"""Build a watertight, printable terrain solid from a reprojected DEM."""
import numpy as np
import rasterio
import trimesh


def _downsample(arr: np.ndarray, max_dim: int):
    """Shrink a 2D array so its largest dimension is <= max_dim, by block averaging."""
    rows, cols = arr.shape
    largest = max(rows, cols)
    if largest <= max_dim:
        return arr, 1
    factor = int(np.ceil(largest / max_dim))
    trimmed_rows = (rows // factor) * factor
    trimmed_cols = (cols // factor) * factor
    arr = arr[:trimmed_rows, :trimmed_cols]
    arr = arr.reshape(trimmed_rows // factor, factor, trimmed_cols // factor, factor)
    arr = arr.mean(axis=(1, 3))
    return arr, factor


def build_terrain_mesh(dem_path: str, footprint_mm: float, exaggeration: float,
                        base_thickness_mm: float, max_grid: int = 250):
    """Build a terrain solid from a UTM-reprojected GeoTIFF.

    Returns (trimesh.Trimesh, metadata dict). metadata carries everything
    the route builder needs to land on the same surface with no manual
    alignment: origin_x/y (UTM), horizontal_scale, vertical_scale,
    min_elev, base_thickness_mm.
    """
    with rasterio.open(dem_path) as src:
        band = src.read(1).astype(np.float64)
        nodata = src.nodata
        if nodata is not None:
            band[band == nodata] = np.nan
        # fill any gaps with nearest-neighbour style average so the mesh stays solid
        if np.isnan(band).any():
            finite_mean = np.nanmean(band)
            band = np.where(np.isnan(band), finite_mean, band)

        transform = src.transform
        rows, cols = band.shape

        band, factor = _downsample(band, max_grid)
        rows, cols = band.shape

        # pixel centre coordinates in UTM metres, accounting for any downsampling
        col_idx = np.arange(cols) * factor + factor / 2.0
        row_idx = np.arange(rows) * factor + factor / 2.0
        xs = transform.c + transform.a * col_idx
        ys = transform.f + transform.e * row_idx
        xx, yy = np.meshgrid(xs, ys)

        min_x, max_x = float(xs.min()), float(xs.max())
        min_y, max_y = float(ys.min()), float(ys.max())
        width_m = max_x - min_x
        height_m = max_y - min_y

        min_elev = float(np.min(band))
        max_elev = float(np.max(band))

    horizontal_scale = footprint_mm / max(width_m, height_m)
    vertical_scale = horizontal_scale * exaggeration

    # scaled, origin-anchored coordinates (mm)
    X = (xx - min_x) * horizontal_scale
    # flip Y so north is +Y in the model (image rows increase downward / southward)
    Y = (yy - min_y) * horizontal_scale
    Z_top = base_thickness_mm + (band - min_elev) * vertical_scale

    verts_top = np.column_stack([X.ravel(), Y.ravel(), Z_top.ravel()])
    verts_bottom = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])

    def idx(r, c):
        return r * cols + c

    faces = []
    # top surface (upward-facing normals)
    for r in range(rows - 1):
        for c in range(cols - 1):
            a, b, cc, d = idx(r, c), idx(r, c + 1), idx(r + 1, c), idx(r + 1, c + 1)
            faces.append([a, cc, b])
            faces.append([b, cc, d])
    top_face_count = len(faces)

    # bottom surface (downward-facing normals) — same indices offset into the bottom block
    n = rows * cols
    for r in range(rows - 1):
        for c in range(cols - 1):
            a, b, cc, d = idx(r, c), idx(r, c + 1), idx(r + 1, c), idx(r + 1, c + 1)
            faces.append([n + a, n + b, n + cc])
            faces.append([n + b, n + d, n + cc])

    # side walls around the perimeter
    def wall(i0, i1):
        # i0, i1: two adjacent top vertex indices along an edge, winding chosen
        # so the wall faces outward
        faces.append([i0, i1, n + i0])
        faces.append([i1, n + i1, n + i0])

    for c in range(cols - 1):  # north edge (row 0)
        wall(idx(0, c + 1), idx(0, c))
    for c in range(cols - 1):  # south edge (last row)
        wall(idx(rows - 1, c), idx(rows - 1, c + 1))
    for r in range(rows - 1):  # west edge (col 0)
        wall(idx(r, 0), idx(r + 1, 0))
    for r in range(rows - 1):  # east edge (last col)
        wall(idx(r + 1, cols - 1), idx(r, cols - 1))

    vertices = np.vstack([verts_top, verts_bottom])
    faces = np.array(faces)

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
    mesh.fix_normals()

    metadata = {
        "origin_x": min_x,
        "origin_y": min_y,
        "horizontal_scale": horizontal_scale,
        "vertical_scale": vertical_scale,
        "min_elev": min_elev,
        "max_elev": max_elev,
        "width_m": width_m,
        "height_m": height_m,
        "base_thickness_mm": base_thickness_mm,
        "model_width_mm": width_m * horizontal_scale,
        "model_height_mm": height_m * horizontal_scale,
        "model_relief_mm": (max_elev - min_elev) * vertical_scale,
        "model_total_height_mm": base_thickness_mm + (max_elev - min_elev) * vertical_scale,
        "is_watertight": bool(mesh.is_watertight),
    }
    return mesh, metadata
