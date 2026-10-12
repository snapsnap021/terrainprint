"""Build a watertight, printable terrain solid from a reprojected DEM."""
import numpy as np
import rasterio
import trimesh
from scipy import ndimage


def _trim_to_valid(valid: np.ndarray):
    """Return (r0, r1, c0, c1) of a rectangle inside the grid whose border
    rows/columns contain only valid cells.

    After reprojection the data covers a slightly rotated quadrilateral, so
    the grid has empty wedges along its edges. Peel off whichever edge row or
    column is missing the largest share of its cells until all four edges
    are clean -- that leaves straight, vertical walls on the printed model."""
    r0, r1, c0, c1 = 0, valid.shape[0], 0, valid.shape[1]
    while r1 - r0 > 2 and c1 - c0 > 2:
        sub = valid[r0:r1, c0:c1]
        bad = {
            "top": 1 - sub[0].mean(), "bottom": 1 - sub[-1].mean(),
            "left": 1 - sub[:, 0].mean(), "right": 1 - sub[:, -1].mean(),
        }
        edge, worst = max(bad.items(), key=lambda kv: kv[1])
        if worst == 0:
            break
        if edge == "top":
            r0 += 1
        elif edge == "bottom":
            r1 -= 1
        elif edge == "left":
            c0 += 1
        else:
            c1 -= 1
    return r0, r1, c0, c1


def _downsample(arr: np.ndarray, max_dim: int):
    """Shrink a 2D array so its largest dimension is <= max_dim, by block averaging."""
    rows, cols = arr.shape
    largest = max(rows, cols)
    if max_dim <= 0 or largest <= max_dim:  # 0 = keep full DEM resolution
        return arr, 1
    factor = int(np.ceil(largest / max_dim))
    trimmed_rows = (rows // factor) * factor
    trimmed_cols = (cols // factor) * factor
    arr = arr[:trimmed_rows, :trimmed_cols]
    arr = arr.reshape(trimmed_rows // factor, factor, trimmed_cols // factor, factor)
    arr = arr.mean(axis=(1, 3))
    return arr, factor


def build_terrain_mesh(dem_path: str, footprint_mm: float, exaggeration: float,
                        base_thickness_mm: float, max_grid: int = 0):
    """Build a terrain solid from a UTM-reprojected GeoTIFF.

    Returns (trimesh.Trimesh, metadata dict). metadata carries everything
    the route builder needs to land on the same surface with no manual
    alignment: origin_x/y (UTM), horizontal_scale, vertical_scale,
    min_elev, base_thickness_mm.
    """
    with rasterio.open(dem_path) as src:
        band = src.read(1).astype(np.float64)
        nodata = src.nodata
        if nodata is not None and not np.isnan(nodata):
            band[band == nodata] = np.nan
        transform = src.transform

        # crop away the empty wedges left by reprojection so the model's
        # sides are straight and its lowest point is real terrain
        valid = np.isfinite(band)
        if not valid.any():
            raise ValueError("The elevation data for this area is empty")
        r0, r1, c0, c1 = _trim_to_valid(valid)
        band = band[r0:r1, c0:c1]
        transform = transform * rasterio.Affine.translation(c0, r0)

        # any remaining interior gaps (rare voids in the DEM): copy the
        # nearest real elevation rather than an area-wide average
        missing = ~np.isfinite(band)
        if missing.any():
            idx = ndimage.distance_transform_edt(missing, return_distances=False, return_indices=True)
            band = band[tuple(idx)]

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
    n = rows * cols

    # top surface (upward-facing normals), built vectorised: for each grid
    # cell a=(r,c), b=(r,c+1), cc=(r+1,c), d=(r+1,c+1)
    R, C = np.meshgrid(np.arange(rows - 1), np.arange(cols - 1), indexing="ij")
    a = (R * cols + C).ravel()
    b, cc = a + 1, a + cols
    d = cc + 1
    top_faces = np.concatenate([np.column_stack([a, cc, b]), np.column_stack([b, cc, d])])

    # perimeter of the grid, counter-clockwise seen from above (north = row 0):
    # south edge west->east, east edge south->north, north edge east->west,
    # west edge north->south; corners appear once
    last_r, last_c = rows - 1, cols - 1
    perim = np.concatenate([
        last_r * cols + np.arange(0, last_c),              # south row
        np.arange(last_r, 0, -1) * cols + last_c,          # east col
        0 * cols + np.arange(last_c, 0, -1),               # north row
        np.arange(0, last_r) * cols + 0,                   # west col
    ])
    m = len(perim)

    # flat base: only the perimeter is needed (plus a centre point for a
    # triangle fan) -- a full-resolution bottom grid would double the file
    # size for a face nobody sees
    verts_bottom = np.column_stack([X.ravel()[perim], Y.ravel()[perim], np.zeros(m)])
    centre = np.array([[X.mean(), Y.mean(), 0.0]])
    bot = n + np.arange(m)          # bottom vertex for each perimeter vertex
    centre_idx = n + m

    nxt = np.roll(np.arange(m), -1)
    top_k, top_k1 = perim, perim[nxt]
    bot_k, bot_k1 = bot, bot[nxt]
    wall_faces = np.concatenate([
        np.column_stack([top_k, bot_k, bot_k1]),
        np.column_stack([top_k, bot_k1, top_k1]),
    ])
    base_faces = np.column_stack([np.full(m, centre_idx), bot_k1, bot_k])

    vertices = np.vstack([verts_top, verts_bottom, centre])
    faces = np.concatenate([top_faces, wall_faces, base_faces])

    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

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
        # the exact (downsampled) grid the top surface was built from, so the
        # route can be draped on the printed surface rather than the raw DEM
        "grid_xs": xs,
        "grid_ys": ys,
        "grid_elev": band,
    }
    return mesh, metadata
