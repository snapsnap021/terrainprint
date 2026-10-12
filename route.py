"""Turn a GPX track into a printable tube that sits on the terrain mesh."""
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
from rasterio.warp import transform as warp_transform
from scipy.interpolate import RegularGridInterpolator


def parse_gpx(gpx_path: str):
    """Return a list of (lon, lat) arrays, one per track segment / route.

    Namespace-agnostic so GPX 1.0 and 1.1 files both work."""
    root = ET.parse(gpx_path).getroot()

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    segments = []
    for el in root.iter():
        if local(el.tag) in ("trkseg", "rte"):
            pts = [(float(p.get("lon")), float(p.get("lat")))
                   for p in el if local(p.tag) in ("trkpt", "rtept")]
            if len(pts) >= 2:
                segments.append(np.array(pts))
    if not segments:
        raise ValueError("GPX file contains no track or route with at least 2 points")
    return segments


def _drop_close_points(pts: np.ndarray, min_dist: float, dims: int = 3) -> np.ndarray:
    """Remove consecutive points closer than min_dist (avoids zero-length
    cylinders and needless triangles where the GPS sampled densely).
    dims=2 measures horizontal distance only."""
    keep = [pts[0]]
    for p in pts[1:]:
        if np.linalg.norm(p[:dims] - keep[-1][:dims]) >= min_dist:
            keep.append(p)
    if len(keep) == 1 and len(pts) > 1 and np.linalg.norm(pts[-1][:dims] - keep[0][:dims]) > 0:
        keep.append(pts[-1])
    return np.array(keep)


def _split_inside(mask: np.ndarray):
    """Yield (start, stop) index ranges where mask is continuously True."""
    start = None
    for i, inside in enumerate(mask):
        if inside and start is None:
            start = i
        elif not inside and start is not None:
            yield start, i
            start = None
    if start is not None:
        yield start, len(mask)


def _line_mesh(pts: np.ndarray, width_mm: float, height_mm: float, embed_mm: float):
    """Flat-topped band with vertical sides following pts (N x 3, mm).

    Top sits height_mm above each point's surface height, bottom embed_mm
    below it (so the band never floats where the terrain rises between GPS
    points). Corners are mitred, with the mitre length capped so hairpin
    turns don't throw out long spikes."""
    xy = pts[:, :2]
    seg = np.diff(xy, axis=0)
    seg /= np.linalg.norm(seg, axis=1, keepdims=True)
    seg_n = np.column_stack([-seg[:, 1], seg[:, 0]])          # left-hand normals

    # per-vertex normal: ends use their one segment, interior vertices the
    # average of both, scaled so the band keeps its width through the corner
    vn = np.empty_like(xy)
    vn[0], vn[-1] = seg_n[0], seg_n[-1]
    avg = seg_n[:-1] + seg_n[1:]
    norm = np.linalg.norm(avg, axis=1, keepdims=True)
    avg = np.where(norm > 1e-9, avg / np.maximum(norm, 1e-9), seg_n[:-1])
    cos_half = np.clip(np.sum(avg * seg_n[:-1], axis=1, keepdims=True), 1 / 3, 1)
    vn[1:-1] = avg / cos_half                                 # mitre, capped at 3x
    off = vn * (width_mm / 2)

    z_top = pts[:, 2] + height_mm
    z_bot = np.maximum(pts[:, 2] - embed_mm, 0.0)
    left, right = xy + off, xy - off
    # 4 vertices per point: left-top, right-top, left-bottom, right-bottom
    verts = np.stack([
        np.column_stack([left, z_top]), np.column_stack([right, z_top]),
        np.column_stack([left, z_bot]), np.column_stack([right, z_bot]),
    ], axis=1).reshape(-1, 3)

    n = len(pts)
    i = np.arange(n - 1) * 4
    j = i + 4
    LT, RT, LB, RB = 0, 1, 2, 3
    quads = [
        (i + LT, i + RT, j + RT, j + LT),    # top
        (i + RB, i + LB, j + LB, j + RB),    # bottom
        (i + LB, i + LT, j + LT, j + LB),    # left side
        (i + RT, i + RB, j + RB, j + RT),    # right side
    ]
    faces = []
    for a, b, c, d in quads:
        faces.append(np.column_stack([a, b, c]))
        faces.append(np.column_stack([a, c, d]))
    s0, s1 = 0, (n - 1) * 4                  # end caps
    faces.append(np.array([[s0 + LT, s0 + LB, s0 + RB], [s0 + LT, s0 + RB, s0 + RT],
                           [s1 + LT, s1 + RT, s1 + RB], [s1 + LT, s1 + RB, s1 + LB]]))
    mesh = trimesh.Trimesh(verts, np.concatenate(faces), process=False)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def build_route_mesh(gpx_path: str, dem_path: str, utm_epsg: int, metadata: dict,
                     vertical_offset_m: float = 4.0, tube_radius_mm: float = 0.6,
                     sections: int = 12, style: str = "tube",
                     line_width_mm: float = 1.2, line_height_mm: float = 1.0,
                     line_embed_mm: float = 1.0):
    """Build the route in the same frame as build_terrain_mesh().

    style="tube": round tube (cylinders + joint spheres) whose centre is
    vertical_offset_m (real-world) above the surface.
    style="line": flat-topped band line_width_mm wide, rising line_height_mm
    above the surface and sunk line_embed_mm into it.

    Elevation comes from the terrain's own grid (metadata['grid_*']), not the
    GPX <ele> values, so the route follows the printed surface exactly.
    dem_path is accepted for API symmetry but the grid in metadata is used.
    """
    if style not in ("tube", "line"):
        raise ValueError(f"Unknown route style {style!r}")
    xs, ys, elev = metadata["grid_xs"], metadata["grid_ys"], metadata["grid_elev"]
    # RegularGridInterpolator needs ascending axes; DEM rows run north->south
    if ys[0] > ys[-1]:
        ys, elev = ys[::-1], elev[::-1, :]
    sampler = RegularGridInterpolator((ys, xs), elev, method="linear")

    h = metadata["horizontal_scale"]
    v = metadata["vertical_scale"]
    x0, y0 = metadata["origin_x"], metadata["origin_y"]
    base = metadata["base_thickness_mm"]
    min_elev = metadata["min_elev"]
    z_offset_m = vertical_offset_m if style == "tube" else 0.0

    parts = []
    for seg in parse_gpx(gpx_path):
        ux, uy = warp_transform("EPSG:4326", f"EPSG:{utm_epsg}", seg[:, 0], seg[:, 1])
        ux, uy = np.asarray(ux), np.asarray(uy)
        inside = (ux >= xs.min()) & (ux <= xs.max()) & (uy >= ys.min()) & (uy <= ys.max())

        for a, b in _split_inside(inside):
            if b - a < 2:
                continue
            e = sampler(np.column_stack([uy[a:b], ux[a:b]]))
            pts = np.column_stack([
                (ux[a:b] - x0) * h,
                (uy[a:b] - y0) * h,
                base + (e - min_elev + z_offset_m) * v,
            ])

            if style == "line":
                pts = _drop_close_points(pts, line_width_mm * 0.5, dims=2)
                if len(pts) >= 2:
                    parts.append(_line_mesh(pts, line_width_mm, line_height_mm, line_embed_mm))
                continue

            pts = _drop_close_points(pts, tube_radius_mm * 0.5)
            for p in pts:
                sphere = trimesh.creation.icosphere(subdivisions=1, radius=tube_radius_mm)
                sphere.apply_translation(p)
                parts.append(sphere)
            for p, q in zip(pts[:-1], pts[1:]):
                parts.append(trimesh.creation.cylinder(
                    radius=tube_radius_mm, segment=[p, q], sections=sections))

    if not parts:
        raise ValueError("No part of the GPX route falls inside the selected area")
    return trimesh.util.concatenate(parts)
