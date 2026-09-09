# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A generator for a Farming Simulator 25 map. It produces two artefacts that a human then
imports into Giants Editor: a 16-bit heightmap PNG and an OSM vector file. There is no
application to run and no test suite - the verification is two acceptance scripts that
exit non-zero.

**What is on the map.** The uplands are a till plain after the country round Royal in
Clay County, northwest Iowa - Des Moines Lobe ground: three octaves of gentle, aimless
swell and swale, with the middle one stretched 2.6x along a northwest-southeast grain so
the recessional moraines read as the lines an ice edge stops on rather than as hills. It
runs 72 to 92 m, about 20 m of relief across the 8 km of the map, and slopes under 3
degrees on the flats. The real place is pitted with prairie potholes, and they are
deliberately not here: closed depressions a metre or two deep read as craters at any
vertical exaggeration that makes the rest of the relief visible.

A river runs the whole length of the map from north to south, meandering about x = 3900,
and widens into a 172 ha lake in the northern quarter with a wooded island in the middle
of it. Both sit in a valley of their own: the ground falls 22 m away from the water over
500 m either side and is back on the till plain beyond that, and the valley takes 21% of
the playable area. The river is cut 5 m into its valley floor (2 m of bank over 3 m of
water) and the lake 40 m (2 m of bank over 38 m of water), so the lake bed is at 20 m.

The 2048 m border is the wall of a larger valley the whole map sits in: two mountain
ranges in the east and west border rising to summits at 250 m with saddles at 200 m
between them, and a sill north and south at about 115 m where the valley runs on out of
the map - notched where the river crosses it, or the rim would dam the channel. The flat
apron between the playable boundary and the first slope is 100 m, the same width as the
clean strip inside it.

The roads are the Public Land Survey System, which is why a map of the American midwest
looks the way it does: the ground was subdivided into one-mile sections before anyone
built on it, and the roads went on the section lines. Two trunk roads run the length of
the map a mile in from the east and west edges - which also puts both of them clear of
the water - and five section lines run east-west between them on the mile grid. Only the
outer two are bridged; the three in the middle run down to the river and stop on the
bank, which is what a section-line road without a crossing does, and it is what makes the
trunk roads carry the through traffic. 56 km of road, two bridges, and 12 km of town
street on top of that.

The mile grid has one thing to be told about, and it is the lake: it is 1290 m across and
the sections are 1609 m apart, so there are only 319 m of anchor to choose from that keep
a section line out of the water. `PLSS_EW_ANCHOR_M` centres that window.

There are four towns, and the road grid puts them where they are: the two bridged section
lines are the only east-west roads that get across the river, so their four junctions with
the trunk roads are the only points on the map where two through routes cross, which is
where a midwest town stands. Each is a 4x4 grid of 100 m blocks with the trunk road running
up the middle of the grid - the middle street line *is* the highway, so main street is the
through road - and the section line does the same across it, which puts the junction the
town is named for at the crossroads in its centre with two columns and two rows of blocks
on every side of it. What a road takes out of the block grid is its own nominal feather
either side of the centreline, the same clearance everything else is held off a road by,
so a primary takes 28 m of grid where a section line takes 22 and a town street 16 -
which is the whole reason a town of square blocks comes out 516 m across and 510 m from
end to end rather than square itself, and why the blocks still measure exactly 100x100
whatever class of road happens to bound them. Eight streets per town, 15.1 km of them;
the whole thing stands on a 516x510 m platform levelled out of the till plain with a
third of a percent of fall to the south so
it drains. That is a grade and not a height, and the checks on it are written the same way,
because the platform is sized from the block grid: change the block and the fall end to end
changes with it.

Eighteen square yards hang off the road grid: twelve industrial aprons (ten of 2 ha, two
of 5 ha) and six 20 ha farms. They are one kind of thing built by one piece of code -
`roadside_pad` - and what separates a granja from an apron is data on the record: its area,
its tags, and which classes of road it may stand on. A farm stands **only on a section
line**, which is not decoration: the trunk roads carry the through traffic, and 447 m of
yard gate opening onto one of them is a thing nobody builds. All of them stand 10 m off the
**edge of the running surface** rather than off the centreline,
because that is what "ten metres off the road" means to anyone standing on it. Against a
primary that puts the fence 15.5 m from the centreline and against a section line 14 m,
and both clear the per-class road clearance the plantings use (14 m and 11 m) - so a
yard placed this way is inside the letter of the brief and outside the verge of the road,
which is the only way both can be true at once. Square and a stated area means the side is
neither number but what falls out of them, `sqrt(area)`: 141.42 m, 223.61 m and 447.21 m.
A site is recorded as *which road, how far along it, which side, how many hectares*, and
the rectangle is worked out from the road's own record - write the corners out as four
numbers instead and the day a road moves, the yards stay where they were, eighteen of them
in the middle of a field with no way in. Four small aprons on the trunk roads and six on
the section lines so every one of the five carries one; both large aprons on the trunk
roads, where the traffic a five-hectare operation generates has somewhere to go.

Where a 447 m farm can go is decided almost entirely by what is already on the map. The
river's valley reaches 500 m either side of a channel that swings between x = 2791 and
x = 5089, so the whole middle third of every section line is out; a trunk road runs up
each side of that; and the four towns and the twelve aprons take their own ground. What is
left is a window either side of each trunk road, and the six farms sit in it.

Five 20 ha woods run along the road grid on the same 10 m setback: rectangles laid
long-side-on along their road, `WOOD_ASPECT` times as long as they are deep, so 632 by
316 m. Both sides fall out of the area and the aspect and nothing else - `sqrt(area/aspect)`
deep by `aspect` times that - with no rounding anywhere in it. They are the first thing on
the map that is *only* vectors: nobody levels ground to grow trees on, so a wood is an
`AREAS` ring with no pad, no feather and no drain, and the heightmap does not change when
one is added. Every wood on the map, the island included, is tagged
`leaf_type=needleleaved` and every shelterbelt `leaf_type=broadleaved` - a windbreak on a
field boundary is hardwood - and both renderers colour a conifer wood apart from a
broadleaf one, so the tag is not dead weight and the two kinds of planting read apart.

The one rule a wood has that a yard does not is that the 10 m clearance is held against
**every** alignment on the map and not only the road it hangs off. A road through the
middle of a wood is perfectly realistic ground and is still wrong here, because it makes
the setback the wood was placed with meaningless - trees held off one road by ten metres
and bisected by the next.

Eleven shelterbelts, 100 m across: five running north to south and six east to west.
Neither length is a choice, and they are different numbers for a reason worth knowing.
A north-south belt runs from one cross road to the next held off each end by that road's
clearance: the mile less twice 14 m, 1581.3 m, landing on the survey the whole map is built
on. East to west there is no such grid - the two trunk roads are 4973 m apart, three
sections and a bit, and the river's valley runs down the middle of what is between them -
so a transversal belt takes its length from the *band* a trunk road and the map edge leave
instead: the mile less the clean strip and the road's own clearance, 1493.8 m, and identical
east and west because the trunks are a mile in from either edge. The middle band has no such
length, which is why no belt spans it: one end would be on a road and the other wherever the
meander happened to be at that station.

They stand on the lines the survey already put there - the half-section lines, where a field
boundary falls and therefore where a windbreak goes, and the frontage of the roads. Which
slots are free is not obvious by eye and was not guessed: all fifty (line, section) and
(band, row) combinations go through the placement rules, and four towns, eighteen yards and
five woods already stand on this grid. A *row* carries a transversal belt only if it can
carry one on both flanks of the map, so what lands is whole rows rather than whichever slots
happened to be left over. The nearest miss on the north-south side is `este_media` section 1,
where the river's east swing brings the belt to 493 m of open water against the 500 m a
planting is held off - seven metres, on a rule that could be moved. It is not moved.

The ground between all of that is parcelled into 145 fields, and they are laid out the
way the ground was actually subdivided: by aliquot parts of a section. A section is a
mile square, 259.0 ha, and the survey halves it and halves it again, so the sizes are not
chosen - a quarter section is 64.75 ha, a forty 16.19 and a ten 4.05, and those are the
brief's large, medium and small to within a hectare. Which one a cell is offered at comes
from its distance to the nearest town, because land near a settlement is worth more and
was sold in smaller pieces. If anything is in the way - a road and its verge, a yard, a
wood, the clean strip, the water's valley - the cell is trimmed if the obstacle is along
an edge and quartered if it reaches inside, which is what fills the awkward corners with
whatever aliquot fits rather than with a special case per obstacle. Afterwards, two
parcels that share a *whole* edge become one field, up to one halving over the band they
stand in, because a farmer working two forties as one does not consult the survey - and
then every field comes in by half of `FIELD_GAP_M` on all four sides, so the ones the
merge did not join stand 5 m off each other instead of sharing an edge. That order is
load-bearing: inset first and there are no whole shared edges left for the merge to find,
nothing is ever joined, and the count goes over two hundred.

Two things about it are not the survey's. The first is that **the fields stay out of the
water's valley entirely** - the full `VALLEY_HALF_W_M`, the same clearance every yard,
wood and belt on the map is already held off the water by, rather than coming down the
valley side to the top of the bank. The ground down that side is under five degrees and a
machine would work it; holding the fields off it costs 901 ha, a fifth of everything the
parcelling lays out, and it is the whole reason the corridor reads as floodplain. The
second is the **count**, `FIELD_MAX_COUNT`: at most 150 fields on the map, which is the
one number here that is a ceiling somebody chose rather than something the survey
implies. It binds, and it is what every band was set against - the parcelling was run and
counted, because the count falls out of the geometry rather than out of any constant and
is not even monotone in the obvious direction: merging one step harder cuts twenty fields
and covers exactly the same ground. `validate()` holds the cap, so the next feature added
to the map cannot quietly push it over. What lands is 145 fields of 3.3 to 94.2 ha,
3252 ha in all, 49% of the playable square.

The rest is the technical base: the projection, the coordinates, the canvas geometry, the
16-bit centimetre encoding, the geometry primitives, the terrain operators in
`terrain_ops.py`, and the two acceptance harnesses. The previous map - northwest Iowa
farmland around Royal, Clay County, with a river, a glacial lake, the PLSS road grid, a
branch line, three villages, seven farmsteads and 118 fields - was cleared out
deliberately; it is in git history at commit `8d11754` if a piece of it is worth reading
back.

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

The rim is the one thing outside that rule, and only because none of it is ground the
player can reach: it is parametric, its constants are the `RIM_*` block in
`map_layout.py`, and its one implementation is `terrain_ops.rim_field`, which the
generator shapes it with and `measure_elevation.py` reads back to find the strips it
reports on. It emits no vectors. Anything inside the playable boundary is not the rim and
belongs in a registry.

Everything the world is made of goes in four registries. `WATER` carries the river and the
lake, `CORRIDORS` the roads and the town streets, `PADS` the four town platforms, and
`AREAS` the island and the town blocks:

    CORRIDORS    roads and railway - alignment, class, platform width, bridge spans
    WATER        rivers, creeks, lakes - centreline or shore, drawn half-width, depth
    PADS         levelled platforms - centre, size, ring, feather
    AREAS        tagged rings the OSM draws - island, town blocks, woods, shelterbelts

A pad is a piece of *terrain* and nothing else: it says where the ground was levelled, not
what stands on it. A town's drawn form is its blocks, which are `AREAS` rings, the same way
the island is an `AREAS` ring rather than something hanging off the lake record. A pad that
does want a footprint of its own carries a `tags` key and `emit_pads` draws it; the towns
do not, or every block would be drawn twice.

The record each one holds is documented above it in `map_layout.py`. Both halves enforce
the rule rather than trusting it: `measure_elevation.py` fails if a platform in the layout
was levelled with nothing drawn over it, and `check_osm.py` fails if the layout and
`map.osm` disagree about whether the map is empty.

Where the OSM side needs to know about the ground - anything that sizes itself to how
broken the ground is - the DEM publishes `dem_generator/terrain_stats.json`, a 128x128
height and roughness grid, and `map_layout.load_roughness()` reads it with the standard
library. Do not import numpy into `osm_generator/`, and do not re-derive the terrain
there.

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

Heights are 16-bit centimetres: raw 8200 = 82.00 m. `map_layout.BASE_ELEV_M` is the
**mean** of the till plain, not a height anything actually sits at, and it is the ceiling
of every excavation on the map. The lake bottoms 62 m under it; at the 20 m datum this
started with, that bed lands at -42 m, the encoding clips everything under zero, and the
lake comes out as a flat pan at 0.00 m that still measures 40 m deep in any average you
take of it. Raising the datum is the whole fix - the rim is quoted as a lift and the
water hangs off the datum, so nothing else cares where it sits. 82 m leaves 20 m of
headroom under the deepest point and 370 m of fill over the summits.

Because it is a mean and not a height, **nothing under it may be quoted against it**. The
till plain swings about ten metres either side, so the valley is anything from 12 m to
32 m deep along its length, and every line of the cross-section closes on the local
ground or the local water surface. The one time this was written as a constant it
subtracted the difference from every acre within half a kilometre of the water.

## Things that have already gone wrong here

Each of these was a real bug found by measurement, not by looking at the output. Some of
the code that hit them went out with the Iowa map, but the mistakes have not gone
anywhere: they are what any terrain written back into this pipeline will hit again, in
the same order.

- **Offsetting a polyline** by more than its radius of curvature folds the ring through
  itself, and an even-odd fill then punches holes in the tightest meanders. Reserves along
  water are stamped by distance to the centreline, never as offset polygons.
  `map_layout.offset_polyline` carries the warning in its docstring.
- **An occupancy raster judges a cell by its centre**, so anything narrower than the cell
  - a 24 m shelterbelt on a 32 m grid - can fall between two centres and mark nothing at
  all. Thin shapes need a fill that also walks the boundary, and reserve radii want
  growing by half a cell diagonal so "no field within R of the water" is true rather than
  nearly true.
- **A monotone polyline can be sliced, and this one is 90% of the module's load time.**
  `water_crossings` walks every alignment densified to 10 m against 322 river vertices and
  97 lake ones, for every corridor on the map and again in `validate()`: 29 of the 36
  seconds importing `map_layout` used to take. The river is a single-valued function of
  northing, so its axis comes out sorted in y and a point can only be within `clearance` of
  the stretch inside its own band - a bisect slice with a vertex of margin at either end,
  exact here and *not* exact on a watercourse that doubled back. With that and a bounding
  box on the lake it is 2.3 s. Worth knowing before adding anything else that walks the
  water: the naive version is quadratic in how much map there is.
- **A vertex-in-polygon test does not find two rectangles crossing in a plus sign.** The
  overlap rule that keeps one planting off another tested whether any vertex of either ring
  lay inside the other, which is right for one shape containing or clipping another and
  silently blind to a cross: neither rectangle has a corner inside the other. The first
  transversal shelterbelt laid over a north-south one and the check said nothing - two
  `natural=wood` polygons over the same ground, drawn twice and counted twice in the
  inventory, and the only reason it was caught is that a crossing belt is visible in the
  layout picture. Test the edges as well (`map_layout.rings_overlap`). The general shape of
  this: a predicate written for the containment case will pass every crossing case, and
  crossing is what a grid of anything produces.
- **Built ground is not the valley.** The measurer kept one mask for "not the till plain"
  and used it for two different questions: excluding graded ground from the landscape
  checks, which wants the roads and the yards in it, and selecting the water's valley for
  the roughness comparison, which does not. Folding a couple of hundred hectares of
  dead-flat platform into "the valley" dragged its mean roughness from 0.350 toward the
  flats, and it got worse with every yard added - the check had reached 0.299 against a
  0.310 floor when it finally failed. A check a feature can turn green by being added to
  the map is not a check. Two masks: `valley` is the water's valley and `natural` is that
  plus everything built. It also fixed a number that had been quietly wrong for as long -
  "how much of the map the water's valley takes" read 31.4% for a valley that is 21%.
- **Size a transition band from where the transition starts, not from where you are
  standing.** The check that a levelled yard is the plane it was graded to has to skip the
  strip the road's own platform reaches into - corridors are graded *after* pads precisely
  so the road wins where they meet - and how wide that strip is comes from the generator's
  own identity, `max(nominal, 1.5*|dz|/tan(4 deg))`. Measuring `dz` at the fence rather
  than at the road makes the whole thing circular: what you read there has already been
  attenuated by the very smoothstep whose width you are solving for. A road running 98 cm
  under a farm's platform showed 29 cm at the fence, which sized the band at 1 m instead of
  11 and handed the check the driveway to judge as yard. Read it at the road. And keep the
  nominal floor in: a primary's platform reaches 14 m off its centreline however little it
  is cutting, so a band sized on the widened term alone comes out under the setback for
  every gentle gate on the map and excludes nothing at all.
- **An alignment's reach is its bounding box, not an infinite stripe.** The measurer's
  mask for "ground a platform touched" tested `|x - axis_x| <= reach` and nothing else,
  which is right only while every road runs the length of the canvas. The moment a 270 m
  town street existed it masked the whole 12 km column it happens to be parallel to, and
  eight of those per town would have written off a quarter of the uplands as road - so
  every landscape check would have been measuring a smaller and smaller map with nothing
  to show for it. Bound it in both directions. And grow the reach past the *nominal*
  feather, because the generator widens a feather to `1.5*|dz|/tan(4 deg)`: a mask sized
  at the nominal misses exactly the ring of steepest ground the platform made.
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
- **A road that stops is not a stripe, so trim the platforms off a cell before the
  roads.** A box in the parcelling knows its extent in both directions and can only reach
  a cell it truly touches; a corridor is tested as a band along its axis, with the reach
  it has *along* that axis, against the cell as it arrived. Every town street stops at
  its town - so for the ten north of a town, whose only overlap with those streets is the
  strip the town's own platform takes out, the streets were counted first and ate a
  201 x 137 m band out of ground they never come near. It left 1.26 ha, under any floor,
  and the towns stood in 147 m of nothing to the north and south with a field at the
  ten-metre headland to the east and west - the asymmetry is the tell, because a street
  grid is symmetric and the trim order is not. Take the platform off first and the street
  no longer reaches. The general shape: an obstacle tested against the *untrimmed* cell
  is being asked a question about ground that is already gone.
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
  its way out of the map.
- **A profile that runs along the rim needs a coordinate that goes round it.** Making the
  ridge line a function of `y` on the east and west ranges and `x` on the north and south
  sills, blended between them, looked right and put a bright crease diagonally out of two
  corners: inside one corner the blend swings the coordinate the whole length of the map,
  so nine kilometres of ridge line lands in two, and the measured slope there was 25
  degrees against 14 everywhere else. `rim_field` returns the fraction of the way round
  the perimeter of the playable square instead, taken at the nearest point on it, so a
  corner is simply constant. That coordinate wraps, which is why the lobe counts are whole
  numbers and the wavelengths fall out of them - a fractional lobe steps the crest where
  it closes at the north-west corner.
- **Build the crest in its own band, then map it to metres.** Adding relief on top of a
  finished ramp pushes the summits through whatever ceiling the brief set; varying the
  crest *height* instead gives the flank its spurs for free, because the ramp scales the
  horizontal variation on the way down. Contain it with a `tanh` rather than a clip: a
  clip flattens the top of every summit that reaches for the ceiling into a plateau at
  exactly the ceiling.
- **Surface texture stays off running surfaces and channels.** 4 cm of micro-relief is 8 cm
  over the 25 m the ruling grade is measured across, a quarter of a railway's budget. It is
  off entirely on the blank base (`MICRO_AMP_M = 0`), along with the dither.
- **A normalised radius is not a distance.** `ellipse_r` returns 1 on the shore, and
  turning that into metres by multiplying by a mean radius is only right on a circle. On
  a lake half again as long as it is wide, with a lobed shore on top of that, it
  compressed the section by a fifth on the short axis and more where a lobe turned: the
  valley came out 400 m wide instead of 500 and its rim measured 12 degrees against the 5
  it is built to. Divide by `terrain_ops.grad_mag` of the field and it becomes an
  approximate signed distance, which is what every section built on it assumed it was.
- **The rim rides on the landscape, so judge it on its lift and not on its height.**
  It is added by addition, which is the whole point - a summit standing on a moraine
  stands the moraine's height higher. Its absolute elevation then says as much about the
  ground under it as about the rim, and an acceptance band written in absolute metres
  either fails on ground that is simply high or has to be widened until it checks
  nothing. `measure_elevation.py` takes the apron height on the same line and reports
  `crest - foot`.
- **A published scale has to discriminate over the ground the map actually has.**
  `terrain_stats.json`'s roughness was normalised at a 3% gradient, which was fine while
  the uplands were a flat plate. The moment they had relief of their own, every cell on
  the map read 1.000 - the parcelling would have sized every field the same and nothing
  would have looked wrong in either output. Six degrees puts the flats near zero, the
  moraine flanks in the middle and the valley sides at the top.
- **Quote the cross-section against the local water surface, never against a constant.**
  The valley's outer term was written as `BASE_ELEV_M - LAKE_WS_M - WATER_BANK_M`, which
  is only right where the water is at the lake's level. Everywhere else the section
  landed at `ws + 17` instead of at the floodplain, so the river's own 2.5 m of fall was
  being subtracted from every field within a kilometre and a half of it, with a 1.7 m
  step where the distance search stopped looking. The playable square measured 73.4 m
  over ground that is 75, and the hillshade showed a perfectly plausible valley.
- **`soft_min` against a *tangent* surface digs a moat.** The water's cross-section
  arrives at the floodplain with zero first and second derivative and stays there, so
  over the whole outer region the two surfaces are equal - and `soft_min`'s `k*h*(1-h)`
  term, which is a penalty for being close, then charges the full `k/4` and cuts a 37 cm
  ditch along both rims of the valley. Against tangent surfaces a hard `np.minimum` is
  exact. Give the floodplain relief of its own and they stop being tangent, and it has to
  go back to `soft_min`.
- **Arrive at flat ground with `smootherstep`, not `smoothstep`.** A smoothstep leaves a
  jump in curvature where the sculpted surface meets the datum, and the order-3 spline
  that resamples 4 m to 1 m rings on it. What is left is a low levee running the length
  of the valley rim - invisible on a 15 m valley and exactly the wrong shape.
- **Sculpt only what the synthesis grid can hold.** A 56 m river puts its submerged bank
  inside 17 m of a 4 m grid, and what came out was a trough whose waterline sat 25 cm
  below where the vectors drew it and three metres further out - the exact disagreement
  `water_half_w` exists to prevent. Ninety metres across resolves. The 110 m floor in the
  docs above is about the terrain; a channel is the one thing allowed near it, and it is
  allowed by being measured rather than assumed.
- **Fade the channel out over the island, not at the lake shore.** Switching the river
  off where it enters the lake is the obvious place and it put the steepest slope on the
  playable map straight across the river's mouth: the channel arrives 3 m under the
  waterline and the lake's littoral shelf is barely wet that close in. Left alone the
  minimum of the two scours a channel across the shelf and hands over to the basin as the
  basin deepens, which is what a river entering a lake does. The island is the one place
  the bed comes back up over the channel, and that is what the fade is for.
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
brief. That is why the rim report calls `rim_field` for its strips rather than slicing
the outer 150 m off each edge and calling it the crest: "the west range" then means the
same thing in the report as it did in the build. The numbers themselves are all read back
out of the PNG.

Four of the water checks failed first time round on the measurement and not on the
terrain, which is the usual ratio. The axis runs `EXTEND_M` past the canvas so it does
not end at a cliff, and sampling it there made the river appear to run 2.35 m uphill,
because the sampler clamps to the canvas and a bed read off the sill beside it is not a
bed. A strict inequality between two stations 40 m apart on a 0.016% grade asks the
centimetre the heightmap is quantised to a question it cannot answer. Five hundred metres
out from the outside of one meander is inside the valley of the next, which is a
floodplain doing what floodplains do. And a 64 m roughness cell measured over a 40 m
baseline sees into a valley its centre is clear of.

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
renderers drop anything else without a word.**

That is about what makes a way *drawable*, not about what a drawable way may carry.
`building=industrial` on an apron and `leaf_type=needleleaved` on a wood are attribute tags
on rings that `landuse=farmyard` and `natural=wood` already put on the map, and both are
read - one or other renderer colours them apart. An attribute no renderer reads is the one
thing not worth emitting; a way whose *only* tags are outside the vocabulary is the thing
`check_osm.py` fails the build over. That is why floodplain pasture carries no
tag - it is simply ground nothing is drawn on. `check_osm.py` fails the build if a way
was emitted that neither renderer can see.

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
`BASE_ELEV_M` (the mean of the till plain, 82 m), `PLAYABLE_M`/`CANVAS_M`, `EDGE_CLEAR_M`,
`LAT_CENTER`/`LON_CENTER`, `SEED`, and the `RIM_*` block - `RIM_APRON_M` (100 m of flat
past the boundary), `RIM_CREST_M`/`RIM_SADDLE_M` (the summits and the saddles between
them), `RIM_MOUTH_M` (the sill the valley leaves over), the lobe counts, and
`RIM_WEST_SMOOTH`, which damps the west range's crest toward the middle of its band so
that side reads as a wall rather than a row of scallops (1.0 makes the two sides alike).
Turning the valley through ninety degrees is one edit: swap `dx` and `dy` in the range
weight `w` in `terrain_ops.rim_field`.

The water has its own block: the meander amplitudes and wavelengths (`RIVER_A1`/`RIVER_L1`
and the shorter pair riding on them), `RIVER_HALF_W_M`, `RIVER_DEPTH_M`, `RIVER_FALL_M`,
the lake's centre, semi-axes and shore harmonics, `LAKE_DEPTH_M`, the island, and the
three that shape the ground around all of it - `VALLEY_HALF_W_M`, `VALLEY_DEPTH_M` and
`WATER_BANK_M`, plus the `LAND_*` block that shapes the till plain itself. Everything under the uplands is quoted relative to the water surface
at that station, so the whole corridor tilts with the river instead of drowning at one
end of the map. `RIVER_NOTCH_HALF_M` has to stay wider than the valley or the sill rides
up on the valley side and dams the channel; `validate()` enforces that, along with the
meanders' radius of curvature against the drawn half-width, the island being inside its
lake with a shelf's worth of clearance, and the bed clearing the floor of the encoding.

The towns are `TOWN_BLOCK_W_M` and `TOWN_BLOCK_H_M`, `TOWN_COLS` and `TOWN_ROWS`, the
`TOWN_STREET` class record, `TOWN_PAD_MARGIN_M` and `TOWN_PAD_FEATHER_M` for the platform
and `TOWN_DRAIN_GRADE` for the fall across it. `TOWN_SITES` says which junctions they
stand on and `validate()` asserts that each one really is a trunk road against a *bridged*
section line, that the blocks come out at the size they are quoted at, and that the town
clears the river's valley by its own half-diagonal. Moving a town is one line of
`TOWN_SITES`; changing the block grid is two constants and every street, block and
platform resizes around them.

The roadside yards are `INDUSTRY_SMALL_HA`, `INDUSTRY_LARGE_HA` and `FARM_AREA_HA` (the
*areas* - the side comes out of `yard_side`), with `ROADSIDE_SETBACK_M`, `YARD_FEATHER_M` and
`YARD_DRAIN_GRADE` shared by all of them: a fence ten metres off the road is ten metres off
the road whatever is behind it. `INDUSTRY_SITES` and `FARM_SITES` say which road each one
hangs off, how far along, which side and how big. Moving one is one number in that table;
resizing one is another. `validate()` measures the ring against the road's axis rather than
trusting the arithmetic that built both - so it catches the setback being taken from the
centreline instead of the kerb, a yard that is not square or not its stated area, one whose
corner has landed inside the water's valley, one standing on the verge of the class of road
it is on, one another road runs through, and a farm that has wandered onto a trunk road.

The shelterbelts are `SHELTER_W_M` and `SHELTER_LEAF_TYPE` - the only free numbers in
them, both lengths coming out of `MILE_M` and a clearance - with `SHELTER_LINES`/`SHELTER_SITES` for the north-south ones
and `SHELTER_BANDS`/`SHELTER_ROWS`/`SHELTER_EW_SITES` for the transversal. `validate()`
holds the width and the length against the drawn ring, and holds each belt to the
orientation its length was derived for.

The woods are `WOOD_AREA_HA`, `WOOD_ASPECT` and `WOOD_LEAF_TYPE`, with `WOOD_SITES` in the
same shape as the yard tables. `validate()` holds the area against the drawn ring rather
than against the formula that built it, along with the setback, the clearance from every
other alignment, the water's valley, and every pad and wood it might overlap. The setback
is measured off the ring too, not off a half-side: it comes to the same thing for a
rectangle and it did not for the lobed outline these were drawn with first, so it stays
measuring the ring - the shape is one constant away from changing again.

The parcelling is `FIELD_MAX_COUNT` (the ceiling on how many fields there may be, and
the constraint every other number here was set against), the two band radii
`FIELD_SMALL_M` and `FIELD_MEDIUM_M`, the aliquot caps `FIELD_CAP_SMALL_HA` /
`_MEDIUM_HA` / `_LARGE_HA` that fall out of `FIELD_SECTION_M`, `FIELD_MERGE_MAX_HA` and
`FIELD_MERGE_STEPS` for joining two parcels into one field, `FIELD_MIN_HA` /
`FIELD_MIN_SIDE_M` for what is worth drawing at all, `FIELD_CLEAR_M` for the headland off
anything built, `FIELD_GAP_M` for the headland between two fields, `FIELD_RIVER_CLEAR_M`
/ `FIELD_LAKE_CLEAR_M` for the water, and the two that decide trim-versus-split,
`FIELD_KEEP_FRAC` and `FIELD_SPLIT_GAIN`. Coverage is almost flat in all of them - every
setting reaches 3250-3300 ha - and what they buy is how finely that ground is cut up, so
tune them against the count and not against the acres. `validate()` measures the output
rather than the rules: that every field is an axis-aligned rectangle inside its size band,
that no two overlap, that none stands closer than `FIELD_GAP_M` to another, that none is
in the water's valley, and that there are no more than `FIELD_MAX_COUNT` of them.

The roads are `MILE_M` and what hangs off it: `ROAD_MAIN_INSET_M`, `PLSS_EW_ANCHOR_M`,
`PLSS_BRIDGED` (which section lines get a crossing), `ROAD_STUB_SETBACK_M` (where the
ones that do not stop), and the two class records `ROAD_PRIMARY` and `ROAD_SECTION` that
carry the running width, the nominal feather and the ruling grade. `validate()` asserts
the survey rather than trusting it - a section grid that is not a mile is not a section
grid - and it refuses an alignment that stops inside the canvas anywhere but on a bank.

Feature constants come back beside the features they belong to.

`map_layout.validate()` is the gate: it returns complaints and both generators refuse to
run when it does. Add a rule there when you find a placement mistake, rather than only
fixing the coordinates - three of the seven farms on the old map turned out to be
misplaced once the road-clearance rule existed, and only one of them was visible.

## Out of scope

`pf_generator/generate_soil.py` is pure seeded noise with no connection to the terrain.
`FS25_Granja_bonita/` is the mod folder itself; nothing in this pipeline writes into it,
and getting `dem_new_12k.png` in there is a separate import step.
