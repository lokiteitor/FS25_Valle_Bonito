# CLAUDE.md

This file provides guidance to Claude Code and AI assistants when working with code in this repository.

## What this is

A clean, modular generator for a Farming Simulator 25 map ("Valle Bonito"). It produces two primary artefacts that are imported into Giants Editor:
1. A 16-bit heightmap PNG (`dem_generator/dem_new_12k.png`) covering 12288x12288 m (1 px = 1 m).
2. An OSM vector file (`osm_generator/map.osm`) defining the playable area (8192x8192 m).

This repository is self-contained and **does not depend on any external OSM or PNG files**. The terrain and vector geometry are synthesized purely procedurally and programmatically from `map_layout.py`.

## Commands

Use `.venv/bin/python3` (or activate `.venv`):

```bash
# 1. Geometry & layout verification
python3 map_layout.py                             # layout self-check, instant

# 2. DEM generation & verification
python3 dem_generator/generate_new_dem_12k.py     # ~25 s -> dem_new_12k.png, terrain_stats.json, preview PNGs
python3 dem_generator/measure_elevation.py        # acceptance tests on DEM, exit 1 on failure

# 3. OSM vector generation & verification
python3 osm_generator/generate_osm.py             # -> map.osm
python3 osm_generator/check_osm.py                # inventory + invariants check, exit 1 on failure
python3 osm_generator/visualize_osm.py            # -> map_osm_visual.png (2D overview)

# 4. 3D Web Viewer & Minimap
python3 visualizer/create_3d_viewer.py            # -> dem_viewer_3d.html (Three.js interactive viewer)
python3 render_pda.py                             # renders PDA overview textures (4K/2K) for the mod folder

# 5. Precision Farming (independent)
python3 pf_generator/generate_soil.py -s <seed>   # Precision Farming soilMap.png
```

> [!NOTE]
> Always run the DEM generator before the OSM generator: `generate_osm.py` reads `dem_generator/terrain_stats.json` published by the DEM.

## Architecture

**`map_layout.py` is the single source of geometry:**
- The DEM sculpts around the geometry registered here, and the OSM writes it out. Neither half defines geometry of its own.
- Standard library only: nothing in `map_layout.py` or `osm_generator/` imports numpy or external dependencies.
- Contains the 5 core registries and clean builder helpers:
  - `CORRIDORS`: `add_corridor(cid, name, kind, axis, ...)`
  - `WATER`: `add_water(wid, name, kind, ring=..., axis=..., ...)`
  - `PADS`: `add_pad(pid, name, kind, centre, size, ...)`
  - `AREAS`: `add_area(aid, name, kind, ring, ...)`
  - `FIELDS`: `add_field(fid, name, ring, ...)`
- Invariants are verified by `map_layout.validate()`. Both generators refuse to proceed if `validate()` detects problems.

**DEM Synthesis (`dem_generator/generate_new_dem_12k.py`):**
- Canvas is 12288 x 12288 m (1 px = 1 m).
- Playable area is 8192 x 8192 m centred (offset 2048 m margin on all sides).
- Base terrain is generated at datum `BASE_ELEV_M` (46.4 m).
- Outside the playable square, `build_rim` creates the mountain border rising up to 250 m (`RIM_CREST_M`), preceded by a 100 m flat apron (`RIM_APRON_M`).
- Features in `PADS`, `CORRIDORS`, and `WATER` are graded/carved smoothly into the ground.

## Coordinates and Projection

- **Playable coordinates:** x east, y south from the north edge, centre at `(4096, 4096)`.
- **Canvas coordinates:** `-2048 .. 10240` in the same frame.
- **Projection:** Equirectangular about `LAT_CENTER, LON_CENTER` at 111111.0 m per degree.
- **Heights:** 16-bit centimetres (`raw / 100 = metres`).
- **Clean strip:** 50 m (`EDGE_CLEAR_M`) inside the playable boundary.

## Closed OSM Tag Vocabulary

Emit only what `map_layout.RENDERED_TAGS` lists:
`natural=water`, `natural=wood`, `landuse=forest`, `landuse=farmyard`, `landuse=farmland`, `highway=*`, `railway=*`.
Rings must close on the same node id.
