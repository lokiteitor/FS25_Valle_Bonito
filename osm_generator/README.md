# osm_generator

`generate_osm.py` writes `map.osm` for the 8192 x 8192 m playable area, from the four
registries in `map_layout.py` at the root of the tree. Those registries are empty, so
what it writes today is a `<bounds>` element with nothing inside it - the blank vector
map that goes with the blank heightmap.

- `map_extent.py`    re-exports the projection from `map_layout.py`
- `generate_osm.py`  write map.osm from the shared layout
- `visualize_osm.py` render map.osm to map_osm_visual.png
- `check_osm.py`     inventory and invariants; exits 1 if one is broken

Standard library plus matplotlib for the render. The DEM goes first: anything that sizes
itself to the ground reads `dem_generator/terrain_stats.json`.

    python3 ../dem_generator/generate_new_dem_12k.py
    python3 generate_osm.py     # -> map.osm
    python3 check_osm.py        # -> inventory + invariants
    python3 visualize_osm.py    # -> map_osm_visual.png

## Map centre

    LAT_CENTER =  43.0600
    LON_CENTER = -95.2800

They live in `map_layout.py`, and everything else is derived from them. Moving the map
means changing those two numbers and re-running both generators - and moving them means
every node in `map.osm` moves relative to the heightmap, which no check downstream would
catch, so change them deliberately or not at all.

## Extent

The playable area is 8192 x 8192 m. Local coordinates are playable metres, x east, y
south from the north edge, so the centre of the map sits at (4096, 4096).

Projection: equirectangular about the centre, 111111.0 m per degree of latitude and
111111.0 * cos(LAT_CENTER) m per degree of longitude, which puts the corners at

    minlat  43.0231359631      south edge, y = 8192
    maxlat  43.0968640369      north edge, y = 0
    minlon -95.3304545078      west edge,  x = 0
    maxlon -95.2295454922      east edge,  x = 8192

These are the four values in the `<bounds>` element of `map.osm`. The 3D viewer stretches
that box to fill the playable square whatever it says, so it has to stay right;
`generate_osm.py` reads the file back and round-trips them through the projection before
it reports success, because a sign slip is invisible in the raw degrees.

## What can go on the map

The vocabulary is closed on purpose: `map_layout.RENDERED_TAGS` is exactly what
`visualize_osm.py` and `visualizer/create_3d_viewer.py` know how to draw, and both drop
anything else without a word. Ground that has no tag in the list is better left unclaimed
- floodplain pasture is simply ground the parcelling leaves out of cultivation - than
tagged with something nothing draws. `check_osm.py` fails the build if a way was emitted
that neither renderer can see.

| Feature | Tags |
|---|---|
| Trunk road | `highway=primary` (+ `ref`) |
| Section roads | `highway=secondary` |
| Farm lanes and village streets | `highway=tertiary` |
| Railway | `railway=rail` |
| Bridges | the way's own tag plus `bridge=yes`, `layer=1` |
| Fields | `landuse=farmland` |
| Villages, farmsteads, industrial aprons | `landuse=farmyard` (+ `building=industrial`) |
| Timber, groves, shelterbelts | `natural=wood` + `landuse=farmyard` + `leaf_type` |
| Rivers and lakes | `natural=water` (+ `water=river` / `water=lake`) |

## Invariants

`check_osm.py` fails the build if any of these break:

- the file declares bounds that round-trip to an 8192 m square
- every way points at nodes that exist, and no node is in no way
- every node inside the playable area
- every area closed on its own first node - the 3D viewer decides polygon versus line by
  comparing the first and last coordinate exactly
- every way carrying a tag both renderers draw
- nothing planted inside the 100 m clean strip along the boundary
- the file and `map_layout` agreeing on whether the map is empty. Both halves of the
  pipeline describe the same world or neither does, and an OSM that quietly stopped
  emitting what the layout carries is the failure that rule exists to catch

Add an invariant here as each kind of feature comes back, rather than only fixing the
coordinates that broke: on the map this replaces, three of the seven farmsteads turned
out to be misplaced once the road-clearance rule existed, and only one had been visible.

## Building the new map

Fill the registries in `map_layout.py` - `CORRIDORS`, `WATER`, `PADS`, `AREAS` - and the
emit functions here pick them up as they are; the record each one holds is documented
above it. The parts worth not rewriting are the node pool (one node per coordinate, so
junctions are shared rather than coincident), the corridor splitting (at every crossing
and every bridge abutment) and `strip_ring` (clip rings, never clamp them). Whatever goes
into the layout has to be sculpted by the DEM in the same pass, or
`generate_new_dem_12k.py` will stop the build and say so.
