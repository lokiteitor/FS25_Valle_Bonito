# FS25 Valle Bonito - map pipeline

Generates the terrain and the vector layout for a Farming Simulator 25 map: a 16-bit
heightmap PNG and an OSM vector file, both built from one shared description of where
everything is, ready to import into Giants Editor.

**The map is blank.** The heightmap is flat at 20 m over the whole canvas and `map.osm`
carries nothing but its `<bounds>`. The technical base is intact and unchanged - the
projection, the coordinates, the canvas geometry, the encoding, the geometric primitives,
the terrain operators and the acceptance harness - so the new map gets built on the same
foundation rather than a new one. The Iowa layout that used to be here (river, glacial
lake, PLSS road grid, branch line, villages, farmsteads, 118 fields) is in git history at
commit `8d11754`.

## Running it

The DEM goes first: anything that sizes itself to the ground reads what it publishes.

    python3 dem_generator/generate_new_dem_12k.py     # heightmap + terrain_stats.json
    python3 dem_generator/measure_elevation.py        # acceptance report, exits 1 on failure
    python3 osm_generator/generate_osm.py             # map.osm
    python3 osm_generator/check_osm.py                # inventory + invariants, exits 1 on failure
    python3 osm_generator/visualize_osm.py            # map_osm_visual.png
    python3 visualizer/create_3d_viewer.py            # dem_viewer_3d.html

    python3 map_layout.py                             # layout self-check, no output files

Needs numpy, scipy, Pillow and matplotlib (all in `.venv`). The scripts that read
`map.osm` back are standard library plus matplotlib.

## Layout of the tree

| Path | What it is |
|---|---|
| `map_layout.py` | **The one source of truth.** The projection, the size of the canvas, the base elevation, the geometric primitives, and four empty registries - `CORRIDORS`, `WATER`, `PADS`, `AREAS` - to put the new world in. Standard library only. |
| `map_source.py` | Two-line shim so `visualizer/create_3d_viewer.py` can import the canvas dimensions. |
| `dem_generator/` | The heightmap: `terrain_ops.py` (primitives, untouched), `generate_new_dem_12k.py` (lays the datum down, and refuses to run if the layout carries features it does not sculpt), `measure_elevation.py` (acceptance). |
| `osm_generator/` | The vectors: `generate_osm.py` (writes `map.osm`), `visualize_osm.py` (2D render), `check_osm.py` (inventory and invariants), `map_extent.py` (re-exports the projection). |
| `pf_generator/` | Precision Farming soil map. Independent of the terrain - pure noise with a seed. |
| `visualizer/` | Three.js viewer that puts the heightmap and the vectors together in one page. |

## The one rule

The terrain and the vectors must describe the same world. Both read their geometry from
`map_layout.py`, and neither is allowed to invent its own: a river carved where none is
drawn, or a yard flattened where no farmyard exists, is invisible in either output on its
own. Where the OSM side needs to know about the ground, the DEM publishes
`dem_generator/terrain_stats.json` and the OSM reads it, rather than either side
re-deriving the other's work.

Both generators enforce it. `generate_new_dem_12k.py` stops if the layout carries water,
pads or corridors it has no sculpting stage for; `check_osm.py` fails if the layout and
the file disagree about whether the map is empty.

## Map facts

- Canvas 12288 x 12288 m at 1 px = 1 m, playable area 8192 x 8192 m centred in it.
- Heights are 16-bit centimetres in a greyscale PNG, which is what Giants Editor imports:
  raw 2000 = 20.00 m.
- The whole canvas is at 20.00 m exactly, playable area and border alike. That leaves
  20 m of cut before the encoding clips at zero and 600 m of fill before the ceiling.
- Centre 43.0600 N, -95.2800; equirectangular about it, 111111.0 m per degree.
- Local coordinates are playable metres, x east, y south from the north edge, so the
  centre of the map is (4096, 4096). Canvas coordinates run -2048 .. 10240 in the same
  frame.
- A 100 m clean strip inside the playable boundary that nothing planted may enter.

## Building the new map

1. Put the geometry in the registries in `map_layout.py`. The record each one holds is
   documented above it, and `validate()` is where the rules about placement go.
2. Bring back the matching sculpting stage in `dem_generator/generate_new_dem_12k.py`.
   Its `sculpt()` docstring carries the build order, which is load-bearing, and
   `terrain_ops.py` still has every primitive the old terrain was built with.
3. Add the invariant to `osm_generator/check_osm.py` and the acceptance check to
   `dem_generator/measure_elevation.py` in the same pass. Both scripts gate the build.

`CLAUDE.md` carries the things that have already gone wrong here - each one a real bug
found by measurement rather than by looking at the output. It is worth reading before
writing any of the terrain back.
