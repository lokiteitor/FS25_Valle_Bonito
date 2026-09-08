# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A generator for a Farming Simulator 25 map. It produces two artefacts that a human then
imports into Giants Editor: a 16-bit heightmap PNG and an OSM vector file. There is no
application to run and no test suite - the verification is two acceptance scripts that
exit non-zero.

**The map is currently blank.** The heightmap is flat at 20 m across the whole canvas and
`map.osm` is a `<bounds>` element with nothing inside it. What is here is the technical
base: the projection, the coordinates, the canvas geometry, the 16-bit centimetre
encoding, the geometry primitives, the terrain operators in `terrain_ops.py`, and the two
acceptance harnesses. The previous map - northwest Iowa farmland around Royal, Clay
County, with a river, a glacial lake, the PLSS road grid, a branch line, three villages,
seven farmsteads and 118 fields - was cleared out deliberately; it is in git history at
commit `8d11754` if a piece of it is worth reading back.

## Commands

The order matters: anything that sizes itself to the ground reads what the DEM publishes.

    python3 dem_generator/generate_new_dem_12k.py     # ~15 s -> dem_new_12k.png + terrain_stats.json
    python3 dem_generator/measure_elevation.py        # acceptance report, exit 1 on any failure
    python3 osm_generator/generate_osm.py             # -> map.osm
    python3 osm_generator/check_osm.py                # inventory + invariants, exit 1 on failure
    python3 osm_generator/visualize_osm.py            # -> map_osm_visual.png
    python3 visualizer/create_3d_viewer.py            # -> dem_viewer_3d.html

    python3 map_layout.py                             # layout self-check, seconds, no output files

Scripts work from the repo root or from their own directory; each inserts what it needs on
`sys.path`. Either interpreter works - the system `python3` and `.venv/bin/python3` (3.14)
both carry numpy, scipy, Pillow and matplotlib.

## The one rule

`map_layout.py` (repo root, standard library only) is the single source of the world's
geometry. The DEM sculpts terrain around it; the OSM writes it out as vectors. **Neither
half may define geometry of its own.** A river carved where none is drawn, or a yard
flattened where no farmyard exists, is invisible in either output on its own.

Everything the world is made of goes in four registries, all empty today:

    CORRIDORS    roads and railway - alignment, class, platform width, bridge spans
    WATER        rivers, creeks, lakes - centreline or shore, drawn half-width, depth
    PADS         levelled platforms - centre, size, ring, feather
    AREAS        tagged rings the OSM draws - fields, woods, yards

The record each one holds is documented above it in `map_layout.py`. Both halves enforce
the rule rather than trusting it: `generate_new_dem_12k.py`'s `sculpt()` stops the build
if the layout carries water, pads or corridors it has no sculpting stage for, and
`check_osm.py` fails if the layout and `map.osm` disagree about whether the map is empty.

Where the OSM side needs to know about the ground (smaller fields on broken ground), the
DEM publishes `dem_generator/terrain_stats.json` - a 128x128 height and roughness grid -
and `map_layout.load_roughness()` reads it with the standard library. Do not import numpy
into `osm_generator/`, and do not re-derive the terrain there.

## Coordinates

Playable metres: **x east, y south from the north edge**, centre `(4096, 4096)`. The DEM
canvas is larger, so canvas coordinates run `-2048 .. 10240` in the same frame. The
projection is equirectangular about `LAT_CENTER, LON_CENTER` at 111111.0 m per degree.
These are unchanged from the Iowa map and should stay that way: moving the centre moves
every node in `map.osm` relative to the heightmap, and nothing downstream would catch it.

The DEM synthesises at **3072x3072 (4 m/px)** and resamples once to 12288x12288 (1 m/px).
The canvas metre of working pixel `j` is `4j + 2`; the centre of output pixel `i` is
`i + 0.5`. Getting that wrong shifts the terrain against the vectors by metres and is
invisible in the image. Full resolution is not an option: one blur there costs 7 s and one
distance transform 5.6 GB, and a real terrain pipeline needs about twenty of them.

Heights are 16-bit centimetres: raw 2000 = 20.00 m. `map_layout.BASE_ELEV_M` is the datum
the blank canvas sits at, and it leaves 20 m of cut before the encoding clips at zero and
600 m of fill before Giants' working ceiling.

## Things that have already gone wrong here

Each of these was a real bug found by measurement, not by looking at the output. Most of
the code that hit them has been cleared out with the Iowa map, but the mistakes have not
gone anywhere - they are what any terrain written back into this pipeline will hit again,
in the same order.

- **Offsetting a polyline** by more than its radius of curvature folds the ring through
  itself, and an even-odd fill then punches holes in the tightest meanders. Reserves along
  water are stamped by distance to the centreline, never as offset polygons.
  `map_layout.offset_polyline` carries the warning in its docstring.
- **An occupancy raster judges a cell by its centre**, so anything narrower than the cell
  - a 24 m shelterbelt on a 32 m grid - can fall between two centres and mark nothing at
  all. Thin shapes need a fill that also walks the boundary, and reserve radii want
  growing by half a cell diagonal so "no field within R of the water" is true rather than
  nearly true.
- **Carve water with `soft_min`, not a weighted blend.** Blending leaves a band of
  half-attenuated noise and a valley of constant width; the smooth minimum leaves the
  ground outside exactly as it was and puts the rim where the two surfaces cross.
- **Platform feathers must widen with the cut**: `max(nominal, 1.5*|dz|/tan(4 deg))`. In a
  smoothstep the steepest gradient is `1.5*rise/run`, so a constant feather cuts a step
  wherever the platform sits deep. The same identity sets every bank and shore width.
- **Corridor grades come from the mean of the two Lipschitz envelopes**
  (`terrain_ops.limit_grade`), which is exact in two passes and balances cut against fill.
  Clipping the slope forward then backward is not idempotent and drags the profile downhill.
- **Build order is load-bearing**: yards before roads (otherwise the pad overwrites the road
  platform and leaves a step at its edge), slope limiting before platforms (diffusing a new
  embankment ruins it), and a higher-class corridor keeps its platform where two cross.
  `sculpt()` in the DEM generator carries the whole order in its docstring.
- **Level-crossing pins must be applied as a wide smooth offset**, not by overwriting one
  sample - `limit_grade` halves any spike, so a hard pin lands about half the error out.
- **Cut the field blocks on the alignments, not on the section grid.** One avenue was
  offset off its section line by the width of the railway's right of way, and while the
  blocks cut on the grid the road ran 26 m inside the field next to it and sliced it in
  two. Road clearance is per class for the same reason: 14 m to the centreline leaves
  three metres of verge against an 11 m primary.
- **Both halves of the pipeline define geometry or neither does.** The co-op elevator was
  once a rectangle the OSM generator worked out from a village pad. The parcelling could
  not see it and laid fields over it, and the DEM never flattened the ground under it. It
  belongs in a registry in `map_layout`, like everything else.
- **Clip a ring, do not clamp it.** Clamping the coordinates of a gallery-timber strip
  back to the clean strip folds the part that hangs over the boundary onto the boundary
  itself, and put a run of nodes straight across the river channel. `clip_ring_to_rect`
  only ever puts a vertex on the ring's own boundary. Water is the one exception, and
  only because it has to leave the map (`clamp_ring` in `generate_osm.py`).
- **The rim is added last and by addition.** Mountains built before the slope limiter get
  flattened by it; built as a second surface blended in, they smear out the roads and the
  valley already in the border. `z + h` carries everything there up the flank intact. The
  ramp wants a 4-norm of the distance outside the playable square - a plain maximum creases
  along the diagonals and puts four seams out of the corners of the map. Hold the lift off
  any river's own valley, or the rim dams the channel and the water runs 200 m uphill on
  its way out of the map. `RIM_HEIGHT_M` is 0 while the map is flat.
- **Surface texture stays off running surfaces and channels.** 4 cm of micro-relief is 8 cm
  over the 25 m the ruling grade is measured across, a quarter of a railway's budget. It is
  off entirely on the blank base (`MICRO_AMP_M = 0`), along with the dither.
- **A channel that holds water has its bed under its own waterline.** The profile the DEM
  carries along a channel is the water surface; the bed is cut under it, on a steep
  submerged bank. That angle is only allowed because every metre of it is under water,
  and it is only *survivable* if the rest of the pipeline agrees where the waterline is.
  Build that agreement once, as a `wet` mask: the slope limiter takes it as `exempt`
  (twelve passes of a 24 m diffusion kernel fill a 44 m trough in - one pass alone puts
  1.5 m back), no platform grades it, the texture stays off it, and the datum's
  percentile is taken on the land without it. Four private opinions about where the water
  is, and the river comes out half filled in on a map that still measures as if it were
  not.
- **A platform's feather is measured against the land, not the water.**
  `feather = 1.5*|dz|/tan(4 deg)` grows with the cut, and against a bed five metres under
  the floodplain it reached 170 m instead of 45. Six roads running 50 to 130 m off the
  bank filled the channel to within a metre of its lip; what showed it was not the image
  but the thalweg no longer falling. Take the water out of `dz`, then keep the weight off
  it as well.

## Measurement discipline

`measure_elevation.py` rebuilds its zone masks from `map_layout` through the same
`terrain_ops` primitives the generator used. A second implementation of "where is the
valley" is the shortest route to a report that passes a heightmap which does not meet the
brief. On the blank map there are no zones and the whole canvas is the mask.

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

Measure the thing, not its average. "Everything at the base elevation" has to mean every
pixel: a mean of 20.00 m is exactly what a surface 5 cm either side of the datum reports,
and that surface is not a blank sheet.

## OSM tag vocabulary is closed

Emit only what `osm_generator/visualize_osm.py` and `visualizer/create_3d_viewer.py`
(`style_rules`, around line 158) already draw. The list is `map_layout.RENDERED_TAGS`, and
both the generator and the checker read it from there so the two cannot drift:
`landuse=farmland`, `landuse=farmyard`, `natural=wood`, `natural=water` (+ `water=*`),
`highway=primary|secondary|tertiary`, `railway=*`, plus `bridge=yes`/`layer`. **Both
renderers drop anything else without a word.** That is why floodplain pasture carries no
tag - it is simply ground the parcelling leaves out of cultivation. `check_osm.py` fails
the build if a way was emitted that neither renderer can see.

An area is a polygon to the 3D viewer only if its first and last coordinates are *exactly*
equal, so rings must close on the same node id.

## Determinism

Keep floating-point randomness out of the alignments, so both halves of the pipeline get
identical polylines - closed-form meanders rather than an RNG walk. Where randomness is
wanted (potholes, parcel jitter), key it to position rather than to iteration order, or
adding one feature shifts every feature after it. The DEM uses named RNG streams with
fixed, spaced indices (`STREAMS` in the generator) so adding an octave does not shift the
existing ones and change the whole terrain. Both seeds are `20250902` (`map_layout.SEED`).

## Tuning

Almost everything worth changing is a constant near the top of `map_layout.py`:
`BASE_ELEV_M` (the datum, 20 m), `PLAYABLE_M`/`CANVAS_M`, `EDGE_CLEAR_M`, `RIM_APRON_M`,
`RIM_HEIGHT_M`, `LAT_CENTER`/`LON_CENTER`, `SEED`. Feature constants come back beside the
features they belong to.

`map_layout.validate()` is the gate: it returns complaints and both generators refuse to
run when it does. Add a rule there when you find a placement mistake, rather than only
fixing the coordinates - three of the seven farms on the old map turned out to be
misplaced once the road-clearance rule existed, and only one of them was visible.

## Out of scope

`pf_generator/generate_soil.py` is pure seeded noise with no connection to the terrain.
`FS25_Granja_bonita/` is the mod folder itself; nothing in this pipeline writes into it,
and getting `dem_new_12k.png` in there is a separate import step.
