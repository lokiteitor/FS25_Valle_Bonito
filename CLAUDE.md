# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A generator for a Farming Simulator 25 map of northwest Iowa farmland (Clay County, around
Royal). It produces two artefacts that a human then imports into Giants Editor: a 16-bit
heightmap PNG and an OSM vector file. There is no application to run and no test suite -
the verification is two acceptance scripts that exit non-zero.

## Commands

The order matters: the parcelling reads terrain the DEM publishes.

    python3 dem_generator/generate_new_dem_12k.py     # ~2 min -> dem_new_12k.png + terrain_stats.json
    python3 dem_generator/measure_elevation.py        # 54 checks, exit 1 on any failure
    python3 osm_generator/generate_osm.py             # -> map.osm
    python3 osm_generator/check_forest_nodes.py       # inventory + 13 invariants, exit 1 on failure
    python3 osm_generator/visualize_osm.py            # -> map_osm_visual.png
    python3 visualizer/create_3d_viewer.py            # -> dem_viewer_3d.html

    python3 map_layout.py                             # layout self-check, seconds, no output files

Scripts work from the repo root or from their own directory; each inserts what it needs on
`sys.path`. Either interpreter works - the system `python3` and `.venv/bin/python3` (3.14)
both carry numpy, scipy, Pillow and matplotlib.

Iterating on the layout alone (field sizes, windbreaks, pad positions) does **not** need a
DEM rerun - only changes to the river, creek, lake, corridors or pads do.

## The one rule

`map_layout.py` (repo root, standard library only) is the single source of the world's
geometry: projection, the PLSS section grid, road and rail alignments, the river, the
creek, the lake, village, farm, elevator and industrial-lot pads, potholes, windbreaks,
the field
parcelling, the clean strip along the boundary and how far out the rim mountains start. The
DEM sculpts terrain around it; the OSM writes it out as vectors. **Neither half may
define geometry of its own.** A river carved where none is drawn, or a yard flattened
where no farmyard exists, is invisible in either output on its own.

Where the OSM side needs to know about the ground (smaller fields on broken ground), the
DEM publishes `dem_generator/terrain_stats.json` - a 128x128 height and roughness grid -
and `map_layout.load_roughness()` reads it with the standard library. Do not import numpy
into `osm_generator/`, and do not re-derive the terrain there.

## Coordinates

Playable metres: **x east, y south from the north edge**, centre `(4096, 4096)`. The DEM
canvas is larger, so canvas coordinates run `-2048 .. 10240` in the same frame.

The DEM synthesises at **3072x3072 (4 m/px)** and resamples once to 12288x12288 (1 m/px).
The canvas metre of working pixel `j` is `4j + 2`; the centre of output pixel `i` is
`i + 0.5`. Getting that wrong shifts the terrain against the vectors by metres and is
invisible in the image. Full resolution is not an option: one blur there costs 7 s and one
distance transform 5.6 GB, and the pipeline needs about twenty of them.

## Things that have already gone wrong here

Each of these was a real bug found by measurement, not by looking at the output. They are
the reason the code is shaped the way it is.

- **Offsetting a polyline** by more than its radius of curvature folds the ring through
  itself, and an even-odd fill then punches holes in the tightest meanders. Reserves along
  water are stamped by distance to the centreline (`_Occupancy._fill_corridor`), never as
  offset polygons.
- **The occupancy raster is 32 m and judges a cell by its centre**, so anything narrower -
  a 24 m shelterbelt - can fall between two centres and mark nothing at all. Thin shapes go
  through `_fill_ring_strict`, which also walks the boundary. Reserve radii are grown by
  half a cell diagonal so "no field within R of the water" is true rather than nearly true.
- **Carve water with `soft_min`, not a weighted blend.** Blending leaves a band of
  half-attenuated noise and a valley of constant width; the smooth minimum leaves the
  ground outside exactly as it was and puts the rim where the two surfaces cross.
- **Platform feathers must widen with the cut**: `max(nominal, 1.5*|dz|/tan(4 deg))`. In a
  smoothstep the steepest gradient is `1.5*rise/run`, so a constant feather cuts a step
  wherever the platform sits deep. The same identity sets every bank and shore width in
  `map_layout`.
- **Corridor grades come from the mean of the two Lipschitz envelopes**
  (`terrain_ops.limit_grade`), which is exact in two passes and balances cut against fill.
  Clipping the slope forward then backward is not idempotent and drags the profile downhill.
- **Build order is load-bearing**: yards before roads (otherwise the pad overwrites the road
  platform and leaves a step at its edge), slope limiting before platforms (diffusing a new
  embankment ruins it), and a higher-class corridor keeps its platform where two cross.
- **Level-crossing pins must be applied as a wide smooth offset**, not by overwriting one
  sample - `limit_grade` halves any spike, so a hard pin lands about half the error out.
- **The blocks are cut on the alignments, not on the section grid.** 270th Avenue is
  offset off its section line by the width of the railway's right of way, and while
  `field_blocks` cut on `GRID` the road ran 26 m inside the field next to it and sliced
  it in two. `ROW_CLEAR` is per class for the same reason: 14 m to the centreline leaves
  three metres of verge against an 11 m primary.
- **Both halves of the pipeline define geometry or neither does.** The co-op elevator was
  a rectangle `generate_osm.py` worked out from the Royal village pad. The parcelling
  could not see it and laid fields over it, and the DEM never flattened the ground under
  it. It is `INDUSTRY_SPEC` in `map_layout` now, and in `pads()`.
- **Clip a ring, do not clamp it.** Clamping the coordinates of a gallery-timber strip
  back to the clean strip folds the part that hangs over the boundary onto the boundary
  itself, and put a run of nodes straight across the river channel. `clip_ring_to_rect`
  only ever puts a vertex on the ring's own boundary.
- **The rim is added last and by addition.** Mountains built before the slope limiter get
  flattened by it; built as a second surface blended in, they smear out the roads and the
  valley already in the border. `z + h` carries everything there up the flank intact. The
  ramp uses a 4-norm of the distance outside the playable square - a plain maximum creases
  along the diagonals and puts four seams out of the corners of the map. The lift is held
  off the river's own valley (`RIM_GORGE_*`), or the rim dams the channel and the water
  runs 200 m uphill on its way out of the map.
- **Surface texture stays off running surfaces and channels.** The 4 cm micro-relief is 8 cm
  over the 25 m the ruling grade is measured across, a quarter of the railway's budget.
- **The river holds water, so its bed is under its own waterline.** The profile the DEM
  carries along the channel is the water surface; `RIVER['water_depth_m']` cuts the bed
  5 m below it, on a 28 degree submerged bank. That angle is only allowed because every
  metre of it is under water - the argument the lake's drop already stood on - and it is
  only *survivable* if the rest of the pipeline agrees where the waterline is. `main`
  builds that agreement once, as `wet`: the slope limiter takes it as `exempt` (twelve
  passes of a 24 m diffusion kernel fill a 44 m trough in - one pass alone puts 1.5 m
  back), no platform grades it, the texture stays off it, and the datum's 0.1st
  percentile is taken on the land without it. Four private opinions about where the water
  is, and the river comes out half filled in on a map that still measures as if it were
  not.
- **A platform's feather is measured against the land, not the water.**
  `feather = 1.5*|dz|/tan(4 deg)` grows with the cut, and against a bed five metres under
  the floodplain it reached 170 m instead of 45. Six roads running 50 to 130 m off the
  bank filled the channel to within a metre of its lip; what showed it was not the image
  but the thalweg no longer falling. `apply_corridor` takes the water out of `dz` and
  then keeps the weight off it as well.

## Measurement discipline

`measure_elevation.py` rebuilds its zone masks from `map_layout` through the same
`terrain_ops` primitives the generator used. A second implementation of "where is the
valley" is the shortest route to a report that passes a heightmap which does not meet the
brief.

Slope is measured over a **5 m baseline**: a DEM quantised to the centimetre at 1 m/px has
a ~0.3 degree noise floor in its per-pixel gradient, so measuring pixel to pixel overstates
every slope on the map. When a check fails, suspect the measurement frame first - several
"failures" here were the measurer comparing arc lengths of two different polylines, reading
the riverbank under a bridge as a ruling grade, or checking a creek culvert against the
river's bed.

That baseline is also why **dry land starts one baseline back from the water's edge**: a
5 m window straddling the lip reads the submerged bank off a pixel that is itself dry, and
reported the inside of the channel as a 15 degree field, the beach beside it as a 17 degree
shore, and the apron the river leaves the map through as 20 degrees.

## OSM tag vocabulary is closed

Emit only what `osm_generator/visualize_osm.py` and `visualizer/create_3d_viewer.py`
(`style_rules`, around line 158) already draw: `landuse=farmland`, `landuse=farmyard`,
`natural=wood`, `natural=water` (+ `water=*`), `highway=primary|secondary|tertiary`,
`railway=*`, plus `bridge=yes`/`layer`. **Both renderers drop anything else without a
word.** That is why floodplain pasture carries no tag - it is simply ground the parcelling
leaves out of cultivation. `check_forest_nodes.py` fails the build if a way was emitted
that neither renderer can see.

An area is a polygon to the 3D viewer only if its first and last coordinates are *exactly*
equal, so rings must close on the same node id.

## Determinism

`map_layout` has no floating-point randomness in the alignments - river and creek meanders
are closed-form sine sums - so both halves of the pipeline get identical polylines. Where it
does use `random.Random(SEED)` (potholes, parcel jitter) the jitter is keyed to position,
not to iteration order. The DEM uses named RNG streams with fixed, spaced indices
(`STREAMS` in the generator) so adding an octave does not shift the existing ones and
change the whole terrain. Both seeds are `20250902`.

## Tuning

Almost everything worth changing is a constant near the top of `map_layout.py`: `RIVER` and
`CREEK` cross-sections (`water_depth_m`/`bed_half_w`/`wet_bank_w` are the channel itself,
and `water_half_w` must stay equal to the sum of the last two - `validate()` says so), `LAKE`/`LAKE_AT_S`, `FIELD_MAX_COUNT`/`FIELD_MAX_HA`,
`WINDBREAK_*`, `PAD_ROAD_CLEAR_M`, `PAD_RIVER_CLEAR_M`, `EDGE_CLEAR_M`, `RIM_APRON_M`,
`RIM_HEIGHT_M`, `LOT_SPEC`/`LOT_HA`. The field count cap is held by
coarsening the whole grain field until it fits, never by deleting the overflow - dropping
fields eats the small parcels near the towns, which is exactly what the size mix needs.

`map_layout.validate()` is the gate: it returns complaints and both generators refuse to
run when it does. Add a rule there when you find a placement mistake, rather than only
fixing the coordinates - three of the seven farms turned out to be misplaced once the
road-clearance rule existed, and only one of them was visible.

## Out of scope

`pf_generator/generate_soil.py` is pure seeded noise with no connection to the terrain.
`osm_generator/generate_osm_bocage.py` is the previous English layout, kept for reference;
it needs a `map_source` API that no longer exists and would overwrite `map.osm` if run.
