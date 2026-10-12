"""Geo helpers: fetching DEMs and reprojecting to a local metric CRS."""
import math
import requests
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling


def utm_epsg_for_lonlat(lon: float, lat: float) -> int:
    """Return the EPSG code of the UTM zone containing (lon, lat)."""
    zone = int(math.floor((lon + 180) / 6) + 1)
    if lat >= 0:
        return 32600 + zone  # northern hemisphere
    return 32700 + zone  # southern hemisphere


def fetch_dem(bbox: tuple, api_key: str, out_path: str, demtype: str = "COP30") -> str:
    """Download a GeoTIFF DEM covering bbox=(west, south, east, north) from
    OpenTopography's Global DEM API."""
    west, south, east, north = bbox
    url = "https://portal.opentopography.org/API/globaldem"
    params = {
        "demtype": demtype,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=120)
    except requests.RequestException as e:
        # don't let the exception text (which includes the full URL) leak the API key
        raise RuntimeError(f"Could not reach OpenTopography: {type(e).__name__}") from None
    if resp.status_code != 200 or resp.headers.get("Content-Type", "").startswith("application/json"):
        raise RuntimeError(f"OpenTopography request failed ({resp.status_code}): {resp.text[:500]}")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return out_path


def reproject_to_utm(src_path: str, dst_path: str) -> int:
    """Reproject a GeoTIFF to the UTM zone matching its centre point.
    Returns the destination EPSG code."""
    with rasterio.open(src_path) as src:
        lon = (src.bounds.left + src.bounds.right) / 2
        lat = (src.bounds.top + src.bounds.bottom) / 2
        dst_epsg = utm_epsg_for_lonlat(lon, lat)
        dst_crs = f"EPSG:{dst_epsg}"

        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds
        )
        kwargs = src.meta.copy()
        kwargs.update({
            "crs": dst_crs,
            "transform": transform,
            "width": width,
            "height": height,
        })

        with rasterio.open(dst_path, "w", **kwargs) as dst:
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=rasterio.band(dst, i),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    resampling=Resampling.bilinear,
                )
    return dst_epsg
