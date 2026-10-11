"""Shared utilities and entry point for building the national public data catalog."""
from __future__ import annotations

import io
import math
import struct
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SNAP = DATA / "snapshots"

def shape_features(raw, state_fips):
    """Read Census polygon SHP and DBF without a GIS dependency."""
    archive = zipfile.ZipFile(io.BytesIO(raw))
    dbf = archive.read(next(n for n in archive.namelist() if n.endswith(".dbf")))
    count, header_size, record_size = struct.unpack_from("<IHH", dbf, 4)
    fields = []
    offset = 32
    while dbf[offset] != 13:
        field = dbf[offset:offset + 32]
        fields.append((field[:11].split(b"\0")[0].decode(), field[16]))
        offset += 32
    attributes = []
    for index in range(count):
        start = header_size + index * record_size + 1
        row = {}
        for name, length in fields:
            row[name] = dbf[start:start + length].decode("latin1").strip()
            start += length
        attributes.append(row)
    shp = archive.read(next(n for n in archive.namelist() if n.endswith(".shp")))
    offset = 100
    features = []
    for row in attributes:
        length = struct.unpack_from(">I", shp, offset + 4)[0] * 2
        content = shp[offset + 8:offset + 8 + length]
        offset += length + 8
        if row.get("STATEFP") != state_fips or len(content) < 44:
            continue
        parts, points = struct.unpack_from("<II", content, 36)
        starts = list(struct.unpack_from(f"<{parts}I", content, 44)) + [points]
        xy = struct.unpack_from(f"<{points * 2}d", content, 44 + parts * 4)
        rings = []
        for p in range(parts):
            ring = [[round(xy[j * 2], 4), round(xy[j * 2 + 1], 4)] for j in range(starts[p], starts[p + 1])]
            # Rings are retained in their supplied order; no synthetic geometry.
            ring = [v for j, v in enumerate(ring) if j == 0 or v != ring[j - 1]]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) >= 4:
                rings.append(ring)
        features.append({"type": "Feature", "properties": {"id": row["GEOID"], "name": row.get("NAME"), "county_id": row.get("COUNTYFP")}, "geometry": {"type": "Polygon", "coordinates": rings}})
    return features


def number(value):
    try:
        parsed = float(value)
        return parsed if parsed >= 0 and math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def select_spread(items, n):
    if len(items) <= n:
        return items
    remaining = sorted(items, key=lambda v: v["id"])
    selected = [remaining.pop(0)]
    while remaining and len(selected) < n:
        item = max(remaining, key=lambda v: min((v["lon"] - s["lon"]) ** 2 + (v["lat"] - s["lat"]) ** 2 for s in selected))
        selected.append(item)
        remaining.remove(item)
    return selected



def prepare():
    """Build national tract/site inputs, state maps, and the queryable archive."""
    DATA.mkdir(exist_ok=True)
    SNAP.mkdir(exist_ok=True)
    from prepare_national import build as build_national
    from prepare_geography import build as build_geography
    from build_catalog import build as build_catalog
    build_national()
    build_geography()
    build_catalog()


if __name__ == "__main__":
    prepare()
