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


def _drop_close_points(pts: np.ndarray, min_dist: float) -> np.ndarray:
    """Remove consecutive points closer than min_dist (avoids zero-length
    cylinders and needless triangles where the GPS sampled densely)."""
    keep = [pts[0]]
    for p in pts[1:]:
        if np.linalg.norm(p - keep[-1]) >= min_dist:
            keep.append(p)
    if len(keep) == 1 and len(pts) > 1:
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


def build_route_mesh(gpx_path: str, dem_path: str, utm_epsg: int, metadata: dict,
                     vertical_offset_m: float = 4.0, tube_radius_mm: float = 0.6,
                     sections: int = 12):
    """Build the route tube in the same frame as build_terrain_mesh().

    Elevation comes from the terrain's own grid (metadata['grid_*']), not the
    GPX <ele> values, so the tube follows the printed surface exactly.
    dem_path is accepted for API symmetry but the grid in metadata is used.
    """
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
                base + (e - min_elev + vertical_offset_m) * v,
            ])
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
