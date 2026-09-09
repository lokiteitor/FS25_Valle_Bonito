#!/usr/bin/env python3
"""The layout of the map: where everything is, in playable metres.

This is the one source of truth shared by the two halves of the pipeline. The DEM
generator sculpts terrain around the geometry defined here; the OSM generator writes the
same geometry out as vectors. If the two disagree - a river carved where no river is
drawn, a farm pad flattened where no farmyard exists - the map is broken in a way that is
invisible in either output on its own, so both read their geometry from this module and
neither is allowed to invent its own.

**What is on the map so far.** The uplands are a till plain after the country round
Royal in Clay County, northwest Iowa. A river runs the length of it from north to south
and widens into a lake in the northern quarter, with a wooded island in it; both sit in a
valley of their own cut into the floodplain. The roads are the Public Land Survey System
- two trunk roads a mile in from the east and west edges, five section lines on the mile
grid between them, of which only the outer two are bridged. And on the four corners where
a trunk road meets a bridged section line there is a town: a grid of blocks with the
trunk road running up the middle of it, standing on a platform levelled out of the till
plain. Hung off the roads are twelve industrial aprons and six farms, all square; five
woods and eleven shelterbelts stand along them. The ground between all of that is not
parcelled: it carries no tag and is simply open country the map leaves alone.

**What the ground already is** is the frame all of that goes in: the border around the
playable square is the wall of a valley. Two mountain ranges stand in the east and west
border and rise to `RIM_CREST_M`; the valley runs north to south between them and leaves
the map over a low sill at either end. That is a parametric shape rather than a registry
entry - the constants are in the rim section below, the one implementation of the shape
is `terrain_ops.rim_field`, and no vectors are drawn for it because none of it is
playable ground.

To add to the map, fill the registries (`CORRIDORS`, `WATER`, `PADS`, `AREAS`) and add
the rules that have to hold about them to `validate()`. Both generators refuse to run
while `validate()` complains, which is what keeps a placement mistake from reaching the
editor. The record each registry holds is documented above it.

Coordinates are playable metres: x east, y south from the north edge, so the centre of
the playable area is (4096, 4096). The DEM canvas is larger than the playable area, so
canvas coordinates run from -2048 to 10240 in the same frame. Those numbers, and the
projection that ties them to lat/lon, are unchanged from the Iowa map: a heightmap and an
OSM built from this module still line up with each other and still import into Giants
Editor the way they always did.

Standard library only. The scripts in `osm_generator/` run without numpy, and the DEM
generator needs the same numbers, so nothing here may depend on anything else. Keep the
alignments free of floating-point randomness too: a river meander that comes out of a
closed formula gives both halves of the pipeline the identical polyline, and one that
comes out of an RNG does not.
"""
import bisect
import json
import math
import os
import random

# --- where the map is -------------------------------------------------------------
# The projection anchor. Unchanged: moving it moves every node in map.osm relative to
# the heightmap, which no check downstream would catch.
LAT_CENTER = 43.0600
LON_CENTER = -95.2800

# --- how big it is ----------------------------------------------------------------
PLAYABLE_M = 8192.0
HALF_M = PLAYABLE_M / 2.0
CANVAS_M = 12288.0
OFFSET_M = (CANVAS_M - PLAYABLE_M) / 2.0      # 2048 m of margin on every side

# How far past the canvas the road, rail and river alignments run. Terrain features that
# stop at the canvas edge leave a valley dying in mid-air or a road ending at a cliff.
EXTEND_M = 300.0
EDGE_MIN = -OFFSET_M - EXTEND_M               # -2348
EDGE_MAX = PLAYABLE_M + OFFSET_M + EXTEND_M   # 10540

# A clean strip inside the playable boundary. Nothing the planting places may stand in
# it: no wood, no shelterbelt, no yard. Only the things that have to leave the map cross
# it - the roads, the railway and the water. Without it a ring is cut off square by the
# boundary and reads as half of itself, and the ground the player sees at the edge is
# the ground the rim rises out of.
EDGE_CLEAR_M = 100.0

# --- the datum --------------------------------------------------------------------
# The elevation of the floodplain: the flat ground of the till plain, everywhere the
# valley of the water does not reach.
#
# It is 75 m and not the 20 m it started at because of what gets cut into it. The datum
# is the ceiling of every excavation on the map, and the lake bottoms 62 m under it -
# 22 m of valley, 2 m of bank and a 38 m water column. At a 20 m datum that bed lands at
# -35 m, the 16-bit centimetre encoding clips everything under zero, and the lake comes
# out as a flat-bottomed pan at 0.00 m that still measures 20 m deep in every average
# you could take of it. Raising the datum is the whole fix; nothing else on the map cares
# where it sits, because the rim is quoted in absolute metres and the water hangs off the
# datum. It is 82 and not 75 because the uplands stopped being a flat sheet: the datum
# is now their *mean*, the valley had to deepen to 22 m to stay under the low ground, and
# the lake bed followed it down. 82 m puts that bed back at 20 m with 20 m of headroom.
BASE_ELEV_M = 82.0

# --- the non-playable rim ----------------------------------------------------------
# What the player sees past the boundary: the map is the floor of a valley running north
# to south between two mountain ranges. The ranges stand in the east and west border,
# and the valley leaves the map over a low sill at the north and the south, so the
# horizon is closed on two sides and open on the other two. The first RIM_APRON_M past
# the boundary is left flat, so the ground does not change character at the edge of play
# and the playable square keeps its own relief right up to the boundary.
#
# These are **absolute** elevations, not lifts. The valley floor is BASE_ELEV_M and the
# summits are RIM_CREST_M, so every pixel of the canvas lives between 20 m and 250 m.
#
# Turning the valley through ninety degrees is one change: swap dx for dy in the range
# weight `w` in `terrain_ops.rim_field`. Nothing else here knows which way it runs.
RIM_APRON_M = 100.0           # flat apron between the boundary and the first slope
                              # - one EDGE_CLEAR_M, so the clean strip inside the
                              # boundary and the apron outside it are the same
                              # width and the mountains start as soon as the
                              # ground stops being ground the player works
RIM_BACK_M = 150.0            # crest shoulder held at full height inside the canvas edge
RIM_CREST_M = 250.0           # the highest summits
RIM_SADDLE_M = 200.0          # the lowest saddle along a range crest
RIM_MOUTH_M = 115.0           # the sill the valley leaves the map over, north and south
RIM_MOUTH_VAR_M = 8.0         # how far the sill rolls either side of that
RIM_RIDGE_LOBES = 13          # summits around the whole rim - see below, must be whole
RIM_RIDGE_BEAT = 23           # a second, coprime, so no two summits come out alike
RIM_SPUR_LAM_M = 1100.0       # spurs and re-entrants down the flank
RIM_SPUR_AMP = 0.30           # ... as a fraction of the saddle-to-crest band
RIM_ROUGH_LAM_M = 520.0       # the coarsest thing on the flank below a spur
RIM_ROUGH_AMP = 0.10

# How much of that relief the *west* range keeps. The two ranges are not asked to read
# alike: the west one is the one the morning light rakes across, and at full amplitude
# its crest wanders the whole 50 m between saddle and summit and the flank comes down as
# a row of scallops rather than a wall. Damping pulls it toward the middle of the band
# without narrowing the band itself, so the east range still carries the summits. 1.0 is
# the two sides identical.
RIM_WEST_SMOOTH = 0.40

# Derived. The flank climbs the whole crest over RIM_RAMP_M: the apron eats the first
# stretch of the border and the shoulder the last, and what is left is the slope itself.
RIM_HEIGHT_M = RIM_CREST_M - BASE_ELEV_M      # highest summit, above the boundary ground
RIM_RAMP_M = OFFSET_M - RIM_APRON_M - RIM_BACK_M

# The ridge line is a profile around the perimeter of the playable square, so it has to
# come back to where it started: a whole number of lobes around the ring, or the west
# edge arrives at the north-west corner half a summit away from where the north edge
# leaves it and there is a step in the crest. That is why the lobe counts are integers
# and the wavelengths are what falls out of them, rather than the other way round.
RIM_PERIM_M = 4.0 * PLAYABLE_M
RIM_RIDGE_LAM_M = RIM_PERIM_M / RIM_RIDGE_LOBES

# The ceiling the flank is built to. In a smoothstep the steepest gradient is
# 1.5 * rise / run, the same identity that sets every platform feather, so the ramp's
# run is what keeps the mountains walkable rather than a wall. `validate()` enforces it.
RIM_MAX_FLANK_DEG = 18.0

# --- how the two relate -----------------------------------------------------------
# Equirectangular about the centre. 111111.0 m per degree is the constant the rest of the
# pipeline was built with (1e7 m from the equator to the pole, over 90 degrees).
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))

SEED = 20250902

# --- the till plain ---------------------------------------------------------------
# The uplands, after the country round Royal in Clay County, northwest Iowa. That is
# Des Moines Lobe ground: the last ice sheet pulled off it about fourteen thousand years
# ago and left a young, barely drained till plain behind. What that looks like is three
# things at once, and all three are here:
#
#   * swell and swale - a gentle, aimless undulation a few metres deep, the surface of
#     the till itself;
#   * low recessional moraines - broader rises the ice left where its edge stalled,
#     running in arcs, which is why this octave is stretched along a grain rather than
#     isotropic like the other two;
#
# The third thing the real place has - prairie potholes, the closed depressions the
# retreating ice left where blocks of it were buried in the till - is deliberately not
# here. They are the signature of the landscape and they read as craters at any useful
# vertical exaggeration, which is not what this map is for.
#
# Total relief is about 18 m across the 8 km of the map, which is the order of what the
# real place does. It has to stay well over the bank top or the river would come out of
# its valley and flood the low ground; `validate()` bounds that analytically.
LAND_MORAINE_M, LAND_MORAINE_LAM_M = 3.6, 3000.0
LAND_MORAINE_GRAIN_DEG = -35.0    # the lobe's ridges trend roughly northwest-southeast
LAND_MORAINE_STRETCH = 2.6        # ... and are that many times longer than they are wide
LAND_SWELL_M, LAND_SWELL_LAM_M = 1.9, 900.0
LAND_SWALE_M, LAND_SWALE_LAM_M = 0.8, 380.0
LAND_WARP_M, LAND_WARP_LAM_M = 130.0, 1700.0     # domain warp, so nothing reads as a sine

# --- the water and its valley -----------------------------------------------------
# A river runs the whole length of the map from north to south and widens into a lake in
# the northern quarter, with an island in the middle of it. Both sit at the bottom of a
# valley of their own, cut into the floodplain: the ground falls away from the water over
# VALLEY_HALF_W_M either side and is back at the datum beyond that.
#
# Everything below the floodplain is quoted **relative to the water surface at that
# station**, and the water surface falls from north to south. That is the only way the
# numbers stay honest along a river that runs downhill through flat ground: quote the
# bank as an absolute height and it drowns at one end of the map and stands 5 m proud at
# the other. The stack, top to bottom, at any point on the water:
#
#     BASE_ELEV_M                     the mean upland                       82.0 m
#       - VALLEY_DEPTH_M              the valley, over VALLEY_HALF_W_M      60.0 m
#       - WATER_BANK_M                the bank top, over BANK_RUN_M         58.0 m  <- ws
#       - RIVER_DEPTH_M               the river bed                         55.0 m
#       - LAKE_DEPTH_M                the lake bed, at its deepest          20.0 m
#
# The first line is a mean and not a height: the till plain swings about eleven metres
# either side of it, so the valley is anything from 11 m to 33 m deep along its length,
# which is what a river cutting across a moraine field actually does. Every line under it
# is measured from the **local** ground or the **local** water surface, never from the
# datum - quote any of them as a constant and the section lands in the wrong place
# everywhere the ground is not exactly average.
#
# So the river is cut five metres into the ground it runs through and the lake forty,
# which is what was asked for; read as water columns rather than as excavations they are
# three metres and thirty-eight.
#
# The meanders are closed form - two sines beating against each other - and not a random
# walk, so both halves of the pipeline get the identical polyline whatever else is added
# to the map first. The shortest radius of curvature they produce is about 250 m, which
# is what keeps `buffer_ring` at the drawn half-width from folding the channel polygon
# through itself.
RIVER_X0 = 3900.0             # the line the river meanders about
RIVER_A1, RIVER_L1 = 850.0, 5400.0            # the long meander
RIVER_A2, RIVER_L2, RIVER_P2 = 340.0, 2200.0, 1.0     # and the short one riding on it
RIVER_STEP_M = 40.0           # how finely the closed form is sampled into a polyline
# 45 m of half-width, not the 28 it started at. The synthesis grid is 4 m, and the
# cross-section of a 56 m river puts its submerged bank inside 17 m of that grid - six
# cells for a shape with two corners in it. What came out was a trough whose waterline
# sat 25 cm below where the vectors drew it and three metres further out, which is the
# exact disagreement `water_half_w` exists to prevent. Ninety metres across resolves.
RIVER_HALF_W_M = 45.0         # half the drawn water surface
RIVER_DEPTH_M = 3.0           # waterline down to the bed
RIVER_FALL_M = 2.5            # total fall of the water surface across the whole axis

WATER_BANK_M = 2.0            # bank top above the waterline
BANK_RUN_M = 60.0             # how far out from the waterline the bank climbs
VALLEY_HALF_W_M = 500.0       # waterline out to where the ground is floodplain again
# 22 m, not the 15 it was while the uplands were a flat sheet. The datum is the *mean*
# upland now, and the till plain swings about eleven metres either side of it: a valley
# only 15 m deep would have the river standing at the level of the low ground a kilometre
# away, and it would come out of its valley into it. `validate()` bounds that at four
# sigma of the relief, and this is what that bound asks for.
VALLEY_DEPTH_M = 22.0         # mean upland down to the bank top

# The lake. Its shore is a lobed ellipse rather than a drawn one, and the DEM shapes its
# basin with `terrain_ops.ellipse_r` from these same numbers and the same harmonics - if
# the two ever drift the water is painted somewhere the basin is not, which shows up in
# neither output alone. `measure_elevation.py` walks the drawn ring and checks the ground
# under it really is at the waterline.
LAKE_C = (4630.0, 1900.0)
LAKE_A, LAKE_B, LAKE_ROT = 880.0, 620.0, 15.0
LAKE_HARMONICS = ((0.060, 3, 0.7), (0.035, 5, 2.1))
LAKE_DEPTH_M = 38.0           # waterline down to the deepest bed
LAKE_SHELF_M = 260.0          # how far in from either shore the bed takes to fall

# The island, concentric with the lake. It is a wood in the vectors and a rise in the
# terrain, and it breaks the surface by ISLAND_H_M - low enough to read as a piece of
# floodplain the lake left standing rather than as a hill.
ISLAND_A, ISLAND_B = 240.0, 175.0
ISLAND_H_M = 4.0
ISLAND_RISE_M = 120.0

# Where the rim has to let the river through. The river crosses the northern and the
# southern sill on its way off the map, and the rim is added by addition on top of
# whatever is already there - so without a notch the sill simply rides up on the channel
# and the water runs forty metres uphill to leave the map. The notch is held to the
# corridor's own width and feathered wider than the sill is tall.
RIVER_NOTCH_HALF_M = 560.0
RIVER_NOTCH_FEATHER_M = 450.0


def river_axis():
    """The centreline, from EDGE_MIN to EDGE_MAX so it does not die at the canvas edge."""
    def at(y):
        return (RIVER_X0
                + RIVER_A1 * math.sin(2.0 * math.pi * y / RIVER_L1)
                + RIVER_A2 * math.sin(2.0 * math.pi * y / RIVER_L2 + RIVER_P2), y)

    n = int(math.ceil((EDGE_MAX - EDGE_MIN) / RIVER_STEP_M))
    # The last step is short rather than the axis stopping short: an alignment that ends
    # inside the canvas ends at a cliff, and the check in `validate()` is there because
    # eight metres of it is not something the hillshade would ever show.
    return [at(EDGE_MIN + min(i * RIVER_STEP_M, EDGE_MAX - EDGE_MIN))
            for i in range(n + 1)]


def lobed_ellipse_ring(cx, cy, a, b, rot_deg=0.0, harmonics=(), n=96):
    """The shore `terrain_ops.ellipse_r` draws with the same harmonics.

    `ellipse_r` returns q/m, where q is the normalised elliptical radius and m is the
    harmonic modulation, so its shore - the level set at 1 - is q = m. This walks that
    level set. Anything else, including `ellipse_ring`, gives a shore the basin does not
    sit under.
    """
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        m = 1.0
        for amp, k, ph in harmonics:
            m += amp * math.sin(k * th + ph)
        u, v = a * m * math.cos(th), b * m * math.sin(th)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return out


def lake_ring():
    return lobed_ellipse_ring(*LAKE_C, LAKE_A, LAKE_B, LAKE_ROT, LAKE_HARMONICS)


def island_ring():
    return lobed_ellipse_ring(*LAKE_C, ISLAND_A, ISLAND_B, LAKE_ROT, (), n=64)


def river_profile():
    """The water surface along the river, as `(s_in, s_out, grade, length)`.

    The lake is a flat sheet, so the profile is flat across it and falls at a constant
    grade above and below - which is the whole of the hydrology this map needs, and it is
    exact rather than iterated. `s_in` and `s_out` are the arc lengths at which the axis
    enters and leaves the drawn lake shore, found against that same ring, so the sheet
    ends exactly where the water does.
    """
    axis = river_axis()
    ring = lake_ring()
    arc = [0.0]
    for i in range(len(axis) - 1):
        arc.append(arc[-1] + math.dist(axis[i], axis[i + 1]))
    inside = [i for i, p in enumerate(axis) if point_in_ring(p, ring)]
    if not inside:
        return 0.0, 0.0, RIVER_FALL_M / arc[-1], arc[-1]
    s_in, s_out, length = arc[inside[0]], arc[inside[-1]], arc[-1]
    return s_in, s_out, RIVER_FALL_M / (s_in + length - s_out), length


LAKE_WS_M = BASE_ELEV_M - VALLEY_DEPTH_M - WATER_BANK_M    # the lake surface, 58.0 m


# --- the survey and the roads -----------------------------------------------------
# The Public Land Survey System, which is why the roads on a map of the American midwest
# look the way they do: the ground was subdivided into one-mile sections before anyone
# built on it, and the roads went on the section lines. So the grid is not a design
# choice here - it is a mile, exactly, and everything else has to fit around it.
#
# Two through roads run the length of the map a mile in from the east and the west edge,
# which is what was asked for and also happens to put both of them clear of the water:
# the river swings between x = 2791 and x = 5089 and its valley reaches 545 m either
# side of that, so neither trunk road ever meets it. The service roads are the section
# lines between them, and each one crosses the river exactly once - a river that is a
# single-valued function of northing cannot be crossed twice by an east-west line.
#
# The one thing the mile grid has to be told about is the lake. It is 1290 m across
# north to south and the sections are 1609 m apart, so there are only 319 m of anchor to
# choose from that keep a section line out of the water. PLSS_EW_ANCHOR_M centres that
# window, which leaves the two nearest section roads running 160 m off the north and
# south shores - a road along a lake shore, which is a thing that exists, rather than a
# mile and a half of causeway.
MILE_M = 1609.344

ROAD_MAIN_INSET_M = MILE_M                      # the trunk roads, a mile in from the edge
ROAD_W_X = ROAD_MAIN_INSET_M                    # 1609.344
ROAD_E_X = PLAYABLE_M - ROAD_MAIN_INSET_M       # 6582.656
PLSS_EW_ANCHOR_M = 1080.5
PLSS_EW_Y = [PLSS_EW_ANCHOR_M + k * MILE_M for k in range(5)]

ROAD_PRIMARY = dict(half_width_m=5.5, feather_m=14.0, grade_max=0.050)
ROAD_SECTION = dict(half_width_m=4.0, feather_m=11.0, grade_max=0.070)

# How far off the centreline of a watercourse a road has to be carried on a deck. The
# river is 90 m wide and a section line crosses it square or nearly so, so this sets a
# span of about 130 m - the ground under it is left exactly as the water cut it.
BRIDGE_CLEAR_M = RIVER_HALF_W_M + 20.0

# Which section lines get a bridge, by index into PLSS_EW_Y. Only the outer two: the
# three in the middle run down to the river and stop, which is what a section-line road
# without a bridge does - the survey put a road allowance on every mile line whether or
# not anyone ever built a crossing on it, and in river country most of them dead-end at
# the bank and the traffic goes round by the trunk roads. It also means the two roads
# that do cross carry the through traffic, which is what makes a trunk road a trunk road.
PLSS_BRIDGED = (0, len(PLSS_EW_Y) - 1)
# Where a road that is not going to cross stops: on the top of the bank, clear of the
# water but close enough to read as a road that ran out rather than one that was aimed
# somewhere else.
ROAD_STUB_SETBACK_M = RIVER_HALF_W_M + BANK_RUN_M


def water_crossings(axis, clearance=None):
    """Arc-length spans where an alignment runs within `clearance` of open water.

    What the DEM leaves alone and the OSM tags `bridge=yes`, computed once here so the
    two cannot disagree about where the deck starts. Grading the ground under a bridge
    is how a channel gets filled in by a road that was supposed to cross it.
    """
    clearance = BRIDGE_CLEAR_M if clearance is None else clearance
    dense = densify(axis, 10.0)
    riv = river_axis()
    ring = lake_ring()
    # Two cheap restrictions, because this is called for every alignment on the map and
    # walks a densified one against 322 river vertices and 97 lake ones: it was 29 of the
    # 36 seconds it took to import this module.
    #
    # The river is a single-valued function of northing, so the axis comes out sorted in
    # y and a point can only be within `clearance` of the stretch inside its own band of
    # northing - a slice, with a vertex of margin at each end. That is exact here and
    # would not be on a watercourse that doubled back.
    rys = [q[1] for q in riv]
    lx = min(q[0] for q in ring) - clearance, max(q[0] for q in ring) + clearance
    ly = min(q[1] for q in ring) - clearance, max(q[1] for q in ring) + clearance
    arc, acc = [0.0], 0.0
    for i in range(1, len(dense)):
        acc += math.dist(dense[i - 1], dense[i])
        arc.append(acc)
    out, run = [], None
    for i, p in enumerate(dense):
        a = max(0, bisect.bisect_left(rys, p[1] - clearance) - 1)
        b = min(len(riv), bisect.bisect_right(rys, p[1] + clearance) + 1)
        near = b - a >= 2 and dist_to_polyline(p, riv[a:b]) < clearance
        if not near and lx[0] <= p[0] <= lx[1] and ly[0] <= p[1] <= ly[1]:
            near = (point_in_ring(p, ring)
                    or min(math.dist(p, q) for q in ring) < clearance)
        if near and run is None:
            run = arc[i]
        elif not near and run is not None:
            out.append((run, arc[i]))
            run = None
    if run is not None:
        out.append((run, arc[-1]))
    return out


def _ns_road(rid, name, x, spec):
    axis = [(x, EDGE_MIN), (x, EDGE_MAX)]
    return dict(id=rid, name=name, kind='primary', axis=axis,
                bridge_spans=water_crossings(axis), **spec)


def _ew_road(rid, name, y, spec, bridged):
    """A section-line road. Bridged ones are one alignment; the rest are two stubs that
    stop on the bank, and the gap between them is the crossing nobody built."""
    axis = [(EDGE_MIN, y), (EDGE_MAX, y)]
    if bridged:
        return [dict(id=rid, name=name, kind='section', axis=axis,
                     bridge_spans=water_crossings(axis), **spec)]
    stop = water_crossings(axis, ROAD_STUB_SETBACK_M)
    if not stop:
        return [dict(id=rid, name=name, kind='section', axis=axis,
                     bridge_spans=[], **spec)]
    x0 = EDGE_MIN + stop[0][0]
    x1 = EDGE_MIN + stop[-1][1]
    return [dict(id=f'{rid}_west', name=f'{name} Oeste', kind='section',
                 axis=[(EDGE_MIN, y), (x0, y)], bridge_spans=[], **spec),
            dict(id=f'{rid}_east', name=f'{name} Este', kind='section',
                 axis=[(x1, y), (EDGE_MAX, y)], bridge_spans=[], **spec)]


# --- the towns --------------------------------------------------------------------
# Four towns, one on each corner where a trunk road meets a section line that carries a
# bridge. That is not a decoration on the road grid, it is the only placement the road
# grid allows: the two bridged section lines are the only east-west roads that get
# across the river, so their junctions with the two trunk roads are the four points on
# the map where two through routes cross, and a town in the midwest stands where two
# through routes cross. The three unbridged section lines dead-end on the bank and carry
# nobody, which is exactly why nothing is built on them.
#
# Each town is a grid of TOWN_COLS x TOWN_ROWS blocks of TOWN_BLOCK_W_M by
# TOWN_BLOCK_H_M, and the trunk road runs up the middle of it - the middle *street line*
# of the grid is the trunk road rather than a street of the town's own, so main street
# is the highway, which is what a section-line town looks like from the air. The section
# line does the same thing across it, so the junction the town is named for is the
# crossroads in its centre and there are equal numbers of blocks on all four sides.
#
# The block sizes are the brief and everything else falls out of them. What a road takes
# out of the grid is its own nominal feather either side of the centreline - the same
# clearance everything else is held off a road by, and the reason a primary takes 28 m
# of the grid where a town street takes 16 - so the blocks come out at exactly the size
# they are quoted at whatever class of road happens to bound them. `validate()` measures
# the rings rather than trusting the arithmetic.
TOWN_COLS, TOWN_ROWS = 4, 4
TOWN_BLOCK_W_M = 100.0        # east-west, across the trunk road
TOWN_BLOCK_H_M = 100.0        # north-south, along it
TOWN_STREET = dict(half_width_m=3.0, feather_m=8.0, grade_max=0.080)

# The platform the whole town is levelled onto. It reaches TOWN_PAD_MARGIN_M past the
# outermost street so the graded ground runs out beyond the last kerb rather than at it,
# and it is not dead flat: TOWN_DRAIN_GRADE of fall to the south is a third of a percent,
# far under any road's ruling grade and enough that the ground drains instead of
# terracing. The feather is nominal only - the DEM widens it to 1.5*|dz|/tan(4 deg)
# wherever the cut is deep, the same identity that sets every bank on the map.
TOWN_PAD_MARGIN_M = 20.0
TOWN_PAD_FEATHER_M = 30.0
TOWN_DRAIN_GRADE = 0.003

TOWN_SITES = (
    ('town_nw', 'Ciudad del Noroeste', ROAD_W_X, PLSS_EW_Y[PLSS_BRIDGED[0]]),
    ('town_ne', 'Ciudad del Noreste', ROAD_E_X, PLSS_EW_Y[PLSS_BRIDGED[0]]),
    ('town_sw', 'Ciudad del Suroeste', ROAD_W_X, PLSS_EW_Y[PLSS_BRIDGED[-1]]),
    ('town_se', 'Ciudad del Sureste', ROAD_E_X, PLSS_EW_Y[PLSS_BRIDGED[-1]]),
)


def _street_lines(n, block, gap_mid, gap_side):
    """Offsets of the `n + 1` street centrelines that bound `n` blocks, measured from
    the through road in the middle of the grid.

    `n` has to be even: the through road is the middle line, so there are `n / 2` blocks
    either side of it. Each step is one block plus what the two lines bounding it take
    out of the grid, which is why the first step out from the middle is wider than the
    rest - a primary road's half-allowance against a street's.
    """
    out = [0.0]
    for k in range(n // 2):
        out.append(out[-1] + block + (gap_mid if k == 0 else gap_side) + gap_side)
    return [-v for v in reversed(out[1:])] + out


def _line_gaps(n, gap_mid, gap_side):
    """What each of those lines takes out of the grid on either side of itself."""
    return [gap_mid if i == n // 2 else gap_side for i in range(n + 1)]


TOWN_COL_LINES = _street_lines(TOWN_COLS, TOWN_BLOCK_W_M,
                               ROAD_PRIMARY['feather_m'], TOWN_STREET['feather_m'])
TOWN_COL_GAPS = _line_gaps(TOWN_COLS, ROAD_PRIMARY['feather_m'],
                           TOWN_STREET['feather_m'])
TOWN_ROW_LINES = _street_lines(TOWN_ROWS, TOWN_BLOCK_H_M,
                               ROAD_SECTION['feather_m'], TOWN_STREET['feather_m'])
TOWN_ROW_GAPS = _line_gaps(TOWN_ROWS, ROAD_SECTION['feather_m'],
                           TOWN_STREET['feather_m'])

TOWN_HALF_W_M = TOWN_COL_LINES[-1] + TOWN_PAD_MARGIN_M
TOWN_HALF_H_M = TOWN_ROW_LINES[-1] + TOWN_PAD_MARGIN_M


def town_streets(tid, name, cx, cy):
    """The town's own streets: every line of the block grid except the two the trunk
    road and the section line already stand on.

    Each one runs from the outermost cross line to the outermost cross line, so both its
    ends land on another alignment and the grid is a connected network rather than eight
    sticks laid beside each other. `validate()` holds that: a street is the one class of
    corridor allowed to stop inside the canvas, and only on a road it meets.
    """
    out = []
    y0, y1 = cy + TOWN_ROW_LINES[0], cy + TOWN_ROW_LINES[-1]
    x0, x1 = cx + TOWN_COL_LINES[0], cx + TOWN_COL_LINES[-1]
    for i, dx in enumerate(TOWN_COL_LINES):
        if i == TOWN_COLS // 2:
            continue                              # the trunk road is this line
        out.append(dict(id=f'{tid}_ns{i}', name=f'{name} Calle {i + 1}', kind='street',
                        axis=[(cx + dx, y0), (cx + dx, y1)], bridge_spans=[],
                        **TOWN_STREET))
    for j, dy in enumerate(TOWN_ROW_LINES):
        if j == TOWN_ROWS // 2:
            continue                              # and the section line is this one
        out.append(dict(id=f'{tid}_ew{j}', name=f'{name} Avenida {j + 1}',
                        kind='street', axis=[(x0, cy + dy), (x1, cy + dy)],
                        bridge_spans=[], **TOWN_STREET))
    return out


def town_blocks(tid, name, cx, cy):
    """The `TOWN_COLS * TOWN_ROWS` blocks, as tagged rings. Each one is the ground
    between two street lines, inset by what each of them takes out of the grid."""
    out = []
    for r in range(TOWN_ROWS):
        y0 = cy + TOWN_ROW_LINES[r] + TOWN_ROW_GAPS[r]
        y1 = cy + TOWN_ROW_LINES[r + 1] - TOWN_ROW_GAPS[r + 1]
        for c in range(TOWN_COLS):
            x0 = cx + TOWN_COL_LINES[c] + TOWN_COL_GAPS[c]
            x1 = cx + TOWN_COL_LINES[c + 1] - TOWN_COL_GAPS[c + 1]
            out.append({'id': f'{tid}_b{r}{c}',
                        'name': f'{name} Cuadra {r + 1}-{c + 1}',
                        'ring': rect_ring(x0, y0, x1, y1),
                        'tags': {'landuse': 'farmyard'}})
    return out


def town_pad(tid, name, cx, cy):
    """The platform the whole town stands on. It carries no tags of its own: what is
    drawn here is the blocks, the same way the island is an `AREAS` ring rather than
    something hanging off the lake record."""
    w, h = 2.0 * TOWN_HALF_W_M, 2.0 * TOWN_HALF_H_M
    return {'id': f'{tid}_pad', 'name': name, 'kind': 'town', 'centre': (cx, cy),
            'size': (w, h),
            'ring': rect_ring(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0),
            'feather_m': TOWN_PAD_FEATHER_M, 'drain_grade': TOWN_DRAIN_GRADE}


# --- the industrial zones ---------------------------------------------------------
# Ten two-hectare aprons hung off the trunk roads and the section lines. An industrial
# yard is a thing that wants a road at its gate and flat ground under it, and both of
# those are relationships rather than coordinates - so a site is recorded as *which road,
# how far along it, and which side*, and the rectangle is worked out from the road's own
# record. Write the corners out as four numbers instead and the day a road moves the
# yards stay where they were, ten aprons in the middle of a field with no way in.
#
# The setback is measured from the edge of the running surface and not from the
# centreline, because that is what "ten metres off the road" means to anyone standing on
# it. Against a primary that puts the fence 15.5 m from the centreline and against a
# section line 14 m, both of which clear the per-class road clearance the plantings use
# (14 m and 11 m) - so a yard placed this way is inside the letter of the brief and
# outside the verge of the road, which is the only way both can be true at once.
#
# The yard is square and it is a stated number of hectares, so the side is neither of
# those numbers - it is what falls out of them. The area is the requirement and the shape
# is the requirement; a side rounded to a whole metre would satisfy the shape and quietly
# miss the area, which is the kind of thing nothing downstream would ever mention. This
# map already runs on 1609.344 m section lines, so an awkward number is not a problem
# here. Two sizes: ten small yards and two large ones.
INDUSTRY_SMALL_HA = 2.0       # -> 141.42 m a side
INDUSTRY_LARGE_HA = 5.0       # -> 223.61 m a side
FARM_AREA_HA = 20.0           # -> 447.21 m a side

# `ROADSIDE_SETBACK_M` is shared by everything that stands beside a road - the aprons, the
# granjas and the woods - because a fence ten metres off the road is ten metres off the
# road whatever is behind it. The other two are the yards': a drain grade is a grade and
# does not want to be re-argued per size, it just falls further across a bigger yard.
ROADSIDE_SETBACK_M = 10.0     # from the *edge of the running surface*, not the centreline
YARD_FEATHER_M = 25.0         # nominal; the DEM widens it with the cut like any other
YARD_DRAIN_GRADE = 0.004      # 57 cm across a 2 ha apron, 1.8 m across a 20 ha farm


def yard_side(area_ha):
    """The side of a square yard of `area_ha`."""
    return math.sqrt(area_ha * 10000.0)

# Where they are: (id, name, road, station along it, side, hectares). `side` is +1
# towards increasing x or y - east of a trunk road, south of a section line - and -1 the
# other way. The stations are spread over the whole map, four small yards on the two
# trunk roads and six on the section lines so that every one of the five has one, and the
# two large yards on the trunk roads, which is where the traffic a five-hectare operation
# generates has somewhere to go. None of them is anywhere near the water: `validate()`
# holds every corner a full valley half-width off the river and the lake, because an
# apron on a valley side is a cut nobody would make.
INDUSTRY_SITES = (
    ('ind_w1', 'Zona Industrial del Oeste 1', 'road_west', 2000.0, +1,
     INDUSTRY_SMALL_HA),
    ('ind_w2', 'Zona Industrial del Oeste 2', 'road_west', 5200.0, -1,
     INDUSTRY_SMALL_HA),
    ('ind_e1', 'Zona Industrial del Este 1', 'road_east', 3400.0, +1,
     INDUSTRY_SMALL_HA),
    ('ind_e2', 'Zona Industrial del Este 2', 'road_east', 6600.0, -1,
     INDUSTRY_SMALL_HA),
    ('ind_s1', 'Zona Industrial de Servicio 1', 'section_1', 2800.0, -1,
     INDUSTRY_SMALL_HA),
    ('ind_s2', 'Zona Industrial de Servicio 2', 'section_1', 5800.0, +1,
     INDUSTRY_SMALL_HA),
    ('ind_s3', 'Zona Industrial de Servicio 3', 'section_2_west', 900.0, +1,
     INDUSTRY_SMALL_HA),
    ('ind_s4', 'Zona Industrial de Servicio 4', 'section_3_east', 7400.0, -1,
     INDUSTRY_SMALL_HA),
    ('ind_s5', 'Zona Industrial de Servicio 5', 'section_4_west', 2600.0, +1,
     INDUSTRY_SMALL_HA),
    ('ind_s6', 'Zona Industrial de Servicio 6', 'section_5', 5700.0, -1,
     INDUSTRY_SMALL_HA),
    ('ind_b1', 'Zona Industrial Mayor del Oeste', 'road_west', 3400.0, +1,
     INDUSTRY_LARGE_HA),
    ('ind_b2', 'Zona Industrial Mayor del Este', 'road_east', 4900.0, -1,
     INDUSTRY_LARGE_HA),
)

# The farms. Same shape of thing as an apron - a square yard hung off a road, ten metres
# off the kerb - and built by the same code from the same table shape. Two differences,
# and both of them are data rather than another implementation: a granja is twenty
# hectares, and it stands **only on a section line**. That last one is not decoration.
# A section-line road is the road a farm in this country actually fronts onto: the trunk
# roads carry the through traffic, and 447 m of yard gate opening onto one of them is a
# thing nobody builds. `validate()` holds it off the record itself, so a farm moved onto
# a trunk road is a complaint and not a surprise in the editor.
#
# 447 m a side is a large square, and where it can go is decided almost entirely by what
# is already on the map: the river's valley reaches 500 m either side of a channel that
# swings between x = 2791 and x = 5089, so the whole middle third of every section line
# is out; a trunk road runs up each side of that; and the four towns and the twelve
# aprons take their own ground. What is left is a window either side of each trunk road,
# and these six sit in it.
FARM_SITES = (
    ('farm_1', 'Granja del Noroeste', 'section_1', 800.0, -1),
    ('farm_2', 'Granja del Oeste', 'section_3_west', 900.0, +1),
    ('farm_3', 'Granja del Suroeste', 'section_5', 800.0, -1),
    ('farm_4', 'Granja del Noreste', 'section_2_east', 7200.0, +1),
    ('farm_5', 'Granja del Este', 'section_4_east', 7300.0, -1),
    ('farm_6', 'Granja del Sureste', 'section_5', 7300.0, -1),
)


def corridor_by_id(cid):
    for c in CORRIDORS:
        if c['id'] == cid:
            return c
    raise KeyError(f"no corridor {cid!r}")


def roadside_geometry(road_id, station, side, area_ha, setback_m):
    """`(centre, size)` for a square yard on `road_id`, from the road's own record.

    The offset is half the running surface plus the setback plus half the yard, so the
    near fence lands exactly `setback_m` off the edge of the road whatever class of road
    it is and whatever size the yard is. The yard being square, `side` only decides which
    way it is put and the size is the same either way.
    """
    c = corridor_by_id(road_id)
    ax = c['axis']
    a = yard_side(area_ha)
    off = c['half_width_m'] + setback_m + a / 2.0
    if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]):
        return (ax[0][0] + side * off, station), (a, a)
    return (station, ax[0][1] + side * off), (a, a)


def roadside_pad(pid, name, road_id, station, side, area_ha, setback_m, drain_grade,
                 feather_m, tags, road_kinds, kind):
    """A levelled yard that draws its own footprint - which is the difference between
    this and a town pad. A yard *is* the thing standing on the platform, so it carries
    `tags` and `emit_pads` draws it; a town's platform carries none, because what is
    drawn there is its blocks.

    `road_kinds` travels with the record so `validate()` can hold "a farm only stands on
    a section line" without knowing what a farm is: the placement rules are one loop over
    every roadside yard, and the differences between an apron and a granja are data.
    """
    (cx, cy), (w, h) = roadside_geometry(road_id, station, side, area_ha, setback_m)
    return {'id': pid, 'name': name, 'kind': kind, 'road': road_id,
            'station': station, 'side': side, 'area_ha': area_ha,
            'setback_m': setback_m, 'road_kinds': road_kinds,
            'centre': (cx, cy), 'size': (w, h),
            'ring': rect_ring(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0),
            'feather_m': feather_m, 'drain_grade': drain_grade, 'tags': dict(tags)}


def industry_pad(pid, name, road_id, station, side, area_ha):
    return roadside_pad(pid, name, road_id, station, side, area_ha,
                        ROADSIDE_SETBACK_M, YARD_DRAIN_GRADE, YARD_FEATHER_M,
                        {'landuse': 'farmyard', 'building': 'industrial'},
                        ('primary', 'section'), 'industry')


def farm_pad(pid, name, road_id, station, side):
    return roadside_pad(pid, name, road_id, station, side, FARM_AREA_HA,
                        ROADSIDE_SETBACK_M, YARD_DRAIN_GRADE, YARD_FEATHER_M,
                        {'landuse': 'farmyard'}, ('section',), 'farm')


# --- the roadside woods -----------------------------------------------------------
# Five twenty-hectare woods along the trunk roads and the section lines: rectangles laid
# long-side-on along the road, `WOOD_ASPECT` times as long as they are deep, which is
# what a planted woodlot beside a road looks like. The two sides fall out of the area and
# the aspect and nothing else - `sqrt(area/aspect)` deep by `aspect` times that long, so
# 632 by 316 m - and there is no rounding anywhere in it.
#
# A wood is the first thing on this map that is *only* vectors. Nobody levels ground to
# grow trees on, so there is no pad, no feather and no drain: these live in `AREAS`
# rather than in `PADS`, and the heightmap does not change when one is added. That is the
# whole difference between a granja and a bosque here, and it is the reason a wood may
# sit on ground a yard could not.
WOOD_AREA_HA = 20.0
WOOD_ASPECT = 2.0             # long side along the road, so it reads as a belt
# What is growing in them. `leaf_type` is not a tag that makes a way drawable - the
# closed vocabulary in RENDERED_TAGS is, and `natural=wood` is what gets these rings on
# the map - but it is not dead weight either: both renderers colour a needleleaf wood
# apart from a broadleaf one, the same way `visualize_osm` tells an industrial apron from
# a farmyard. A tag no renderer reads is the one thing not worth emitting. The woods and
# the island are the conifer; the shelterbelts are hardwood (`SHELTER_LEAF_TYPE`), and
# that difference is the whole reason this tag earns its place on the ring.
WOOD_LEAF_TYPE = 'needleleaved'
# Same table shape as the yards: which road, how far along, which side. The setback is
# the shared roadside one, measured from the edge of the running surface - so the trees
# come down to the verge and stop where the road's graded platform begins.
WOOD_SITES = (
    ('wood_1', 'Bosque del Oeste', 'road_west', 3400.0, -1),
    ('wood_2', 'Bosque del Este', 'road_east', 5100.0, +1),
    ('wood_3', 'Bosque del Norte', 'section_1', 2600.0, +1),
    ('wood_4', 'Bosque del Este Bajo', 'section_4_east', 6000.0, +1),
    ('wood_5', 'Bosque del Suroeste', 'section_5', 2700.0, -1),
)


def wood_ring_at_origin(along_x, area_ha):
    """The wood's outline about (0, 0), long side east-west if `along_x`."""
    d = math.sqrt(area_ha * 10000.0 / WOOD_ASPECT)      # the short side
    w, h = (WOOD_ASPECT * d, d) if along_x else (d, WOOD_ASPECT * d)
    return rect_ring(-w / 2.0, -h / 2.0, w / 2.0, h / 2.0)


def wood_geometry(road_id, station, side, area_ha):
    """`(centre, ring)` for a wood on `road_id`.

    The centre is put wherever it has to be for the ring's *own* nearest point to land
    `ROADSIDE_SETBACK_M` off the edge of the road, read off the ring rather than assumed
    from a half-side. It comes to the same thing for a rectangle and it did not for the
    lobed outline these were drawn with first, which is reason enough to leave it
    measuring the ring: the shape is a constant away from changing again.
    """
    c = corridor_by_id(road_id)
    ax = c['axis']
    vertical = abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1])
    ring0 = wood_ring_at_origin(not vertical, area_ha)
    d = [p[0] for p in ring0] if vertical else [p[1] for p in ring0]
    reach = -min(d) if side > 0 else max(d)
    off = c['half_width_m'] + ROADSIDE_SETBACK_M + reach
    cx, cy = ((ax[0][0] + side * off, station) if vertical
              else (station, ax[0][1] + side * off))
    return (cx, cy), [(cx + x, cy + y) for x, y in ring0]


def wood_area(wid, name, road_id, station, side):
    (cx, cy), ring = wood_geometry(road_id, station, side, WOOD_AREA_HA)
    return {'id': wid, 'name': name, 'road': road_id, 'station': station, 'side': side,
            'area_ha': WOOD_AREA_HA, 'centre': (cx, cy), 'ring': ring,
            'tags': {'natural': 'wood', 'leaf_type': WOOD_LEAF_TYPE}}


# --- the shelterbelts -------------------------------------------------------------
# Rompevientos: long narrow strips of hardwood along the trunk roads, a hundred metres
# across and a full section of frontage long.
#
# The length is not a choice. A belt runs from one cross road to the next, held off each
# end by that road's own clearance, so it is the mile less twice what a section line
# keeps clear - 1581.3 m - and it lands exactly on the survey the whole map is built on.
# Picking a round number instead would put the ends of the belts wherever that number
# happened to fall, and the one thing a shelterbelt is *for* is running the length of the
# ground it shelters.
#
# That also decides which roads can carry one. The section lines are a mile apart, so a
# mile-long belt fits between two of them exactly - along a trunk road. Across one, on a
# section line, the same belt would have to fit between the two trunk roads and clear the
# river's valley, and the widest window that leaves anywhere on the map is 1494 m. So
# these are trunk-road belts, and `validate()` says so rather than leaving it to whoever
# adds the next one to rediscover.
SHELTER_W_M = 100.0
# What is planted in them, and it is deliberately not what is in the woods. A windbreak
# on this survey is a row of hardwood - the trees a farmer puts on a field boundary - and
# the woods are conifer, so the two read apart in both renderers instead of being one
# undifferentiated green. It is the same tag doing the same job it does on a wood; the
# value is the only thing that differs.
SHELTER_LEAF_TYPE = 'broadleaved'
SHELTER_LEN_M = MILE_M - 2.0 * (ROAD_SECTION['half_width_m'] + ROADSIDE_SETBACK_M)

# The north-south lines a belt may run on. Two kinds, and both of them are lines the
# survey already put there rather than places a belt looked good:
#
#   * the **half-section lines**, half a mile off each trunk road. In this survey that is
#     where a field boundary falls, and a field boundary is where a windbreak goes - it
#     shelters the ground on both sides of it instead of one.
#   * the **frontage** of each trunk road, one setback off the running surface, which is
#     the other place they stand.
#
# The half-section line half a mile *east* of the west trunk road is not here, and cannot
# be: it lands at x = 2414 and the river's valley reaches x = 2291, so a belt on it would
# be planted on a valley side. `validate()` would say so; it is listed here because the
# gap in an otherwise regular series is the kind of thing that gets 'fixed' by someone who
# has not checked.
SHELTER_FRONT_M = (ROAD_PRIMARY['half_width_m'] + ROADSIDE_SETBACK_M + SHELTER_W_M / 2.0)
SHELTER_LINES = {
    'oeste_media': ROAD_W_X - MILE_M / 2.0,          # 804.7, half a mile west of the trunk
    'este_media': ROAD_E_X - MILE_M / 2.0,           # 5778.0
    'este_lejana': ROAD_E_X + MILE_M / 2.0,          # 7387.3
    'oeste_camino': ROAD_W_X + SHELTER_FRONT_M,      # on the west trunk's east frontage
    'este_camino': ROAD_E_X - SHELTER_FRONT_M,       # and the east trunk's west frontage
}

# --- and the same thing turned through ninety degrees -------------------------------
# A transversal belt cannot take its length from the survey the way a north-south one
# does: the section lines are a mile apart in y, but in x the only two lines the grid
# gives are the trunk roads, and they are 4973 m apart. What it takes it from instead is
# the *band* a trunk road and the map edge leave between them - from the clean strip to
# the road's own clearance - which is a mile less both of those, 1493.8 m, and comes out
# identical east and west because the trunks are a mile in from either edge. So the
# length is still derived and still symmetric; it is just derived from the edge of the
# map rather than from the next road along.
#
# The middle band - between the two trunk roads - has no such length. The river's valley
# runs down the middle of it and the meanders move, so a belt spanning it would have one
# end on a road and the other wherever the water happened to be that station. There are
# none there, and that is why.
SHELTER_LEN_EW_M = (MILE_M - EDGE_CLEAR_M
                    - (ROAD_PRIMARY['half_width_m'] + ROADSIDE_SETBACK_M))
SHELTER_BANDS = {
    'oeste': (EDGE_CLEAR_M, ROAD_W_X - ROAD_PRIMARY['half_width_m']
              - ROADSIDE_SETBACK_M),
    'este': (ROAD_E_X + ROAD_PRIMARY['half_width_m'] + ROADSIDE_SETBACK_M,
             PLAYABLE_M - EDGE_CLEAR_M),
}
# The rows, which mirror `SHELTER_LINES` exactly: the half-section lines in y - the same
# half-mile offset the north-south belts run on, turned round - and the frontage of each
# section road, one setback off its running surface.
SHELTER_FRONT_EW_M = (ROAD_SECTION['half_width_m'] + ROADSIDE_SETBACK_M
                      + SHELTER_W_M / 2.0)
SHELTER_ROWS = dict(
    [(f'media_{k}', PLSS_EW_Y[0] - MILE_M / 2.0 + k * MILE_M)
     for k in range(len(PLSS_EW_Y))]
    + [(f'{side}_{k}', y + sgn * SHELTER_FRONT_EW_M)
       for k, y in enumerate(PLSS_EW_Y) for side, sgn in (('norte', -1), ('sur', +1))]
)

# A row carries a belt only if it can carry one on *both* sides of the map, so what goes
# on the ground is whole rows rather than whichever individual slots happened to be left
# over. Of the thirty (band, row) combinations, three rows come back clear on both
# flanks. The rest are taken by the four towns, the eighteen yards, the five woods and
# the five north-south belts - and two of them, `media_1` and `norte_1`, are taken by the
# north-south belts *crossing* them, which is the case the overlap test used to miss.
SHELTER_EW_SITES = (
    ('belt_h1', 'Rompevientos Transversal Oeste 1', 'oeste', 'media_0'),
    ('belt_h2', 'Rompevientos Transversal Este 1', 'este', 'media_0'),
    ('belt_h3', 'Rompevientos Transversal Oeste 2', 'oeste', 'sur_3'),
    ('belt_h4', 'Rompevientos Transversal Este 2', 'este', 'sur_3'),
    ('belt_h5', 'Rompevientos Transversal Oeste 3', 'oeste', 'media_4'),
    ('belt_h6', 'Rompevientos Transversal Este 3', 'este', 'media_4'),
)


def shelter_ring_ew(band, row):
    """A transversal belt: the whole band, `SHELTER_W_M` about its row."""
    x0, x1 = SHELTER_BANDS[band]
    y = SHELTER_ROWS[row]
    return rect_ring(x0, y - SHELTER_W_M / 2.0, x1, y + SHELTER_W_M / 2.0)


def shelter_area_ew(bid, name, band, row):
    x0, x1 = SHELTER_BANDS[band]
    return {'id': bid, 'name': name, 'band': band, 'row': row,
            'area_ha': SHELTER_W_M * SHELTER_LEN_EW_M / 10000.0,
            'centre': ((x0 + x1) / 2.0, SHELTER_ROWS[row]),
            'ring': shelter_ring_ew(band, row),
            'tags': {'natural': 'wood', 'leaf_type': SHELTER_LEAF_TYPE}}


# (id, name, line, which section it covers). The section index `k` is the stretch between
# `PLSS_EW_Y[k]` and `PLSS_EW_Y[k + 1]`, so a belt is named by the ground it covers rather
# than by a station someone measured off a ruler.
#
# There are five and not six because five is what fits. Every one of the twenty
# (line, section) slots was put through the placement rules: four towns, eighteen yards
# and five woods already stand on this grid, and what they leave is these five. The
# nearest miss is `este_media` section 1, where the river's east swing brings the belt to
# 493 m of open water against the 500 m a planting is held off - seven metres, on a rule
# that could be moved. It is not moved. The clearance is what decides whether a belt
# stands on the floodplain or on the valley side, and a rule that gives way the first
# time a feature wants it to is not deciding anything.
SHELTER_SITES = (
    ('belt_1', 'Rompevientos Oeste 1', 'oeste_media', 0),
    ('belt_2', 'Rompevientos Oeste 2', 'oeste_camino', 2),
    ('belt_3', 'Rompevientos Este 1', 'este_media', 2),
    ('belt_4', 'Rompevientos Este 2', 'este_camino', 1),
    ('belt_5', 'Rompevientos Este 3', 'este_lejana', 0),
)


def shelter_ring(line, gap):
    """The belt's outline: `SHELTER_W_M` about its line, ending on the two cross roads.

    Both ends are a clearance rather than a number - one section line's at each - so the
    belt is the section, and moving the setback moves both together.
    """
    x = SHELTER_LINES[line]
    end = ROAD_SECTION['half_width_m'] + ROADSIDE_SETBACK_M
    return rect_ring(x - SHELTER_W_M / 2.0, PLSS_EW_Y[gap] + end,
                     x + SHELTER_W_M / 2.0, PLSS_EW_Y[gap + 1] - end)


def shelter_area(bid, name, line, gap):
    ring = shelter_ring(line, gap)
    return {'id': bid, 'name': name, 'line': line, 'gap': gap,
            'area_ha': SHELTER_W_M * SHELTER_LEN_M / 10000.0,
            'centre': (SHELTER_LINES[line],
                       (PLSS_EW_Y[gap] + PLSS_EW_Y[gap + 1]) / 2.0), 'ring': ring,
            'tags': {'natural': 'wood', 'leaf_type': SHELTER_LEAF_TYPE}}


# --- the OSM vocabulary -----------------------------------------------------------
# Exactly what `osm_generator/visualize_osm.py` and `visualizer/create_3d_viewer.py` know
# how to draw. A way tagged with anything else is dropped by both renderers without a
# word, so it is worse than useless: it costs nodes and shows nothing. Both the generator
# and the checker read this list, so the two cannot drift apart.
RENDERED_TAGS = (('natural', 'water'), ('water', None), ('natural', 'wood'),
                 ('landuse', 'forest'), ('landuse', 'farmyard'),
                 ('landuse', 'farmland'), ('railway', None), ('highway', None))

# The road classes the renderers colour, keyed by the `kind` a corridor record carries.
HIGHWAY_CLASS = {'primary': 'primary', 'section': 'secondary',
                 'track': 'tertiary', 'street': 'tertiary'}


# ==================================================================================
# projection
# ==================================================================================
def local_to_global(x, y):
    """Playable metres -> (lat, lon). y grows southwards, so it subtracts."""
    return (LAT_CENTER - (y - HALF_M) / M_PER_DEG,
            LON_CENTER + (x - HALF_M) / M_PER_DEG_LON)


def global_to_local(lat, lon):
    """(lat, lon) -> playable metres. The inverse of local_to_global."""
    return (HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON,
            HALF_M - (lat - LAT_CENTER) * M_PER_DEG)


def bounds():
    """The four values of the OSM `<bounds>` element, as (minlat, minlon, maxlat,
    maxlon). The south-west corner is local (0, PLAYABLE_M); the north-east is
    (PLAYABLE_M, 0)."""
    minlat, minlon = local_to_global(0.0, PLAYABLE_M)
    maxlat, maxlon = local_to_global(PLAYABLE_M, 0.0)
    return minlat, minlon, maxlat, maxlon


def to_canvas(x, y):
    """Playable metres -> canvas metres (the DEM's frame, origin at its NW corner)."""
    return x + OFFSET_M, y + OFFSET_M


def from_canvas(xc, yc):
    return xc - OFFSET_M, yc - OFFSET_M


# ==================================================================================
# geometry helpers
# ==================================================================================
def polyline_length(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def ring_area_ha(ring):
    """Shoelace area in hectares. The ring may be given open or closed."""
    pts = ring[:-1] if len(ring) > 2 and math.dist(ring[0], ring[-1]) < 1e-9 else ring
    if len(pts) < 3:
        return 0.0
    twice = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return abs(twice) / 2.0 / 10000.0


def ring_perimeter(ring):
    pts = ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]
    return polyline_length(pts)


def close_ring(ring):
    return ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]


def rect_ring(x0, y0, x1, y1):
    """Axis-aligned rectangle as a closed ring, counter-clockwise in screen terms."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def clip_ring_to_rect(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman against an axis-aligned rectangle.

    Clamping a ring's coordinates instead folds whatever hangs over the edge onto the
    edge itself, which is how a strip of gallery timber ended up with a run of nodes
    lying across a river channel. Clipping only ever puts a new vertex on the ring's
    own boundary, and cuts a straight edge where the ring leaves the rectangle.

    Returns a closed ring, or [] if nothing of it is left inside.
    """
    poly = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    for keep, cut in ((lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)),
                      (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
                      (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)),
                      (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1))):
        out = []
        for i, b in enumerate(poly):
            a = poly[i - 1]
            if keep(b):
                if not keep(a):
                    out.append(cut(a, b))
                out.append(b)
            elif keep(a):
                out.append(cut(a, b))
        poly = out
        if not poly:
            return []
    return poly + [poly[0]]


def _cut_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def _cut_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def ellipse_ring(cx, cy, a, b, rot_deg=0.0, n=48):
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        u, v = a * math.cos(t), b * math.sin(t)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return out


def point_in_ring(pt, ring):
    """Even-odd test. The ring may be open or closed."""
    x, y = pt
    pts = ring[:-1] if math.dist(ring[0], ring[-1]) < 1e-9 else ring
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xx = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xx:
                inside = not inside
    return inside


def segs_cross(a, b, c, d):
    """True if segment ab properly crosses segment cd."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return False
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    return 0.0 < t < 1.0 and 0.0 < u < 1.0


def rings_overlap(p, q):
    """True if two simple rings share any area.

    A vertex test on its own is not enough, and the case it misses is not exotic: two
    rectangles crossing in a plus sign have no vertex of either inside the other, and
    that is exactly the shape a transversal shelterbelt makes against a north-south one.
    Two belts came out overlapping, drawn twice and counted twice in the inventory, and
    the check that was supposed to catch it said nothing. Test the edges as well.
    """
    if any(point_in_ring(v, q) for v in p) or any(point_in_ring(v, p) for v in q):
        return True
    return any(segs_cross(p[i], p[i + 1], q[j], q[j + 1])
               for i in range(len(p) - 1) for j in range(len(q) - 1))


def seg_point_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_polyline(p, pts):
    return min(seg_point_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def catmull_rom(pts, per_seg=16):
    """Centripetal-ish Catmull-Rom through the control points, endpoints duplicated."""
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
        for j in range(per_seg):
            t = j / per_seg
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t
                       + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in (0, 1)))
    out.append(tuple(pts[-1]))
    return out


def densify(pts, step):
    """Resample a polyline to roughly `step` between vertices, keeping the ends."""
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        d = step - carry
        while d < seg:
            t = d / seg
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            d += step
        carry = seg - (d - step)
    out.append(pts[-1])
    return out


def offset_polyline(pts, dist):
    """Offset a polyline sideways by `dist` (positive = left of travel).

    Only safe while `dist` stays under the radius of curvature: offset a meander by more
    than it bends and the ring folds through itself, and an even-odd fill then punches
    holes in the tightest bends. Reserves along water are stamped by distance to the
    centreline instead, never as offset polygons.
    """
    out = []
    n = len(pts)
    for i in range(n):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ll, dx / ll
        out.append((pts[i][0] + dist * nx, pts[i][1] + dist * ny))
    return out


def buffer_ring(pts, half_w):
    """Closed ring around a polyline: left side out, right side back."""
    left = offset_polyline(pts, half_w)
    right = offset_polyline(pts, -half_w)
    return close_ring(left + right[::-1])


def clip_polyline(pts, x0, y0, x1, y1):
    """Keep the vertices inside the box, splitting into runs. Coarse (vertex level),
    which is all that is needed at 40 m sampling."""
    runs, cur = [], []
    for p in pts:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _smoothstep(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def playable_sdf(x, y):
    """Signed distance to the playable boundary: negative inside, positive out in the
    border. The clean strip is everything between -EDGE_CLEAR_M and 0."""
    dx = max(-x, x - PLAYABLE_M)
    dy = max(-y, y - PLAYABLE_M)
    if dx <= 0.0 and dy <= 0.0:
        return max(dx, dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def rng():
    """The layout's random stream. Seeded, so both halves of the pipeline draw the same
    numbers - and key any jitter to position rather than to iteration order, or adding
    one feature shifts every feature after it."""
    return random.Random(SEED)


# ==================================================================================
# the registries
# ==================================================================================
# Everything the world is made of, in four lists. Both generators read them and neither
# defines geometry of its own, so a feature added here appears in the terrain and in the
# vectors together, and a feature added to only one half of the pipeline is the bug this
# arrangement exists to prevent.

# Roads and railway. One record per alignment:
#     id             short stable key, used in messages and by the DEM's build order
#     name           what goes in the OSM `name` tag
#     kind           'primary' | 'section' | 'track' | 'street' | 'rail'
#                    - the class decides the OSM highway tag (HIGHWAY_CLASS), how wide
#                      the graded platform is and which corridor wins where two cross
#     axis           [(x, y), ...] centreline in playable metres, running from EDGE_MIN
#                    to EDGE_MAX if it leaves the map: an alignment that stops at the
#                    canvas edge ends in a cliff
#     half_width_m   half the running surface
#     feather_m      nominal width of the fill either side; the DEM widens it to
#                    max(feather_m, 1.5 * |dz| / tan(4 deg)) wherever the cut is deep,
#                    because in a smoothstep the steepest gradient is 1.5 * rise / run
#                    and a constant feather cuts a step under a deep platform
#     grade_max      ruling grade, as a fraction (rail is ~0.015, a section road ~0.06)
#     bridge_spans   [(s0, s1), ...] arc lengths along the axis carried on a deck, so
#                    the DEM leaves the channel alone there and the OSM tags bridge=yes
CORRIDORS = (
    [_ns_road('road_west', 'Camino del Oeste', ROAD_W_X, ROAD_PRIMARY),
     _ns_road('road_east', 'Camino del Este', ROAD_E_X, ROAD_PRIMARY)]
    + [r for k, y in enumerate(PLSS_EW_Y)
       for r in _ew_road(f'section_{k + 1}', f'Camino de Servicio {k + 1}', y,
                         ROAD_SECTION, k in PLSS_BRIDGED)]
    + [s for site in TOWN_SITES for s in town_streets(*site)]
)

# Rivers, creeks and lakes. One record per body:
#     id, name       as above
#     kind           'river' | 'lake'
#     axis           centreline, for a watercourse
#     ring           closed shore, for a standing body
#     water_half_w   half-width of the drawn water surface; must equal the half-width at
#                    which the carved trough reaches the waterline, or the map paints
#                    water over dry bank
#     water_depth_m  water surface down to the bed. The profile the DEM carries along
#                    the channel is the **water surface**; the bed is cut this far under
#                    it, and the whole pipeline has to agree where that waterline is -
#                    the slope limiter, the platforms, the texture and the datum all
#                    take it as exempt ground.
WATER = [
    {'id': 'river', 'name': 'Rio Valle Bonito', 'kind': 'river',
     'axis': river_axis(), 'water_half_w': RIVER_HALF_W_M,
     'water_depth_m': RIVER_DEPTH_M},
    {'id': 'lake', 'name': 'Lago del Norte', 'kind': 'lake',
     'ring': lake_ring(), 'water_half_w': None,
     'water_depth_m': LAKE_DEPTH_M},
]

# Levelled platforms: village and farm yards, industrial aprons, anything that wants
# flat ground under it. One record per pad:
#     id, name, kind
#     centre         (x, y)
#     size           (w, h) in metres
#     ring           closed outline, normally rect_ring around centre
#     feather_m      nominal edge; widened with the cut the same way a corridor's is
#     drain_grade    residual fall across the pad, so a yard drains instead of terracing
#     tags           optional; OSM tags for the platform's own footprint. Absent means
#                    the pad is terrain only and what stands on it is drawn by AREAS
#
# A pad is a piece of *terrain* and nothing else: it says where the ground was levelled,
# not what stands on it. What is drawn over a town is its blocks, which are `AREAS`
# rings, for the same reason the island is an `AREAS` ring rather than something hanging
# off the lake record - that is all either of them is to the vectors. A pad that does
# want a footprint of its own carries a `tags` key and `emit_pads` draws it; the towns
# do not, or every block would be drawn twice.
#
# Build order is load-bearing: pads are graded before corridors, or a road platform
# overwrites the pad and leaves a step at its edge.
PADS = ([town_pad(*site) for site in TOWN_SITES]
        + [industry_pad(*site) for site in INDUSTRY_SITES]
        + [farm_pad(*site) for site in FARM_SITES])

# Tagged rings the OSM draws and the terrain mostly ignores: the island, the town
# blocks, the woods and the shelterbelts. One record per ring:
#     id, name
#     ring           closed outline in playable metres
#     tags           OSM tags; every one of them has to match RENDERED_TAGS or both
#                    renderers drop the way without a word
#
# The island is here rather than on the lake record because that is all it is to the
# vectors: a tagged ring, drawn after the water and so on top of it. The terrain knows it
# from the same ISLAND_* constants the ring is built from.
AREAS = (
    [{'id': 'island', 'name': 'Isla del Lago', 'ring': island_ring(),
      'tags': {'natural': 'wood', 'leaf_type': WOOD_LEAF_TYPE}}]
    + [b for site in TOWN_SITES for b in town_blocks(*site)]
    + [wood_area(*site) for site in WOOD_SITES]
    + [shelter_area(*site) for site in SHELTER_SITES]
    + [shelter_area_ew(*site) for site in SHELTER_EW_SITES]
)


def corridors():
    return list(CORRIDORS)


def water():
    return list(WATER)


def water_axes():
    """(polyline, name) for every watercourse that has a centreline. What the DEM's
    exempt-ground mask and the OSM's clearance checks both walk."""
    return [(w['axis'], w['name']) for w in WATER if w.get('axis')]


def pads():
    return list(PADS)


def areas():
    return list(AREAS)


def load_roughness(path=None):
    """Read `terrain_stats.json` and return a roughness lookup, or None if it is not
    there yet.

    This is how the OSM side finds out where the broken ground is without re-deriving
    the terrain in a second implementation that would drift - anything that wants to
    size itself to the ground reads it from here. The DEM has to run first; the OSM
    degrades gracefully if it has not. On a flat map every cell reads 0.
    """
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'dem_generator', 'terrain_stats.json')
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        data = json.load(fh)
    n = data['n']
    cell = data['cell_m']
    x0 = data['origin'][0]
    y0 = data['origin'][1]
    rough = data['roughness']

    def lookup(x, y):
        c = int((x - x0) // cell)
        r = int((y - y0) // cell)
        c = 0 if c < 0 else (n - 1 if c >= n else c)
        r = 0 if r < 0 else (n - 1 if r >= n else r)
        return rough[r * n + c]
    return lookup


# ==================================================================================
# self-check
# ==================================================================================
def validate():
    """Everything that has to be true about the layout before anything is built on it.
    Returns a list of complaints; empty means the layout is sound. Both generators
    refuse to run while it complains.

    Add a rule here when you find a placement mistake, rather than only moving the
    coordinates: on the map this replaces, three of the seven farmsteads turned out to
    be misplaced once the road-clearance rule existed, and only one of them had been
    visible.

    The checks below are the ones that hold on any map, empty or not. The ones that
    belong to particular features go in beside those features as they are added.
    """
    bad = []

    # The rim. It carries no geometry of its own, but it has a shape the rest of the
    # map has to live inside, and every one of these is a mistake that would look
    # plausible in the hillshade: a valley walled in at both ends, a range that dips
    # under its own saddle, a flank too steep to be a mountainside rather than a cliff.
    if RIM_RAMP_M <= 0.0:
        bad.append(f"rim: the apron ({RIM_APRON_M:.0f} m) and the shoulder "
                   f"({RIM_BACK_M:.0f} m) leave no border to climb the flank over")
    else:
        flank = math.degrees(math.atan(1.5 * (RIM_CREST_M - BASE_ELEV_M) / RIM_RAMP_M))
        if flank > RIM_MAX_FLANK_DEG:
            bad.append(f"rim: the flank measures {flank:.1f} deg at its steepest "
                       f"(1.5 * {RIM_CREST_M - BASE_ELEV_M:.0f} m / {RIM_RAMP_M:.0f} m), "
                       f"over the {RIM_MAX_FLANK_DEG:.0f} deg ceiling - lengthen the "
                       "ramp or lower the crest")
    if not BASE_ELEV_M < RIM_MOUTH_M < RIM_SADDLE_M <= RIM_CREST_M:
        bad.append(f"rim: heights out of order - the floor ({BASE_ELEV_M:.0f} m), the "
                   f"sill ({RIM_MOUTH_M:.0f} m), the saddles ({RIM_SADDLE_M:.0f} m) and "
                   f"the summits ({RIM_CREST_M:.0f} m) have to climb in that order")
    if RIM_MOUTH_M - 3.0 * RIM_MOUTH_VAR_M <= BASE_ELEV_M:
        bad.append(f"rim: the sill rolls +-{3 * RIM_MOUTH_VAR_M:.0f} m about "
                   f"{RIM_MOUTH_M:.0f} m, which dips under the valley floor and would "
                   "cut a notch out of the border")
    if RIM_MOUTH_M + 3.0 * RIM_MOUTH_VAR_M >= RIM_SADDLE_M:
        bad.append(f"rim: the sill reaches {RIM_MOUTH_M + 3 * RIM_MOUTH_VAR_M:.0f} m, "
                   f"as high as the range saddles - the valley would be closed at both "
                   "ends instead of running through")
    if RIM_SPUR_LAM_M >= RIM_RAMP_M or RIM_ROUGH_LAM_M >= RIM_RAMP_M:
        bad.append("rim: a flank feature longer than the flank itself reads as a tilt "
                   "of the whole range, not as a spur")
    if not 0.0 < RIM_WEST_SMOOTH <= 1.0:
        bad.append(f"rim: RIM_WEST_SMOOTH is {RIM_WEST_SMOOTH} - it damps the west "
                   "range toward the middle of its band, so it belongs in (0, 1]; at "
                   "zero the range is a wall of exactly one height")
    if int(RIM_RIDGE_LOBES) != RIM_RIDGE_LOBES or int(RIM_RIDGE_BEAT) != RIM_RIDGE_BEAT:
        bad.append("rim: the ridge lobe counts have to be whole numbers or the crest "
                   "steps where it closes at the north-west corner")
    elif math.gcd(int(RIM_RIDGE_LOBES), int(RIM_RIDGE_BEAT)) != 1:
        bad.append(f"rim: {RIM_RIDGE_LOBES} and {RIM_RIDGE_BEAT} lobes share a factor, "
                   f"so the two waves beat with a period of only "
                   f"{RIM_PERIM_M / math.gcd(int(RIM_RIDGE_LOBES), int(RIM_RIDGE_BEAT)):.0f} m "
                   "and the ranges repeat themselves")

    ids = [r['id'] for r in CORRIDORS + WATER + PADS + AREAS]
    for i in sorted(set(ids)):
        if ids.count(i) > 1:
            bad.append(f"{i}: used by {ids.count(i)} records - ids must be unique")

    for c in CORRIDORS:
        if c['kind'] not in HIGHWAY_CLASS and c['kind'] != 'rail':
            bad.append(f"{c['id']}: unknown corridor class {c['kind']!r}")
        if len(c['axis']) < 2:
            bad.append(f"{c['id']}: an alignment needs at least two points")
        ax = c['axis']
        # An alignment may only stop inside the canvas if it stops at the water. Anywhere
        # else it ends in mid-field, which reads as a road that was cut off rather than
        # one that ran out at a crossing nobody built.
        for end in (ax[0], ax[-1]):
            on_edge = (abs(end[0] - EDGE_MIN) < 1.0 or abs(end[0] - EDGE_MAX) < 1.0
                       or abs(end[1] - EDGE_MIN) < 1.0 or abs(end[1] - EDGE_MAX) < 1.0)
            if on_edge:
                continue
            # A town street is the other thing allowed to stop inside the canvas, and
            # only where it runs into the road at the end of it. Left unchecked, a grid
            # whose outer streets are a metre short of the cross street reads as eight
            # sticks laid beside each other: the OSM shares no node at that corner, the
            # network is disconnected, and nothing downstream would say so.
            if c['kind'] in ('street', 'track'):
                d = min(dist_to_polyline(end, o['axis']) for o in CORRIDORS
                        if o['id'] != c['id'])
                if d > 1.0:
                    bad.append(f"{c['id']}: ends at ({end[0]:.0f}, {end[1]:.0f}), "
                               f"{d:.0f} m from the nearest other alignment - a street "
                               "has to end on the road it meets, not in mid-block")
                continue
            d = dist_to_polyline(end, river_axis())
            if abs(d - ROAD_STUB_SETBACK_M) > 25.0:
                bad.append(f"{c['id']}: ends at ({end[0]:.0f}, {end[1]:.0f}), {d:.0f} m "
                           f"from the river - a stub has to stop on the bank, "
                           f"{ROAD_STUB_SETBACK_M:.0f} m out")
        # Every metre of alignment that runs over water has to be on a deck. A road
        # graded through a channel fills it in, and the heightmap then disagrees with
        # its own vectors about whether there is a river there.
        want = water_crossings(ax)
        have = list(c.get('bridge_spans', ()))
        if len(want) != len(have) or any(abs(a[0] - b[0]) > 1.0 or abs(a[1] - b[1]) > 1.0
                                         for a, b in zip(want, have)):
            bad.append(f"{c['id']}: crosses water at {want} but carries spans {have}")

    # The survey. These are the numbers the whole road layout is, so they are worth
    # asserting rather than trusting: a section grid that is not a mile is not a section
    # grid, and a trunk road that has drifted off its inset is invisible in every output.
    if abs(ROAD_W_X - MILE_M) > 1e-6 or abs(PLAYABLE_M - ROAD_E_X - MILE_M) > 1e-6:
        bad.append(f"roads: the trunks sit {ROAD_W_X:.1f} m and "
                   f"{PLAYABLE_M - ROAD_E_X:.1f} m in, not a mile ({MILE_M:.3f} m)")
    for a, b in zip(PLSS_EW_Y, PLSS_EW_Y[1:]):
        if abs((b - a) - MILE_M) > 1e-6:
            bad.append(f"roads: section lines {a:.1f} and {b:.1f} are {b - a:.1f} m "
                       f"apart, not a mile")
    ring = lake_ring()
    ly0 = min(p[1] for p in ring)
    ly1 = max(p[1] for p in ring)
    for y in PLSS_EW_Y:
        if ly0 <= y <= ly1:
            bad.append(f"roads: the section line at y={y:.0f} runs through the lake - "
                       f"move PLSS_EW_ANCHOR_M, the window is "
                       f"{ly1 - ly0 + MILE_M - MILE_M:.0f} m wide either side")
        if not EDGE_CLEAR_M < y < PLAYABLE_M - EDGE_CLEAR_M:
            bad.append(f"roads: the section line at y={y:.0f} is outside the map")

    for w in WATER:
        if not w.get('axis') and not w.get('ring'):
            bad.append(f"{w['id']}: water with neither a centreline nor a shore")

    # The water. Every one of these is a mistake the hillshade would not show: a river
    # that stops short of the canvas edge dies in mid-air, a channel polygon offset by
    # more than the meander bends folds through itself and punches holes in its own
    # tightest bends, an island that touches its lake is a peninsula, and a bed under the
    # bottom of the 16-bit range is a flat pan that still averages the right depth.
    axis = river_axis()
    if axis[0][1] > EDGE_MIN + 1e-6 or axis[-1][1] < EDGE_MAX - 1e-6:
        bad.append(f"river: the axis runs {axis[0][1]:.0f} .. {axis[-1][1]:.0f}, not out "
                   f"to {EDGE_MIN:.0f} .. {EDGE_MAX:.0f} - it would end at a cliff")
    curv = (RIVER_A1 * (2.0 * math.pi / RIVER_L1) ** 2
            + RIVER_A2 * (2.0 * math.pi / RIVER_L2) ** 2)
    r_min = 1.0 / curv if curv > 0 else float('inf')
    if RIVER_HALF_W_M >= r_min:
        bad.append(f"river: the meanders bend to a {r_min:.0f} m radius and the channel "
                   f"is drawn {RIVER_HALF_W_M:.0f} m out from the centreline - "
                   "`buffer_ring` folds the ring through itself at that offset")
    if min(p[0] for p in axis) < VALLEY_HALF_W_M or \
            max(p[0] for p in axis) > PLAYABLE_M - VALLEY_HALF_W_M:
        bad.append("river: a meander swings within one valley half-width of the map "
                   "edge, so the valley would be cut off square by the boundary")

    ring = lake_ring()
    if max(playable_sdf(x, y) for x, y in ring) > -EDGE_CLEAR_M:
        bad.append(f"lake: the shore reaches into the {EDGE_CLEAR_M:.0f} m clean strip")
    isl = island_ring()
    gap = min(min(math.dist(q, r) for r in ring) for q in isl)
    if gap < LAKE_SHELF_M:
        bad.append(f"island: {gap:.0f} m from the lake shore at its closest, under the "
                   f"{LAKE_SHELF_M:.0f} m the bed takes to fall - the two shelves would "
                   "meet and there would be no deep water anywhere in the lake")
    if not all(point_in_ring(q, ring) for q in isl):
        bad.append("island: part of it lies outside the lake shore, which makes it a "
                   "peninsula rather than an island")
    if ISLAND_H_M <= 0.0:
        bad.append("island: it has to break the surface to be an island")

    bed = LAKE_WS_M - LAKE_DEPTH_M
    if bed < 15.0:
        bad.append(f"lake: the bed lands at {bed:.1f} m, too close to the floor of the "
                   "16-bit range - raise BASE_ELEV_M rather than shallowing the lake")
    if VALLEY_HALF_W_M <= BANK_RUN_M:
        bad.append("water: the bank run eats the whole valley, so there is no valley")
    if RIVER_NOTCH_HALF_M < VALLEY_HALF_W_M:
        bad.append(f"river: the rim notch is {RIVER_NOTCH_HALF_M:.0f} m half-wide "
                   f"against a {VALLEY_HALF_W_M:.0f} m valley - the sill would ride up "
                   "on the valley side and dam the channel")
    if LAKE_WS_M >= BASE_ELEV_M:
        bad.append("water: the waterline is at or above the floodplain")

    # The till plain against the water. The relief is a sum of independent unit-variance
    # noises, so four sigma is a fair bound on how far it ever falls below the datum. If
    # that reaches the bank top the river comes out of its valley into the low ground,
    # which is not a thing the hillshade would show and not a thing any check downstream
    # would catch either.
    sigma = math.sqrt(LAND_MORAINE_M ** 2 + LAND_SWELL_M ** 2 + LAND_SWALE_M ** 2)
    floor = BASE_ELEV_M - 4.0 * sigma
    if floor <= LAKE_WS_M + WATER_BANK_M:
        bad.append(f"land: the relief can reach {floor:.1f} m and the bank top is "
                   f"{LAKE_WS_M + WATER_BANK_M:.1f} m - the water would come out of its "
                   "valley into the low ground")

    # The towns. Every one of these is a placement mistake that would look perfectly
    # plausible in either output on its own: a grid whose blocks are not the size they
    # are quoted at, a town the trunk road misses the middle of, or one standing in the
    # river's valley - which is 500 m wide and nowhere near any of these four junctions,
    # and would be a town on a hillside if it ever were.
    for tid, tname, cx, cy in TOWN_SITES:
        if abs(cx - ROAD_W_X) > 1e-6 and abs(cx - ROAD_E_X) > 1e-6:
            bad.append(f"{tid}: stands at x={cx:.0f}, which is neither trunk road - "
                       "the town is built round the road that runs up the middle of it")
        k = [i for i, y in enumerate(PLSS_EW_Y) if abs(y - cy) < 1e-6]
        if not k:
            bad.append(f"{tid}: stands at y={cy:.0f}, which is not a section line")
        elif k[0] not in PLSS_BRIDGED:
            bad.append(f"{tid}: stands on section line {k[0] + 1}, which has no bridge "
                       "- that road dead-ends at the river and carries nobody")
        d = dist_to_polyline((cx, cy), river_axis())
        reach = VALLEY_HALF_W_M + math.hypot(TOWN_HALF_W_M, TOWN_HALF_H_M)
        if d < reach:
            bad.append(f"{tid}: its centre is {d:.0f} m from the river, inside the "
                       f"{reach:.0f} m its own corner needs to clear the valley")
        blocks = town_blocks(tid, tname, cx, cy)
        if len(blocks) != TOWN_COLS * TOWN_ROWS:
            bad.append(f"{tid}: {len(blocks)} blocks, not "
                       f"{TOWN_COLS}x{TOWN_ROWS}")
        for b in blocks:
            xs = [q[0] for q in b['ring']]
            ys = [q[1] for q in b['ring']]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if abs(w - TOWN_BLOCK_W_M) > 1e-6 or abs(h - TOWN_BLOCK_H_M) > 1e-6:
                bad.append(f"{b['id']}: {w:.1f} x {h:.1f} m, not "
                           f"{TOWN_BLOCK_W_M:.0f} x {TOWN_BLOCK_H_M:.0f}")
                break
            if (max(abs(q[0] - cx) for q in b['ring']) > TOWN_HALF_W_M
                    or max(abs(q[1] - cy) for q in b['ring']) > TOWN_HALF_H_M):
                bad.append(f"{b['id']}: reaches outside the platform the town is "
                           "levelled onto, so it would stand on the feather")
                break

    # The roadside yards - the industrial aprons and the farms, which are the same shape
    # of thing and get the same rules. The geometry is derived from the road each one
    # hangs off, so what is worth asserting is not the arithmetic but the placement: that
    # the setback really is what the brief asked for, that a yard is the area and the
    # shape it is quoted at, and that none of them has landed somewhere a yard cannot go.
    # Every one of these would look perfectly ordinary in either output on its own.
    yards = [p for p in PADS if 'road' in p]
    want = len(INDUSTRY_SITES) + len(FARM_SITES)
    if len(yards) != want:
        bad.append(f"yards: {len(yards)} built from {want} sites")
    for p in yards:
        c = corridor_by_id(p['road'])
        if c['kind'] not in p['road_kinds']:
            bad.append(f"{p['id']}: hangs off {p['road']}, which is a {c['kind']} - a "
                       f"{p['kind']} belongs on "
                       f"{' or '.join(p['road_kinds'])}, and nothing else")
        # The near fence against the running surface, taken off the ring and the axis
        # rather than off the constants they were both built from - which is the whole
        # point of a check. The half-extent that matters is the one across the road.
        cx, cy = p['centre']
        w, h = p['size']
        ax = c['axis']
        across = w if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]) else h
        near = dist_to_polyline((cx, cy), ax) - across / 2.0
        gap = near - c['half_width_m']
        if abs(gap - p['setback_m']) > 0.01:
            bad.append(f"{p['id']}: its fence stands {gap:.1f} m off the edge of "
                       f"{p['road']}, not the {p['setback_m']:.0f} m asked for")
        if gap + c['half_width_m'] < c['feather_m']:
            bad.append(f"{p['id']}: {near:.1f} m to the centreline of {p['road']}, "
                       f"inside the {c['feather_m']:.0f} m that class of road keeps "
                       "clear - the apron would be standing on the verge")
        area = ring_area_ha(p['ring'])
        if abs(area - p['area_ha']) > 1e-6:
            bad.append(f"{p['id']}: {area:.4f} ha, not {p['area_ha']:.2f}")
        if abs(w - h) > 1e-6:
            bad.append(f"{p['id']}: {w:.1f} x {h:.1f} m - the aprons are square")
        # Clear of the water. Not the centre but every corner, and by a full valley
        # half-width: past that the ground is back on the till plain, and inside it an
        # apron is a platform cut into a valley side.
        ring = lake_ring()
        for q in p['ring'][:-1]:
            dr = dist_to_polyline(q, river_axis())
            dl = 0.0 if point_in_ring(q, ring) else min(math.dist(q, r) for r in ring)
            if min(dr, dl) < VALLEY_HALF_W_M:
                bad.append(f"{p['id']}: a corner is {min(dr, dl):.0f} m from open "
                           f"water, inside the {VALLEY_HALF_W_M:.0f} m valley - the "
                           "apron would be levelled into a valley side")
                break
        # Clear of everything else on the map, including the road it is not on.
        for o in PADS:
            if o['id'] == p['id']:
                continue
            ox, oy = o['centre']
            ow, oh = o['size']
            if abs(cx - ox) < (w + ow) / 2.0 and abs(cy - oy) < (h + oh) / 2.0:
                bad.append(f"{p['id']}: overlaps {o['id']}")
        for o in CORRIDORS:
            if o['id'] == p['road']:
                continue
            d = dist_to_polyline((cx, cy), o['axis'])
            reach = math.hypot(w, h) / 2.0
            if d < reach:
                bad.append(f"{p['id']}: {o['id']} runs within {d:.0f} m of its centre, "
                           f"inside its own {reach:.0f} m half-diagonal - a road "
                           "through a yard")

    # The woods. Same table shape as the yards and the same reasoning behind the rules,
    # with one difference that matters: a wood is only vectors, so the constraint is not
    # "can this ground be levelled" but "is this ring somewhere a wood can be drawn".
    # What that rules out is a road through the middle of one - which would be perfectly
    # realistic ground and is still wrong here, because it makes the 10 m setback the
    # wood was placed with meaningless: trees held off one road by ten metres and
    # bisected by the next. So the clearance is held against *every* alignment on the
    # map, not only the one the wood hangs off.
    woods = [a for a in AREAS if 'road' in a or 'line' in a or 'band' in a]
    want = len(WOOD_SITES) + len(SHELTER_SITES) + len(SHELTER_EW_SITES)
    if len(woods) != want:
        bad.append(f"plantings: {len(woods)} built from {want} sites")
    # Every ring drawn as a wood carries a leaf type, and which one is not free: a belt
    # is a hardwood windbreak on a field boundary and a wood - the island included - is
    # conifer. Both renderers colour the two apart, so a ring with the wrong value is a
    # planting drawn as the other kind rather than a tag nobody reads.
    for a in AREAS:
        if a['tags'].get('natural') != 'wood':
            continue
        want_leaf = SHELTER_LEAF_TYPE if ('line' in a or 'band' in a) else WOOD_LEAF_TYPE
        if a['tags'].get('leaf_type') != want_leaf:
            bad.append(f"{a['id']}: leaf type "
                       f"{a['tags'].get('leaf_type')!r}, not {want_leaf!r}")
    # Belts come in two orientations and neither of them has its length chosen: a
    # north-south belt spans one section between two cross roads, and a transversal one
    # spans the band a trunk road and the map edge leave. Both are the mile less two
    # clearances; they are different numbers only because the two clearances differ.
    ring = lake_ring()
    for a in woods:
        area = ring_area_ha(a['ring'])
        if abs(area - a['area_ha']) > 1e-6:
            bad.append(f"{a['id']}: {area:.4f} ha, not {a['area_ha']:.2f}")
        if 'road' in a:
            own = corridor_by_id(a['road'])
            near = min(dist_to_polyline(q, own['axis']) for q in a['ring'])
            if abs(near - own['half_width_m'] - ROADSIDE_SETBACK_M) > 0.01:
                bad.append(f"{a['id']}: its edge comes to "
                           f"{near - own['half_width_m']:.1f} m off the running surface "
                           f"of {a['road']}, not the {ROADSIDE_SETBACK_M:.0f} m asked "
                           "for")
        # Held off *every* alignment and not only its own. A road through the middle of
        # a planting is perfectly realistic ground and is still wrong here, because it
        # makes the setback the ring was placed with meaningless - trees ten metres off
        # one road and bisected by the next. It is also what holds a belt's ends on the
        # cross roads that are supposed to bound them.
        for c in CORRIDORS:
            keep = c['half_width_m'] + ROADSIDE_SETBACK_M
            d = min(dist_to_polyline(q, c['axis']) for q in a['ring'])
            if d < keep - 0.01:
                bad.append(f"{a['id']}: {c['id']} comes within {d:.0f} m of it, inside "
                           f"the {keep:.0f} m every road is held off")
                break
        for q in a['ring']:
            dr = dist_to_polyline(q, river_axis())
            dl = 0.0 if point_in_ring(q, ring) else min(math.dist(q, r) for r in ring)
            if min(dr, dl) < VALLEY_HALF_W_M:
                bad.append(f"{a['id']}: reaches {min(dr, dl):.0f} m from open water, "
                           f"inside the {VALLEY_HALF_W_M:.0f} m valley")
                break
        for p in PADS:
            px, py = p['centre']
            pw, ph = p['size']
            if any(abs(q[0] - px) < pw / 2.0 and abs(q[1] - py) < ph / 2.0
                   for q in a['ring']):
                bad.append(f"{a['id']}: overlaps {p['id']}")
                break
        for b2 in woods:
            if b2['id'] <= a['id']:
                continue
            if rings_overlap(close_ring(a['ring']), close_ring(b2['ring'])):
                bad.append(f"{a['id']}: overlaps {b2['id']}")
        if 'line' in a or 'band' in a:
            xs = [q[0] for q in a['ring']]
            ys = [q[1] for q in a['ring']]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            across, along = min(w, h), max(w, h)
            want_len = SHELTER_LEN_M if 'line' in a else SHELTER_LEN_EW_M
            if abs(across - SHELTER_W_M) > 1e-6:
                bad.append(f"{a['id']}: {across:.1f} m across, not "
                           f"{SHELTER_W_M:.0f} - a rompevientos is its width")
            if abs(along - want_len) > 1e-6:
                bad.append(f"{a['id']}: {along:.1f} m long, not the {want_len:.1f} m "
                           "its band leaves")
            if ('line' in a) != (h > w):
                bad.append(f"{a['id']}: runs the wrong way for the length it was given "
                           "- a section of frontage is north to south, a band is east "
                           "to west")

    m = EDGE_CLEAR_M
    for p in PADS:
        x0 = p['centre'][0] - p['size'][0] / 2
        x1 = p['centre'][0] + p['size'][0] / 2
        y0 = p['centre'][1] - p['size'][1] / 2
        y1 = p['centre'][1] + p['size'][1] / 2
        if x0 < m or y0 < m or x1 > PLAYABLE_M - m or y1 > PLAYABLE_M - m:
            bad.append(f"{p['id']}: stands in the {m:.0f} m clean strip along the "
                       "boundary")

    for a in AREAS:
        if not a['tags']:
            bad.append(f"{a['id']}: untagged - both renderers would drop it")
        elif not any(k in a['tags'] and (v is None or a['tags'][k] == v)
                     for k, v in RENDERED_TAGS):
            bad.append(f"{a['id']}: tagged {a['tags']} - neither renderer draws that")
        if len(a['ring']) < 4 or math.dist(a['ring'][0], a['ring'][-1]) > 1e-9:
            bad.append(f"{a['id']}: ring does not close on its first point")
        if max(playable_sdf(x, y) for x, y in a['ring']) > -m:
            bad.append(f"{a['id']}: reaches into the {m:.0f} m clean strip along the "
                       "boundary")
    return bad


def summary():
    """One-line description of the layout, for the generators to print."""
    if not (CORRIDORS or WATER or PADS or AREAS):
        return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, no features "
                f"yet, flat at {BASE_ELEV_M:.0f} m inside a valley rim rising to "
                f"{RIM_CREST_M:.0f} m")
    if not PADS:
        return (f"{PLAYABLE_M:.0f} m playable, till plain about {BASE_ELEV_M:.0f} m, a "
                f"river and a {ring_area_ha(lake_ring()):.0f} ha lake at "
                f"{LAKE_WS_M:.0f} m in a {2 * VALLEY_HALF_W_M:.0f} m valley, "
                f"{len(CORRIDORS)} roads on the mile grid, rim to {RIM_CREST_M:.0f} m")
    streets = sum(1 for c in CORRIDORS if c['kind'] == 'street')
    towns = sum(1 for p in PADS if p['kind'] == 'town')
    ind = [p for p in PADS if p['kind'] == 'industry']
    farms = [p for p in PADS if p['kind'] == 'farm']
    woods = [a for a in AREAS if 'station' in a]
    belts = [a for a in AREAS if 'line' in a or 'band' in a]
    return (f"{PLAYABLE_M:.0f} m playable, till plain about {BASE_ELEV_M:.0f} m, a river "
            f"and a {ring_area_ha(lake_ring()):.0f} ha lake in a "
            f"{2 * VALLEY_HALF_W_M:.0f} m valley, {len(CORRIDORS) - streets} roads on "
            f"the mile grid, {towns} towns of {TOWN_COLS}x{TOWN_ROWS} blocks on "
            f"{streets} streets, {len(ind)} industrial aprons of "
            f"{sum(p['area_ha'] for p in ind):.0f} ha, {len(farms)} farms of "
            f"{sum(p['area_ha'] for p in farms):.0f} ha, {len(woods)} woods of "
            f"{sum(a['area_ha'] for a in woods):.0f} ha and {len(belts)} shelterbelts "
            f"of {sum(a['area_ha'] for a in belts):.0f} ha, rim to "
            f"{RIM_CREST_M:.0f} m")


if __name__ == '__main__':
    print("=== map_layout self-check ===")
    print("  ", summary())
    sw = global_to_local(*local_to_global(0.0, PLAYABLE_M))
    ne = global_to_local(*local_to_global(PLAYABLE_M, 0.0))
    print(f"   centre {LAT_CENTER:.4f}, {LON_CENTER:.4f}; the projection round-trips "
          f"the corners to ({sw[0]:.3f}, {sw[1]:.3f}) and ({ne[0]:.3f}, {ne[1]:.3f})")
    print(f"   canvas metres run {-OFFSET_M:.0f} .. {PLAYABLE_M + OFFSET_M:.0f}, "
          f"alignments out to {EDGE_MIN:.0f} .. {EDGE_MAX:.0f}")
    problems = validate()
    if problems:
        print("\n   PROBLEMS")
        for p in problems:
            print("    -", p)
    else:
        print("\n   layout is sound")
