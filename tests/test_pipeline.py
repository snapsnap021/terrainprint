"""End-to-end checks on synthetic data (no network needed).

Run with:  python -m pytest tests
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from geo import reproject_to_utm
from mesh import build_terrain_mesh
from route import build_route_mesh

BBOX = (17.38, -28.85, 17.505, -28.685)   # ~12 x 18 km


@pytest.fixture(params=[(-9999, "float32"), (None, "float32"), (-32768, "int16")],
                ids=["nodata", "no-nodata", "int16"])
def utm_dem(tmp_path, request):
    nodata, dtype = request.param
    w, s, e, n = BBOX
    rows, cols = 300, 230
    lon, lat = np.meshgrid(np.linspace(w, e, cols), np.linspace(n, s, rows))
    z = 300 + 100 * np.sin(lon * 90) + 50 * np.cos(lat * 80)
    raw, utm = tmp_path / "raw.tif", tmp_path / "utm.tif"
    with rasterio.open(raw, "w", driver="GTiff", height=rows, width=cols, count=1, dtype=dtype,
                       crs="EPSG:4326", transform=from_bounds(w, s, e, n, cols, rows),
                       nodata=nodata) as dst:
        dst.write(z.astype(dtype), 1)
    epsg = reproject_to_utm(str(raw), str(utm))
    return str(utm), epsg, z


@pytest.fixture
def gpx(tmp_path):
    lons = np.linspace(17.40, 17.48, 60)
    lats = -28.80 + 0.05 * np.sin(np.linspace(0, 6, 60))
    pts = "".join(f'<trkpt lat="{la}" lon="{lo}"></trkpt>' for lo, la in zip(lons, lats))
    path = tmp_path / "route.gpx"
    path.write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
                    f"{pts}</trkseg></trk></gpx>")
    return str(path)


@pytest.mark.parametrize("max_grid", [0, 80])
def test_terrain_is_clean_solid(utm_dem, max_grid):
    path, _, z = utm_dem
    mesh, meta = build_terrain_mesh(path, footprint_mm=200, exaggeration=2,
                                    base_thickness_mm=2, max_grid=max_grid)
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0
    # no reprojection gaps leaking in as 0 m or filler values
    assert meta["min_elev"] >= z.min() - 1
    # longest side matches the footprint, base sits on z=0
    lo, hi = mesh.bounds
    assert lo[2] == 0 and max(hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx(200)
    # every base vertex lies on one of the four straight walls (centre point excepted)
    base = mesh.vertices[mesh.vertices[:, 2] == 0][:-1]
    on_wall = (np.isclose(base[:, 0], lo[0]) | np.isclose(base[:, 0], hi[0])
               | np.isclose(base[:, 1], lo[1]) | np.isclose(base[:, 1], hi[1]))
    assert on_wall.all()


@pytest.mark.parametrize("style", ["tube", "line"])
def test_route_lies_on_terrain(utm_dem, gpx, style):
    path, epsg, _ = utm_dem
    terrain, meta = build_terrain_mesh(path, 200, 2, 2)
    route = build_route_mesh(gpx, path, epsg, meta, style=style)
    lo, hi = terrain.bounds
    assert (route.bounds[0][:2] >= lo[:2] - 1).all() and (route.bounds[1][:2] <= hi[:2] + 1).all()
    if style == "line":
        assert all(b.is_watertight and b.volume > 0 for b in route.split(only_watertight=False))
