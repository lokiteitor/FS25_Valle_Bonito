# FS25 Valle Bonito - map pipeline

Generates the terrain and the vector layout for a Farming Simulator 25 map of northwest
Iowa farm country: Clay County, around Royal, on the western edge of the Des Moines Lobe.
Gently rolling till plain, closed prairie-pothole depressions, a river running across the
southwest **through** a deep glacial lake with a tributary feeding its head, the Public
Land Survey road grid over all of it, three small towns, seven farmsteads with their
groves and snow fences, and a straight branch line.

## Running it

The DEM goes first: the parcelling reads the terrain it publishes.

    python3 dem_generator/generate_new_dem_12k.py     # heightmap + terrain_stats.json
    python3 dem_generator/measure_elevation.py        # acceptance report, exits 1 on failure
    python3 osm_generator/generate_osm.py             # map.osm
    python3 osm_generator/check_forest_nodes.py       # inventory + invariants, exits 1 on failure
    python3 osm_generator/visualize_osm.py            # map_osm_visual.png
    python3 visualizer/create_3d_viewer.py            # dem_viewer_3d.html

Needs numpy, scipy, Pillow and matplotlib (all in `.venv`). The two scripts that read
`map.osm` back are standard library plus matplotlib.

## Layout of the tree

| Path | What it is |
|---|---|
| `map_layout.py` | **The one source of truth.** Where everything is, in playable metres: projection, road and rail alignments, the section grid, the river, the lake, the village and farm pads, the potholes and the field parcelling. Standard library only, no randomness in the alignments. |
| `map_source.py` | Two-line shim so `visualizer/create_3d_viewer.py` can import the canvas dimensions. |
| `dem_generator/` | The heightmap: `terrain_ops.py` (primitives), `generate_new_dem_12k.py` (synthesis), `measure_elevation.py` (acceptance). |
| `osm_generator/` | The vectors: `generate_osm.py` (writes `map.osm`), `visualize_osm.py` (2D render), `check_forest_nodes.py` (inventory and invariants), `map_extent.py` (re-exports the projection). |
| `pf_generator/` | Precision Farming soil map. Independent of the terrain - pure noise with a seed. |
| `visualizer/` | Three.js viewer that puts the heightmap and the vectors together in one page. |

`osm_generator/generate_osm_bocage.py` is the previous English bocage generator, kept for
reference only. It needs a `map_source` API that no longer exists and is not part of the
build.

## The one rule

The terrain and the vectors must describe the same world. Both read their geometry from
`map_layout.py`, and neither is allowed to invent its own. Where the OSM side needs to
know about the ground - the parcelling makes smaller fields on broken ground - the DEM
publishes `dem_generator/terrain_stats.json` and the OSM reads it, rather than either
side re-deriving the other's work.

## Map facts

- Canvas 12288 x 12288 m at 1 px = 1 m, playable area 8192 x 8192 m centred in it.
- Heights are 16-bit centimetres in a greyscale PNG, which is what Giants Editor imports.
- Centre 43.0600 N, -95.2800 - Royal, Clay County, Iowa.
- Section grid one mile apart (1609.344 m), at x and y = 878, 2487, 4096, 5705, 7314.
- Local coordinates are playable metres, x east, y south from the north edge.
- 31.6 m of relief on the land, plus a 40 m lake basin on the main stem of the river.
- 200 fields, 4823 ha, 72% of the playable area; nothing over 100 ha, nothing in the basin.
