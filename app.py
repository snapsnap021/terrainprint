import json
import os
import shutil
import tempfile
import zipfile

from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from geo import fetch_dem, reproject_to_utm
from mesh import build_terrain_mesh
from route import build_route_mesh

# OpenTopography global DEMs offered in the UI (all ~30 m)
DEM_TYPES = ("COP30", "NASADEM", "AW3D30", "SRTMGL1")

app = FastAPI(title="Terrain + Route STL Generator")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/generate")
async def generate(
    bbox: str = Form(...),  # JSON: [west, south, east, north]
    api_key: str = Form(...),
    footprint_mm: float = Form(200.0),
    exaggeration: float = Form(2.0),
    base_thickness_mm: float = Form(2.0),
    vertical_offset_m: float = Form(4.0),
    tube_diameter_mm: float = Form(1.2),
    max_grid: int = Form(0),  # 0 = full DEM resolution
    demtype: str = Form("COP30"),
    gpx: UploadFile = File(...),
):
    try:
        bbox_vals = json.loads(bbox)
        if len(bbox_vals) != 4:
            raise ValueError
    except Exception:
        raise HTTPException(400, "bbox must be a JSON array [west, south, east, north]")
    if demtype not in DEM_TYPES:
        raise HTTPException(400, f"demtype must be one of {', '.join(DEM_TYPES)}")

    workdir = tempfile.mkdtemp(prefix="terrain_")
    try:
        gpx_path = os.path.join(workdir, "route.gpx")
        with open(gpx_path, "wb") as f:
            f.write(await gpx.read())

        raw_dem = os.path.join(workdir, "dem_raw.tif")
        utm_dem = os.path.join(workdir, "dem_utm.tif")

        fetch_dem(tuple(bbox_vals), api_key, raw_dem, demtype=demtype)
        utm_epsg = reproject_to_utm(raw_dem, utm_dem)

        terrain_mesh, metadata = build_terrain_mesh(
            utm_dem,
            footprint_mm=footprint_mm,
            exaggeration=exaggeration,
            base_thickness_mm=base_thickness_mm,
            max_grid=max_grid,
        )

        route_mesh = build_route_mesh(
            gpx_path,
            utm_dem,
            utm_epsg,
            metadata,
            vertical_offset_m=vertical_offset_m,
            tube_radius_mm=tube_diameter_mm / 2.0,
        )

        terrain_stl = os.path.join(workdir, "terrain.stl")
        route_stl = os.path.join(workdir, "route.stl")
        terrain_mesh.export(terrain_stl)
        route_mesh.export(route_stl)

        summary_lines = [
            "Terrain + Route generation summary",
            "===================================",
            f"Elevation source: {demtype}",
            f"UTM zone EPSG: {utm_epsg}",
            f"Terrain grid: {metadata['grid_elev'].shape[1]} x {metadata['grid_elev'].shape[0]} points"
            f" ({metadata['width_m'] / max(metadata['grid_elev'].shape[1] - 1, 1):.0f} m /"
            f" {metadata['model_width_mm'] / max(metadata['grid_elev'].shape[1] - 1, 1):.2f} mm spacing)",
            f"Area covered: {metadata['width_m']:.1f} m x {metadata['height_m']:.1f} m",
            f"Elevation range: {metadata['min_elev']:.1f} - {metadata['max_elev']:.1f} m",
            f"Horizontal scale: {metadata['horizontal_scale']:.6f} (mm per real metre)",
            f"Vertical scale: {metadata['vertical_scale']:.6f} (mm per real metre, exaggeration x{exaggeration})",
            f"Model footprint: {metadata['model_width_mm']:.1f} x {metadata['model_height_mm']:.1f} mm",
            f"Model relief height: {metadata['model_relief_mm']:.2f} mm",
            f"Base thickness: {metadata['base_thickness_mm']:.2f} mm",
            f"Total terrain height: {metadata['model_total_height_mm']:.2f} mm",
            f"Terrain mesh watertight: {metadata['is_watertight']}",
            f"Route vertical offset above terrain: {vertical_offset_m} m (real-world, pre-scale)",
            f"Route tube diameter: {tube_diameter_mm} mm",
            "",
            "terrain.stl and route.stl share the same origin and scale,",
            "so they should already be aligned when loaded together in a slicer",
            "or Blender at the same position — no manual rotation or nudging needed.",
        ]
        summary_path = os.path.join(workdir, "summary.txt")
        with open(summary_path, "w") as f:
            f.write("\n".join(summary_lines))

        zip_path = os.path.join(workdir, "terrain_and_route.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(terrain_stl, "terrain.stl")
            zf.write(route_stl, "route.stl")
            zf.write(summary_path, "summary.txt")

        return FileResponse(zip_path, filename="terrain_and_route.zip",
                             media_type="application/zip")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {e}")
    # Note: workdir is intentionally left for FileResponse to stream from;
    # the OS temp dir will be cleaned up on next reboot. For a long-running
    # server you'd want a background task to remove it after the response.
