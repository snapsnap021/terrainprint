# Handoff notes (read this first)

This project was built in a cloud chat session with no real network access
or Python package installs available, so it's **written and logic-checked,
but not yet run end-to-end with a live server or a real DEM download.**
That's the first thing to do once you're set up locally.

## What's verified
- `test_mesh_logic.py` — standalone numpy-only test of the terrain mesh
  math (watertightness, correct scale factors, outward-facing normals,
  no degenerate faces). Run with `python3 test_mesh_logic.py` — it has no
  dependencies beyond numpy and should pass immediately.
- All `.py` files pass `python3 -m py_compile` (syntax-checked only).

## What's NOT yet verified (do this first in Claude Code)
1. **Dependency install** — `pip install -r requirements.txt` was never
   actually run. `rasterio` pulls in GDAL and can be finicky, especially on
   Windows — if it fails, try `pip install rasterio --only-binary=:all:`
   or a conda environment instead.
2. **A real OpenTopography fetch** — `geo.fetch_dem()` has never hit the
   live API. Worth testing standalone first with a small bbox before
   going through the full UI, e.g.:
   ```python
   from geo import fetch_dem, reproject_to_utm
   fetch_dem((18.60, -33.95, 18.65, -33.90), "YOUR_KEY", "test_dem.tif")
   reproject_to_utm("test_dem.tif", "test_dem_utm.tif")
   ```
3. **The full mesh pipeline against a real DEM** — `mesh.build_terrain_mesh()`
   and `route.build_route_mesh()` have only been tested against synthetic
   data in `test_mesh_logic.py` (which re-implements the terrain math
   standalone, since `trimesh` wasn't installable in that session). Once
   dependencies are installed, run the real functions against a real DEM +
   GPX and check the output STLs actually look right (e.g. open in a
   viewer, or inspect in Blender) before trusting them for a print.
4. **The FastAPI endpoint and frontend** — never hit with a real request.
   Likely candidates for first-run bugs: form field type coercion between
   JS and FastAPI's `Form(...)` params, CORS (shouldn't matter since
   frontend and API are same-origin here), and the temp-file cleanup
   (currently never deletes `workdir` — fine for occasional personal use,
   worth fixing if this gets used a lot).

## Known rough edges / possible follow-ups
- `app.py`'s temp directories under `/tmp` are never cleaned up after the
  response is sent. Low priority for personal use; add a background task
  (`BackgroundTasks` in FastAPI) to `shutil.rmtree` the workdir after the
  response if this bothers you.
- `mesh.py`'s `max_grid` downsampling uses simple block-averaging, which
  is fine for a DEM but means very high `max_grid` values just reflect the
  DEM's native resolution (usually ~30m for Copernicus GLO-30) — there's a
  ceiling on how much real detail you'll get no matter how high you set it.
- OpenTopography's free tier has an area cap (roughly 125,000,000 m² per
  request last I knew, but check their docs/portal — this may have
  changed). If a large bbox request fails, that's the likely cause.
- No automated test hits the live API or does an end-to-end run — worth
  adding one once you've confirmed the manual run works, so future changes
  don't silently break the pipeline.

## Where this came from
This replaces a manual TouchTerrain → QGIS → Blender workflow that kept
hitting rotation and scale-mismatch issues between tools. The design goal
was to do the whole DEM → terrain mesh → route mesh pipeline in one
process, in one consistent coordinate frame, so there's nothing left to
misalign by hand. See `README.md` for the full usage instructions.
