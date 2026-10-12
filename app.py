import asyncio
import hmac
import json
import os
import shutil
import tempfile
import zipfile

from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from geo import fetch_dem, reproject_to_utm
from mesh import build_terrain_mesh, write_stl
from route import build_route_mesh

# OpenTopography global DEMs offered in the UI (all ~30 m)
DEM_TYPES = ("COP30", "NASADEM", "AW3D30", "SRTMGL1")

# Hosting settings, all optional (environment variables):
#   OPENTOPO_API_KEY  server-side key, so visitors don't need their own
#   ACCESS_CODE       if set, visitors must enter this code to generate
#   MAX_GRID          cap on terrain grid points per side (bounds memory use
#                     on small instances; 0/unset = no cap)
SERVER_API_KEY = os.environ.get("OPENTOPO_API_KEY", "").strip()
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()
MAX_GRID_CAP = int(os.environ.get("MAX_GRID", "0") or 0)

# Mesh building is memory-hungry, so run one job at a time; others wait.
_job_slot = asyncio.Semaphore(1)

app = FastAPI(title="Terrain + Route STL Generator")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/config")
def config():
    """Tell the page which fields it needs to show."""
    return {"server_api_key": bool(SERVER_API_KEY), "access_code_required": bool(ACCESS_CODE)}


@app.post("/api/generate")
async def generate(
    bbox: str = Form(...),  # JSON: [west, south, east, north]
    api_key: str = Form(""),
    access_code: str = Form(""),
    footprint_mm: float = Form(200.0),
    exaggeration: float = Form(2.0),
    base_thickness_mm: float = Form(2.0),
    vertical_offset_m: float = Form(4.0),
    tube_diameter_mm: float = Form(1.2),  # tube diameter, or line width
    route_style: str = Form("tube"),       # "tube" or "line"
    line_height_mm: float = Form(1.0),
    max_grid: int = Form(0),  # 0 = full DEM resolution
    demtype: str = Form("COP30"),
    gpx: UploadFile = File(...),
):
    if ACCESS_CODE and not hmac.compare_digest(access_code.strip().encode(), ACCESS_CODE.encode()):
        raise HTTPException(403, "Wrong access code")
    try:
        bbox_vals = [float(v) for v in json.loads(bbox)]
        if len(bbox_vals) != 4:
            raise ValueError
    except Exception:
        raise HTTPException(400, "bbox must be a JSON array [west, south, east, north]")
    if route_style not in ("tube", "line"):
        raise HTTPException(400, "route_style must be 'tube' or 'line'")
    if demtype not in DEM_TYPES:
        raise HTTPException(400, f"demtype must be one of {', '.join(DEM_TYPES)}")
    key = api_key.strip() or SERVER_API_KEY
    if not key:
        raise HTTPException(400, "Enter an OpenTopography API key")
    if MAX_GRID_CAP:
        max_grid = MAX_GRID_CAP if max_grid <= 0 else min(max_grid, MAX_GRID_CAP)

    gpx_bytes = await gpx.read()
    params = dict(bbox=tuple(bbox_vals), api_key=key, footprint_mm=footprint_mm,
                  exaggeration=exaggeration, base_thickness_mm=base_thickness_mm,
                  vertical_offset_m=vertical_offset_m, tube_diameter_mm=tube_diameter_mm,
                  route_style=route_style, line_height_mm=line_height_mm,
                  max_grid=max_grid, demtype=demtype)

    workdir = tempfile.mkdtemp(prefix="terrain_")
    try:
        async with _job_slot:
            zip_path = await run_in_threadpool(_build_zip, workdir, gpx_bytes, **params)
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(500, f"Generation failed: {e}")
    # delete the temp files once the zip has been sent
    return FileResponse(zip_path, filename="terrain_and_route.zip", media_type="application/zip",
                        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True))


def _build_zip(workdir, gpx_bytes, *, bbox, api_key, footprint_mm, exaggeration,
               base_thickness_mm, vertical_offset_m, tube_diameter_mm, route_style,
               line_height_mm, max_grid, demtype):
    gpx_path = os.path.join(workdir, "route.gpx")
    with open(gpx_path, "wb") as f:
        f.write(gpx_bytes)

    raw_dem = os.path.join(workdir, "dem_raw.tif")
    utm_dem = os.path.join(workdir, "dem_utm.tif")

    fetch_dem(bbox, api_key, raw_dem, demtype=demtype)
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
        style=route_style,
        line_width_mm=tube_diameter_mm,
        line_height_mm=line_height_mm,
    )

    terrain_stl = os.path.join(workdir, "terrain.stl")
    route_stl = os.path.join(workdir, "route.stl")
    write_stl(terrain_stl, terrain_mesh.vertices, terrain_mesh.faces)
    write_stl(route_stl, route_mesh.vertices, route_mesh.faces)

    grid_cols = metadata["grid_elev"].shape[1]
    grid_rows = metadata["grid_elev"].shape[0]
    summary_lines = [
        "Terrain + Route generation summary",
        "===================================",
        f"Elevation source: {demtype}",
        f"UTM zone EPSG: {utm_epsg}",
        f"Terrain grid: {grid_cols} x {grid_rows} points"
        f" ({metadata['width_m'] / max(grid_cols - 1, 1):.0f} m /"
        f" {metadata['model_width_mm'] / max(grid_cols - 1, 1):.2f} mm spacing)",
        f"Area covered: {metadata['width_m']:.1f} m x {metadata['height_m']:.1f} m",
        f"Elevation range: {metadata['min_elev']:.1f} - {metadata['max_elev']:.1f} m",
        f"Horizontal scale: {metadata['horizontal_scale']:.6f} (mm per real metre)",
        f"Vertical scale: {metadata['vertical_scale']:.6f} (mm per real metre, exaggeration x{exaggeration})",
        f"Model footprint: {metadata['model_width_mm']:.1f} x {metadata['model_height_mm']:.1f} mm",
        f"Model relief height: {metadata['model_relief_mm']:.2f} mm",
        f"Base thickness: {metadata['base_thickness_mm']:.2f} mm",
        f"Total terrain height: {metadata['model_total_height_mm']:.2f} mm",
        *([f"Route style: tube, {tube_diameter_mm} mm diameter",
           f"Route vertical offset above terrain: {vertical_offset_m} m (real-world, pre-scale)"]
          if route_style == "tube" else
          [f"Route style: flat line, {tube_diameter_mm} mm wide, "
           f"{line_height_mm} mm above the terrain (sunk 1 mm into it)"]),
        "",
        "terrain.stl and route.stl share the same origin and scale,",
        "so they should already be aligned when loaded together in a slicer",
        "or Blender at the same position — no manual rotation or nudging needed.",
    ]
    summary_path = os.path.join(workdir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    zip_path = os.path.join(workdir, "terrain_and_route.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(terrain_stl, "terrain.stl")
        zf.write(route_stl, "route.stl")
        zf.write(summary_path, "summary.txt")
    return zip_path
