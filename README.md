# Terrain + Route STL Generator

A local web app that replaces the whole TouchTerrain → QGIS → Blender chain
with one step: draw your area on a map, upload a GPX route, and get back
two STL files (`terrain.stl` and `route.stl`) that are **already aligned**
— same origin, same horizontal and vertical scale, no manual rotation or
nudging needed, because both are built from the same reprojected elevation
grid instead of being round-tripped through several tools with their own
coordinate handling.

## What it does differently from the manual workflow

- Fetches the DEM (Copernicus GLO-30) for your drawn area directly from
  OpenTopography.
- Reprojects it to the correct local UTM zone automatically (detected from
  the area's centre point) — no manual EPSG lookup.
- Builds the terrain as a solid, watertight mesh directly from the
  elevation grid (top surface + flat base + walls), with a horizontal
  scale factor computed from your target footprint size and a vertical
  scale factor derived from that plus your chosen exaggeration — no
  TouchTerrain export/re-measure/back-calculate cycle.
- Samples the GPX route's elevation from the *same* reprojected grid, and
  scales and positions it with the *exact same* factors and origin as the
  terrain — so there's no separate rotation step to get wrong, and no
  drape/offset/scale chain to debug across three different programs.
- Builds the route as a solid tube (cylinders + joint spheres), ready to
  print on its own.

## Setup

1. Install Python 3.10+.
2. In this folder, create a virtual environment and install dependencies:
   ```
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
   `rasterio` pulls in GDAL. On Windows, if the plain `pip install` fails to
   find a wheel, try `pip install rasterio --only-binary=:all:`, or install
   via conda instead (`conda install -c conda-forge rasterio`).
3. Get a free OpenTopography API key at
   https://portal.opentopography.org (account → myOpenTopo → Request API key).
   You'll paste this into the app itself — it's kept in your browser's
   local storage, not stored by the app.

## Running it

**Windows, no terminal needed:** double-click `start.bat`. The first run
sets everything up (a few minutes); after that it starts in seconds and
opens the app in your browser. Keep its window open while you use the app.

**Any platform, from a terminal:**

```
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000 in your browser.

## Using it

1. **Draw your area** — use the rectangle tool (top-left of the map) to draw
   a box over the terrain you want. Keep it a reasonable size: OpenTopography's
   free tier caps how large an area you can request in one call (roughly
   125,000,000 m², i.e. about 11km × 11km, though this can change — if a
   request fails with an area-too-large error, draw a smaller box).
2. **Upload your GPX** — exported from Strava (activity → ••• menu → Export GPX)
   or any GPS watch/app.
3. **Enter your API key.**
4. **Set print options:**
   - *Footprint* — the longest side of the printed terrain, in mm (your
     Bambu P2S bed fits about 240mm comfortably).
   - *Vertical exaggeration* — 1.0 is true-to-scale (often looks flat);
     1.5–3.0 usually looks right for mountainous terrain.
   - *Base thickness* — solid base under the terrain, in mm.
   - *Route tube diameter* — thickness of the printed route line, in mm.
     1–1.5mm prints reliably on a 0.4mm nozzle.
   - *Route height above terrain* — a small real-world offset (metres)
     so the route sits proud of the surface rather than being buried in it.
   - *Terrain mesh detail* — grid resolution; higher is more detailed but
     slower to generate and slice. 250 is a good default; drop it if
     generation is slow or Bambu Studio struggles with the file.
5. Click **Generate STL files**. This can take anywhere from a few seconds
   to a couple of minutes depending on area size and mesh detail — it's
   downloading real elevation data and building the mesh server-side.
6. You'll get a `terrain_and_route.zip` containing `terrain.stl`,
   `route.stl`, and `summary.txt` (the scale factors and extents used, for
   your records).

## Printing

- Load both STLs into Bambu Studio **at the same position** (don't
  re-center or auto-arrange them relative to each other) — they share a
  common origin, so they'll already sit correctly together.
- With an AMS: assign each object a different filament colour and print
  together.
- Without an AMS: print them separately, then glue (see the earlier guide
  for finishing tips — filler primer, light sand, dry-brush paint on the
  terrain; a glossy/saturated colour on the route for contrast).

## Troubleshooting

- **"OpenTopography request failed"** — usually an invalid API key, an area
  that's too large, or a temporary outage. The error message includes
  OpenTopography's own response text.
- **Route looks tiny or huge relative to terrain** — shouldn't happen with
  this app (both are scaled from the same real-world extent), but if it
  does, check `summary.txt` — the `horizontal_scale` and `vertical_scale`
  values should be consistent with the footprint and exaggeration you set.
- **Generation is slow** — lower *Terrain mesh detail*, or draw a smaller
  area.
- **Terrain mesh not watertight in summary.txt** — very rare (can happen
  with a DEM tile that's mostly nodata); try a slightly different/larger
  area.

## Files

- `app.py` — FastAPI server and the `/api/generate` endpoint.
- `geo.py` — DEM fetch (OpenTopography) and UTM reprojection (rasterio).
- `mesh.py` — terrain solid mesh construction from the elevation grid.
- `route.py` — GPX parsing, elevation sampling, and tube mesh construction.
- `static/` — the browser frontend (Leaflet map + form).
- `test_mesh_logic.py` — a standalone check of the terrain mesh math
  (watertightness, scale, orientation) that doesn't require the full
  dependency stack; run with `python3 test_mesh_logic.py`.
