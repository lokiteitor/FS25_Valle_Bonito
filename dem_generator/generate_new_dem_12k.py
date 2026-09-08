#!/usr/bin/env python3
"""FS25 heightmap generator.

Builds the 12288x12288 m canvas (1 px = 1 m) with the 8192x8192 m playable area centred
in it. The playable square is a till plain whose mean is `map_layout.BASE_ELEV_M`, with a
river and a lake cut into it in a valley of their own, the Public Land Survey road grid
graded across it, and four towns levelled onto it where a trunk road meets a bridged
section line. The 2048 m border around it is the wall of the valley the map sits in. Two mountain ranges stand in the east and west
border and climb from the valley floor at 20 m to summits at `RIM_CREST_M`, 250 m, with
saddles between them at `RIM_SADDLE_M`; north and south the valley runs on out of the map
over a sill at `RIM_MOUTH_M`, so the horizon closes on two sides and opens on two.

The rim is built the way the rim always has to be built here: **last, and by addition**.
Anything already in the border rides up the flank intact under `z + h`, where a second
surface blended in would smear it out; and the ramp is a smoothstep of the *4-norm* of
the distance outside the playable square, because a plain maximum creases along the
diagonals and puts four seams out of the corners. It is also entirely and exactly zero
inside the playable boundary and across the `RIM_APRON_M` of apron beyond it, which is
what lets the playable square still measure flat to the centimetre with the flank of a
250 m range starting 100 m outside it.

Everything the terrain would be shaped around - where the water runs, where the roads
are, where the yards sit - comes from `map_layout.py` at the root of the tree, which the
OSM generator reads too. Neither half invents its own geometry: the rim's constants live
there and its one implementation is `terrain_ops.rim_field`, which the acceptance script
reads back to find the strips it reports on. A feature added to the registries without a
sculpting stage here is the exact failure the shared-geometry rule exists to prevent -
the vectors would draw a river the ground knows nothing about - so `sculpt()` carries the
whole build order in its docstring and the acceptance script checks that every platform
in the layout is both carved and drawn. The rim needs no vectors of its own because none
of it is ground the player can reach.

Heights are stored as 16-bit centimetres (raw / 100 = metres), matching the rest of the
project and Giants Editor's import convention. 20 m is raw 2000.

Two pieces of the machinery are worth knowing about before reading the code, because
they are what the sculpting stages will be built back on top of:

* The relief is synthesised at 3072x3072 (4 m per pixel) and resampled once to the full
  canvas. At full resolution a single Gaussian blur costs 7.2 s and one distance
  transform costs 12.7 s and 5.6 GB; a real terrain pipeline needs about twenty of them.
  Nothing in the terrain may have a wavelength under ~110 m, which is an order of
  magnitude above the Nyquist limit of the working grid, so the resampling loses nothing.
* The canvas metre of working pixel `j` is `4j + 2`, and the centre of output pixel `i`
  is `i + 0.5`. Getting that wrong shifts the terrain against the vectors by metres and
  is invisible in the image.

The primitives the sculpting is written with - `soft_min`, `limit_grade`, `limit_slope`,
`polyline_field`, the envelopes - are all still in `terrain_ops.py`, untouched.
"""
import json
import math
import os
import sys
import time

import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

from scipy import ndimage

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402
import terrain_ops as ops                                           # noqa: E402

# --- canvas geometry -------------------------------------------------------------------
CANVAS_M = int(ml.CANVAS_M)
PLAYABLE_M = int(ml.PLAYABLE_M)
OFFSET_M = int(ml.OFFSET_M)

WORK_PX = 3072
WORK_DX = CANVAS_M / WORK_PX          # 4 m per working pixel
BAND_ROWS = 1024                      # output is written in twelve of these

# --- datum -----------------------------------------------------------------------------
BASE_ELEV_M = ml.BASE_ELEV_M          # the height of the blank sheet, from the layout
Z_MAX_CM = 62000.0                    # Giants' working ceiling, in centimetres

# --- surface finish --------------------------------------------------------------------
# Both off while the map is a base plane: the brief is BASE_ELEV_M everywhere, exactly,
# and 4 cm of micro-relief plus a centimetre of dither would make it BASE_ELEV_M +- 5 cm
# and put a texture in the file that nothing asked for. They are kept here rather than
# deleted because they are the last two stages of any real terrain and both have to come
# back with it: the micro-relief has to stay off running surfaces and channels (4 cm over
# the 25 m the ruling grade is measured across is 8 cm of slope, a quarter of the
# railway's whole budget), and the dither decorrelates the rounding error from the
# surface so a flat yard shows grain instead of contour banding.
MICRO_AMP_M = 0.0                     # surface texture, added at full resolution
MICRO_LAM_M = 14.0
DITHER_CM = 0.0

# How softly the water's cross-section meets the ground it is cut into. The section
# already arrives at the floodplain with zero gradient, so this only has to round the
# corner where the river's valley and the lake's overlap; a large k here would eat the
# valley rim itself.
WATER_BLEND_M = 1.5

# The widest a road platform's feather is allowed to grow. It is sized at
# 1.5*|dz|/tan(4 deg), which is right and which runs away where a road crosses ground it
# disagrees with by a lot: against a riverbed five metres under the floodplain it once
# reached 170 m, and six roads filled the channel to within a metre of its lip. The water
# is taken out of `dz` first; this is the belt to that pair of braces.
FEATHER_CAP_M = 120.0

MASTER_SEED = ml.SEED
# Named streams with fixed, spaced indices: adding one later must not shift the streams
# that already exist, or the whole terrain changes underneath you.
STREAMS = {'moraine': 20, 'swell': 21, 'swale': 22, 'warp_x': 23, 'warp_y': 24,
           'rim_spur': 10, 'rim_rough': 11, 'micro': 40, 'dither': 41}

STATS_GRID = 128                      # terrain_stats.json resolution
# What counts as fully broken ground, as a gradient. The parcelling reads this to size
# fields, so it has to discriminate across the ground the map actually has: at the 3%
# it was set to while the map was a flat plate, the till plain's own swells saturate it
# and every cell on the map reads 1.000, which tells the parcelling nothing at all. Six
# degrees puts the flats near zero, the moraine flanks in the middle and the valley
# sides at the top.
ROUGH_FULL_SCALE = 0.105              # tan(6 deg)


def rng_for(name):
    return np.random.default_rng([MASTER_SEED, STREAMS[name]])


# ==================================================================================
# the surface
# ==================================================================================
def build_base(X, Y):
    """Stage 1: the till plain, after the country round Royal in Clay County, Iowa.

    Three octaves and a warp. They are not a generic fbm with a fixed lacunarity - each
    is a different thing on the ground and they are sized and shaped separately:

    * the **moraines** are stretched `LAND_MORAINE_STRETCH` times along a northwest-
      southeast grain, because a recessional moraine is a line the ice edge stopped on
      and not a blob. Isotropic noise at this wavelength reads as hills, which is the
      one thing this landscape does not have.
    * the **swell and swale** is the till surface itself, aimless and a couple of metres.
    * the **swale** octave under it keeps the ground from looking rolled.
    * the **warp** displaces the whole lot by up to `LAND_WARP_M` at long wavelength, so
      no ridge line reads as the sine it is underneath.

    The real place has prairie potholes over all of this, and they are deliberately left
    out: closed depressions a metre or two deep read as craters at any vertical
    exaggeration that makes the rest of the relief visible.

    Called by the measurer too. It has to be: "where is the upland" needs one answer.
    """
    a = math.radians(ml.LAND_MORAINE_GRAIN_DEG)
    c, sn = math.cos(a), math.sin(a)
    wx = ml.LAND_WARP_M * ops.value_noise(X, Y, ml.LAND_WARP_LAM_M, rng_for('warp_x'),
                                          CANVAS_M)
    wy = ml.LAND_WARP_M * ops.value_noise(X, Y, ml.LAND_WARP_LAM_M, rng_for('warp_y'),
                                          CANVAS_M)
    Xw, Yw = X + wx, Y + wy

    # Along the grain and across it. Dividing the along-grain coordinate by the stretch
    # is what makes one wavelength cover more ground in that direction.
    u = (Xw * c + Yw * sn) / ml.LAND_MORAINE_STRETCH
    v = -Xw * sn + Yw * c
    z = (BASE_ELEV_M
         + ml.LAND_MORAINE_M * ops.value_noise(u, v, ml.LAND_MORAINE_LAM_M,
                                               rng_for('moraine'), CANVAS_M)
         + ml.LAND_SWELL_M * ops.value_noise(Xw, Yw, ml.LAND_SWELL_LAM_M,
                                             rng_for('swell'), CANVAS_M)
         + ml.LAND_SWALE_M * ops.value_noise(Xw, Yw, ml.LAND_SWALE_LAM_M,
                                             rng_for('swale'), CANVAS_M))

    return z.astype(np.float32)


def sculpt(z, X, Y):
    """Where the terrain work goes.

    Stages 1, 2, 4 and 5 are built; stage 3 waits on ground that needs it. The build
    order is load-bearing and this is the order:

        1. the landscape        fbm relief, warped, before anything is cut into it
        2. water                carved with `soft_min`, not blended - a weighted blend
                                leaves a band of half-attenuated noise and a valley of
                                constant width, while the smooth minimum leaves the
                                ground outside exactly as it was and puts the rim where
                                the two surfaces cross. The channel holds water, so the
                                profile carried along it is the water **surface** and the
                                bed is cut under it; every later stage takes that wet mask
                                as exempt ground or the channel fills back in.
        3. slope limiting       before the platforms, never after: diffusing a finished
                                embankment ruins it
        4. pads, then corridors yards first, or a road platform overwrites the pad and
                                leaves a step at its edge. Feathers widen with the cut,
                                `max(nominal, 1.5*|dz|/tan(4 deg))`, measured against the
                                land and not against water five metres under it. Built:
                                `grade_pads` levels the four towns, `grade_corridors`
                                cuts the roads and the town streets in on top of them
        5. the rim              last, and by addition (`z + h`), so everything already in
                                the border rides up the flank intact. Built: `build_rim`
                                raises the valley wall around the playable square.

    It refuses rather than ignoring: a layout that carries features this generator does
    not sculpt is the exact failure the shared-geometry rule exists to prevent - the
    vectors would show a river the ground knows nothing about.
    """
    wet, water_z, d_river = water_fields(X, Y, z)
    # `np.minimum`, not `soft_min`, and only because of how the two surfaces meet here:
    # the cross-section arrives at the floodplain with zero first *and* second derivative
    # and stays there, so the two are tangent over the whole outer region and a hard
    # minimum introduces no crease at all. A smooth minimum does the opposite of its job
    # against tangent surfaces - its `k*h*(1-h)` term is a penalty for being close, and
    # with the two exactly equal it charges the full `k/4`, digging a 37 cm moat right
    # along the rim of the valley. Give the floodplain relief of its own and the two stop
    # being tangent; that is when this has to become `soft_min` again.
    z = np.minimum(z, water_z)
    z, built_pads = grade_pads(z, X, Y, wet)
    z, built = grade_corridors(z, X, Y, wet)
    np.maximum(built, built_pads, out=built)
    return z + build_rim(X, Y, d_river), wet, built


BANK_DEG = 4.0            # the slope every feather is sized to hold


def grade_pads(z, X, Y, wet):
    """Stage 4a: level the ground under a yard. Returns the ground and the graded mask.

    Before the corridors and never after: a road platform laid over a finished yard
    overwrites it and leaves a step at the edge of the pad, and driving it is the only
    thing that would show that. It is also what makes the town streets cheap - graded
    onto a platform that is already flat, a 6 m street with an 8 m feather has almost no
    cut to make, which is the only reason a corridor that thin is allowed on a 4 m
    synthesis grid at all.

    Three things here are the ones this pipeline has already been caught by, and they
    are the same three the corridors are:

    * **The platform sits on the ground it replaces.** Its height is the median of the
      land inside the ring, so a town lands on the till plain rather than on the datum -
      which is only the *mean* of the uplands, and would stand a pad on low ground up to
      a storey proud of it.
    * **The feather widens with the cut**, `max(nominal, 1.5*|dz|/tan(4 deg))`, because
      in a smoothstep the steepest gradient is 1.5*rise/run and a constant feather cuts a
      step wherever the platform sits deep. The water is taken out of `dz` before that is
      measured and the weight is kept off it afterwards, for the same reason a road's is:
      against a bed five metres under the floodplain the feather runs away to 170 m and
      fills the channel in.
    * **A pad is not dead flat.** `drain_grade` leaves a residual fall across it, to the
      south, so the yard drains instead of terracing. It is a third of a percent - far
      under any road's ruling grade, so a corridor crossing the pad still holds its own.
    """
    tan_bank = math.tan(math.radians(BANK_DEG))
    built = np.zeros(z.shape, dtype=np.float32)
    for p in ml.pads():
        cx, cy = p['centre']
        w, h = p['size']
        d = ops.rect_sdf(X, Y, cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        on = (d <= 0.0) & (wet < 0.25)
        if not bool(on.any()):
            continue
        # The drain tilt is clamped to the pad's own extent. Left to run on, the target
        # plane keeps climbing past the edge of the platform while the ground under it
        # does whatever it does, so `dz` - and with it the feather sized at
        # `1.5*|dz|/tan(4 deg)` - grows with distance instead of settling. On a 310 m
        # pad that is invisible; on a 910 m one it put 6.4 degrees of ground 90 m off
        # the north-west corner, out past any mask, in the middle of what the report
        # calls the uplands. Clamped, the feather has one fixed height to come down
        # from, which is what a feather is.
        sy = np.clip(cy - Y, -h / 2.0, h / 2.0)
        target = (float(np.median(z[on]))
                  + p['drain_grade'] * sy).astype(np.float32)
        dz = target - z
        dry_dz = np.where(wet > 0.25, 0.0, dz)
        feather = np.clip(1.5 * np.abs(dry_dz) / tan_bank, p['feather_m'],
                          FEATHER_CAP_M)
        wgt = (1.0 - ops.smoothstep(d / feather)) * (1.0 - np.clip(wet, 0.0, 1.0))
        z = z + wgt * dz
        np.maximum(built, ops.smoothstep(-d / 8.0), out=built)
    return z, built


def grade_corridors(z, X, Y, wet):
    """Stage 4: cut the roads in. Returns the ground and the graded-ground mask.

    Lower classes first and the trunk roads last, so where two cross it is the higher
    class that keeps its platform - a section road stamped over a primary leaves a step
    across the primary's running surface, and nothing but driving it would show that.

    Three things here are the ones this pipeline has already been caught by:

    * **The profile comes from `limit_grade`**, the mean of the two Lipschitz envelopes.
      It is exact in two passes and balances cut against fill. Clipping the slope
      forward and then backward - the obvious way - is not idempotent and walks the whole
      profile downhill.
    * **The feather widens with the cut**, `max(nominal, 1.5*|dz|/tan(4 deg))`, because
      in a smoothstep the steepest gradient is 1.5*rise/run and a constant feather cuts a
      step wherever the platform sits deep.
    * **Nothing is graded under a bridge, and nothing is graded on water.** The span is
      the one `map_layout.water_crossings` computed and the OSM tags `bridge=yes` from,
      so the deck and the hole in the terrain are the same hole. The ground line the
      profile is fitted to skips the channel too: fitted through it, the road dives 5 m
      into the river and climbs out, and the ruling grade it reports is the riverbank.
    """
    tan_bank = math.tan(math.radians(BANK_DEG))
    x0, y0 = float(X[0, 0]), float(Y[0, 0])
    dx = float(X[0, 1] - X[0, 0])
    built = np.zeros(z.shape, dtype=np.float32)
    rank = {'track': 0, 'street': 0, 'section': 1, 'rail': 2, 'primary': 3}
    for c in sorted(ml.corridors(), key=lambda c: rank.get(c['kind'], 0)):
        dense = ml.densify(c['axis'], 20.0)
        arc = ops.polyline_arclen(dense)
        xs = np.array([p[0] for p in dense])
        ys = np.array([p[1] for p in dense])
        ground = ops.sample_bilinear(z, x0, y0, dx, dx, xs, ys)

        # The ground line the road is fitted to, with the water taken out of it. A span
        # is bridged, so what matters under it is nothing at all; interpolating across
        # gives the profile the abutment-to-abutment chord it actually has to hold.
        onwater = ops.sample_bilinear(wet, x0, y0, dx, dx, xs, ys) > 0.25
        for s0, s1 in c.get('bridge_spans', ()):
            onwater |= (arc >= s0 - 10.0) & (arc <= s1 + 10.0)
        if onwater.all():
            continue
        ground = np.interp(arc, arc[~onwater], ground[~onwater])
        prof = ops.limit_grade(ground, float(arc[1] - arc[0]), c['grade_max'])
        prof = ops.smooth_1d(prof, 1.5)

        half = c['half_width_m']
        reach = half + FEATHER_CAP_M
        d, sarc = ops.polyline_field(X, Y, dense, reach)
        target = np.interp(sarc, arc, prof).astype(np.float32)
        dz = target - z
        feather = np.clip(1.5 * np.abs(dz) / tan_bank, c['feather_m'], FEATHER_CAP_M)
        w = 1.0 - ops.smoothstep((d - half) / feather)

        # Off the water, and off the deck. The abutment is faired over 15 m so the
        # embankment meets the bridge rather than ending at it.
        w = w * (1.0 - np.clip(wet, 0.0, 1.0))
        for s0, s1 in c.get('bridge_spans', ()):
            w = w * (1.0 - ops.smoothstep((sarc - s0) / 15.0)
                     * ops.smoothstep((s1 - sarc) / 15.0))
        z = z + w * dz
        np.maximum(built, ops.smoothstep((half + 0.5 * c['feather_m'] - d) / 8.0),
                   out=built)
    return z, built


def water_fields(X, Y, land):
    """The water: one surface for the river and the lake together, and the wet mask.

    Returns `(wet, water_z, d_river)`. `water_z` is the surface the ground is cut down
    to - the floodplain far from the water, the valley side, the bank, and the bed under
    the waterline, all in one closed-form cross-section. `wet` is where the finished
    ground lies under the waterline; `d_river` is distance to the centreline, which the
    rim needs so it can let the river out of the map.

    Three things here are load-bearing and each of them was a bug on the map before this
    one:

    * **The profile carried along the channel is the water surface, not the bed.** The
      bed is cut under it. Everything downstream - the slope limiter, any platform, the
      texture, the datum's percentile - has to take `wet` as exempt ground, or four
      private opinions about where the water is fill the channel back in on a map that
      still measures as though they had not.
    * **The cross-section reaches the ground it is cut into exactly**, at the waterline
      plus `VALLEY_HALF_W_M`, and it gets there through a smootherstep whose first and
      second derivatives are both zero at that point. It closes on `land`, the local
      height of the till plain, and not on `BASE_ELEV_M`: the datum is only the *mean* of
      the uplands, and a section that closes on the mean subtracts the difference from
      every acre within half a kilometre of the water. Because it closes on the ground
      itself the two surfaces are tangent out there, which is what makes the hard minimum
      exact and a smooth one wrong - see the note where it is taken.
    * **The river is faded out inside the lake.** The axis runs straight over the island,
      and a channel carved there would cut a five-metre notch through it. Inside a lake
      there is no channel; there is a lake.
    """
    ss, sss = ops.smoothstep, ops.smootherstep
    axis = ml.river_axis()
    s_in, s_out, grade, length = ml.river_profile()
    reach = ml.VALLEY_HALF_W_M + ml.RIVER_NOTCH_HALF_M + ml.RIVER_NOTCH_FEATHER_M
    d_river, s_river = ops.polyline_field(X, Y, axis, reach)

    # The water surface: flat across the lake, falling at a constant grade above and
    # below it. Everything else in the cross-section hangs off this, which is what keeps
    # the bank two metres over the water at both ends of a river that drops 2.5 m.
    ws = np.where(s_river < s_in, ml.LAKE_WS_M + grade * (s_in - s_river),
                  np.where(s_river > s_out, ml.LAKE_WS_M - grade * (s_river - s_out),
                           ml.LAKE_WS_M)).astype(np.float32)

    # The valley climbs from the local bank top to the floodplain, and the rise is
    # `BASE_ELEV_M - bank` and not a constant taken off the lake's level. Written as a
    # constant it is only right at the lake: everywhere else the section lands at
    # `ws + 17` instead of at the floodplain, so the river's own 2.5 m of fall was being
    # subtracted from every field within a kilometre and a half of it, with a 1.7 m step
    # where the distance search stopped looking. The playable square measured 73.4 m over
    # ground that is 75.
    hw = ml.RIVER_HALF_W_M
    bank = ws + ml.WATER_BANK_M
    river_z = (ws
               - ml.RIVER_DEPTH_M * (1.0 - ss((d_river - 0.4 * hw) / (0.6 * hw)))
               + ml.WATER_BANK_M * ss((d_river - hw) / ml.BANK_RUN_M)
               + (land - bank)
               * sss((d_river - hw - ml.BANK_RUN_M)
                     / (ml.VALLEY_HALF_W_M - ml.BANK_RUN_M)))

    # The lake. `din` is metres inside its shore, `disl` metres outside the island's;
    # both come from `ellipse_r` on the constants the drawn rings are built from, so the
    # water is painted exactly where the basin is.
    # `ellipse_r` is a *normalised* radius - 1 on the shore - and turning it into metres
    # by multiplying by a mean radius is not the same thing as a distance. On a lake half
    # again as long as it is wide, and with a lobed shore on top of that, it compresses
    # the section by a fifth on the short axis and more where a lobe turns: the valley
    # came out 400 m wide instead of 500 on the north shore and its rim measured 12
    # degrees against the 5 it is built to. Dividing by the gradient of the field is what
    # makes it a distance, and it costs two `np.gradient` calls.
    dx = float(X[0, 1] - X[0, 0])
    q = ops.ellipse_r(X, Y, *ml.LAKE_C, ml.LAKE_A, ml.LAKE_B, ml.LAKE_ROT,
                      ml.LAKE_HARMONICS)
    qi = ops.ellipse_r(X, Y, *ml.LAKE_C, ml.ISLAND_A, ml.ISLAND_B, ml.LAKE_ROT)
    din = (1.0 - q) / ops.grad_mag(q, dx)
    disl = (qi - 1.0) / ops.grad_mag(qi, dx)
    lake_z = (ml.LAKE_WS_M
              - ml.LAKE_DEPTH_M * ss(din / ml.LAKE_SHELF_M) * ss(disl / ml.LAKE_SHELF_M)
              + ml.ISLAND_H_M * ss(-disl / ml.ISLAND_RISE_M)
              + ml.WATER_BANK_M * ss(-din / ml.BANK_RUN_M)
              + (land - ml.LAKE_WS_M - ml.WATER_BANK_M)
              * sss((-din - ml.BANK_RUN_M) / (ml.VALLEY_HALF_W_M - ml.BANK_RUN_M)))

    # Off the lake entirely the expression above is meaningless, but it is also far above
    # the ground by then, so the minimum simply never selects it. Pinning it high past
    # that keeps it that way without a discontinuity anywhere the two surfaces are within
    # blending distance of each other.
    lake_z = np.where(din > -ml.VALLEY_HALF_W_M, lake_z, 1.0e4).astype(np.float32)
    # The river is switched off over the island and nowhere else. Switching it off at the
    # lake shore instead - the obvious place - is what put the steepest slope on the
    # playable map right across the river's mouth: the channel arrives three metres under
    # the waterline, the lake's littoral shelf is barely wet that close in, and the two
    # were being swapped over a few tens of metres. Left alone they need no swap at all,
    # because the minimum of the two already scours the channel across the shelf and
    # hands over to the basin as the basin gets deeper, which is what a river entering a
    # lake actually does. The island is the one place the lake bed comes back up over the
    # channel, and a three-metre notch cut through an island is what the fade is for.
    river_z = river_z + (1.0e4 - river_z) * ss((ml.ISLAND_RISE_M - disl)
                                               / ml.ISLAND_RISE_M)

    water_z = ops.soft_min(river_z, lake_z, WATER_BLEND_M)
    wet = np.maximum(ss((ws - river_z) / 0.5) * (din <= 0.0),
                     ss((ml.LAKE_WS_M - lake_z) / 0.5))
    return wet.astype(np.float32), water_z, d_river


def rim_crest(X, Y):
    """Where the summits are around the rim and how high, as `(along, u, crest_abs)`.

    Split out of `build_rim` because the east range's *foot* has to follow its own crest:
    a spur runs out from a summit and a re-entrant sits under a saddle, so the toe cannot
    be worked out until the crest is. `u` is the dimensionless height in the saddle-to-
    crest band, and the measurer reads this too, so "where the summits are" has one
    answer.

    Two things here are load-bearing:

    * The ridge line is closed form - two sines beating against each other around the
      perimeter - rather than an RNG walk, so it comes out identically every run whatever
      else is added to the terrain first. The lobe counts are whole numbers because
      `along` wraps, and coprime so the pair beats over the whole ring and no two summits
      along the 8 km of a range come out at the same height.
    * `u` is built in the *dimensionless* band between the saddles and the summits and
      only then mapped to metres, so the crest cannot leave 200 .. 250 m on a range
      however the noise falls. `tanh` rather than a clip does the containing: a clip
      flattens the top of every summit that reaches for the ceiling into a plateau at
      exactly 250.00 m.
    """
    along = ops.rim_along(X, Y, PLAYABLE_M)
    tau = 2.0 * np.pi
    n_spur = ops.value_noise(X, Y, ml.RIM_SPUR_LAM_M, rng_for('rim_spur'), CANVAS_M)
    n_rough = ops.value_noise(X, Y, ml.RIM_ROUGH_LAM_M, rng_for('rim_rough'), CANVAS_M)
    ridge = (0.35 * np.sin(tau * ml.RIM_RIDGE_LOBES * along)
             + 0.15 * np.sin(tau * ml.RIM_RIDGE_BEAT * along + 1.1)
             + ml.RIM_SPUR_AMP * n_spur + ml.RIM_ROUGH_AMP * n_rough)

    # The west range reads smoother than the east. Damping `ridge` is the right place to
    # do it: it pulls the crest toward the middle of the saddle-to-crest band without
    # touching the band, so the west still climbs to a mountain range and simply wanders
    # less on the way along it. The selector is a function of X alone and turns over
    # inside the playable square, where the rim is identically zero - so however abrupt
    # it is, it cannot put a seam in any ground that exists.
    ridge = ridge * (1.0 - (1.0 - ml.RIM_WEST_SMOOTH)
                     * ops.smoothstep((0.5 * PLAYABLE_M - X) / (0.5 * PLAYABLE_M)))
    u = 0.5 + 0.5 * np.tanh(2.0 * ridge)                       # (0, 1), no plateaus
    peak = ml.RIM_SADDLE_M + (ml.RIM_CREST_M - ml.RIM_SADDLE_M) * u
    sill = ml.RIM_MOUTH_M + ml.RIM_MOUTH_VAR_M * (0.6 * n_spur + 0.4 * n_rough)
    return along, u, peak, sill


def rim_ramp(X, Y):
    """`(t, w)` for the rim, with the east range's toe following its own crest.

    The measurer takes its strips from this, so the two halves cannot disagree about
    where a range is or how far in it reaches.
    """
    _, u, _, _ = rim_crest(X, Y)
    toe = ml.RIM_EAST_TOE_X + ml.RIM_EAST_WANDER_M * (1.0 - u)
    _, t, w = ops.rim_field(X, Y, PLAYABLE_M, ml.RIM_APRON_M, ml.RIM_BACK_M, OFFSET_M,
                            east=(toe, ml.RIM_EAST_WARP_K))
    return t, w


def rim_crest(X, Y):
    """Where the summits are around the rim and how high: `(u, peak, sill)`.

    Split out of `build_rim` because the west range's *foot* has to follow its own crest -
    a spur runs out from a summit and a re-entrant sits under a saddle - so the toe cannot
    be worked out until the crest is. `u` is the dimensionless height in the saddle-to-
    crest band; the measurer reads this through `rim_ramp`, so "where the summits are" has
    one answer.

    Two things here are load-bearing:

    * The ridge line is closed form - two sines beating against each other around the
      perimeter - rather than an RNG walk, so it comes out identically every run whatever
      else is added to the terrain first. The lobe counts are whole numbers because
      `along` wraps, and coprime so the pair beats over the whole ring and no two summits
      along the 8 km of a range come out at the same height.
    * `u` is built in the *dimensionless* band between the saddles and the summits and
      only then mapped to metres, so the crest cannot leave 200 .. 250 m on a range
      however the noise falls. `tanh` rather than a clip does the containing: a clip
      flattens the top of every summit that reaches for the ceiling into a plateau at
      exactly 250.00 m.
    """
    along = ops.rim_along(X, Y, PLAYABLE_M)
    tau = 2.0 * np.pi
    n_spur = ops.value_noise(X, Y, ml.RIM_SPUR_LAM_M, rng_for('rim_spur'), CANVAS_M)
    n_rough = ops.value_noise(X, Y, ml.RIM_ROUGH_LAM_M, rng_for('rim_rough'), CANVAS_M)
    ridge = (0.35 * np.sin(tau * ml.RIM_RIDGE_LOBES * along)
             + 0.15 * np.sin(tau * ml.RIM_RIDGE_BEAT * along + 1.1)
             + ml.RIM_SPUR_AMP * n_spur + ml.RIM_ROUGH_AMP * n_rough)

    # The west range reads smoother than the east. Damping `ridge` is the right place to
    # do it: it pulls the crest toward the middle of the saddle-to-crest band without
    # touching the band, so the west still climbs to a mountain range and simply wanders
    # less along it. The selector is a function of X alone and turns over inside the
    # playable square, where the rim is identically zero on both
    # sides, so however abrupt it is it cannot put a seam in any ground that exists.
    ridge = ridge * (1.0 - (1.0 - ml.RIM_WEST_SMOOTH)
                     * ops.smoothstep((0.5 * PLAYABLE_M - X) / (0.5 * PLAYABLE_M)))
    u = 0.5 + 0.5 * np.tanh(2.0 * ridge)                       # (0, 1), no plateaus
    peak = ml.RIM_SADDLE_M + (ml.RIM_CREST_M - ml.RIM_SADDLE_M) * u
    sill = ml.RIM_MOUTH_M + ml.RIM_MOUTH_VAR_M * (0.6 * n_spur + 0.4 * n_rough)
    return u, peak, sill


def rim_ramp(X, Y):
    """`(t, w)` for the rim. The measurer takes its strips from this, so the two halves
    cannot disagree about where a range is."""
    _, t, w = ops.rim_field(X, Y, PLAYABLE_M, ml.RIM_APRON_M, ml.RIM_BACK_M, OFFSET_M)
    return t, w


def build_rim(X, Y, d_river=None):
    """The valley wall: two ranges east and west, a sill north and south.

    The whole thing is one height field, `t * (crest - datum)`, where `t` is the ramp out
    of the toe and `crest` is the elevation the rim reaches directly out from each pixel.
    The relief on the flank is the *horizontal* variation of `crest` scaled by `t`, so a
    summit and the saddle beside it grow their own spurs and re-entrants down the slope
    for free, and every one of them lands on the crest line rather than 30 m above it.
    Adding the texture on top of a finished ramp instead would push the summits straight
    through the 250 m the brief allows.

    Every side starts one apron past the playable boundary, so none of the rim is ground
    the player can reach and the playable square keeps its own relief right up to the
    edge.
    """
    _, peak, sill = rim_crest(X, Y)
    t, w = rim_ramp(X, Y)
    crest = sill + (peak - sill) * w
    lift = t * np.maximum(crest - BASE_ELEV_M, 0.0)

    # Let the river out. The rim is added on top of whatever is already in the border, so
    # over the channel it would lift the water forty metres on its way off the map - the
    # sill damming the river it is supposed to let through. The notch is wider than the
    # valley and feathered wider than the sill is tall, so what is left either side of the
    # water is a shoulder and not a gorge wall.
    if d_river is not None:
        lift = lift * ops.smoothstep((d_river - ml.RIVER_NOTCH_HALF_M)
                                     / ml.RIVER_NOTCH_FEATHER_M)
    return lift


# ==================================================================================
# output
# ==================================================================================
def write_stats(z, X, Y, path):
    """A coarse height and roughness grid for the OSM generator.

    The parcelling wants smaller fields on broken ground. Rather than have it re-derive
    the terrain (two implementations of one landscape, guaranteed to drift) or pull a
    150 megapixel PNG through numpy in a standard-library-only folder, the DEM publishes
    what it already knows, in JSON. On a flat map every cell reads 0 roughness.
    """
    p0 = int(OFFSET_M / WORK_DX)
    p1 = int((OFFSET_M + PLAYABLE_M) / WORK_DX)
    play = z[p0:p1, p0:p1]
    slope = np.tan(np.radians(ops.slope_deg(play, WORK_DX, baseline_m=40.0)))
    k = play.shape[0] // STATS_GRID
    hgt = play.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    slp = slope.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    rough = np.clip(slp / ROUGH_FULL_SCALE, 0.0, 1.0)
    with open(path, 'w') as fh:
        json.dump({'n': STATS_GRID, 'cell_m': PLAYABLE_M / STATS_GRID,
                   'origin': [0.0, 0.0],
                   'height': [round(float(v), 2) for v in hgt.ravel()],
                   'roughness': [round(float(v), 4) for v in rough.ravel()]}, fh)


def write_dem(z_work, built_work, out_path):
    """Resample to 1 m and quantise, one band at a time.

    Never materialises a full-resolution float array: the output is a preallocated uint16
    and each band is 1024 rows. Peak memory is about half a gigabyte instead of five.

    `built_work` is the graded-ground mask - the micro-relief is damped to a sixth of its
    amplitude on a platform, because a running surface is graded and gravel, not prairie.
    """
    n = CANVAS_M
    out = np.empty((n, n), dtype=np.uint16)
    cols = (np.arange(n, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX

    lattice = None
    if MICRO_AMP_M > 0.0:
        rng_micro = rng_for('micro')
        lattice_n = int(CANVAS_M / MICRO_LAM_M) + 4
        lattice = rng_micro.standard_normal((lattice_n, lattice_n)).astype(np.float32)
        lattice /= lattice.std()
    rng_dither = rng_for('dither')

    for b in range(n // BAND_ROWS):
        r0, r1 = b * BAND_ROWS, (b + 1) * BAND_ROWS
        rows = (np.arange(r0, r1, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX
        coords = np.stack(np.broadcast_arrays(rows[:, None], cols[None, :]))
        band = ndimage.map_coordinates(z_work, coords, order=3, mode='nearest',
                                       output=np.float32)
        if lattice is not None:
            blt = ndimage.map_coordinates(built_work, coords, order=1, mode='nearest',
                                          output=np.float32)
            mcoords = np.stack(np.broadcast_arrays(
                (np.arange(r0, r1, dtype=np.float32) / MICRO_LAM_M)[:, None],
                (np.arange(n, dtype=np.float32) / MICRO_LAM_M)[None, :]))
            micro = ndimage.map_coordinates(lattice, mcoords, order=3,
                                            mode='grid-wrap', output=np.float32)
            band += MICRO_AMP_M * micro * (1.0 - 0.85 * np.clip(blt, 0.0, 1.0))
        band *= 100.0
        if DITHER_CM > 0.0:
            # triangular dither: decorrelates the rounding error from the surface, so a
            # flat yard shows grain instead of contour bands
            band += DITHER_CM * (rng_dither.random(band.shape, dtype=np.float32)
                                 + rng_dither.random(band.shape, dtype=np.float32) - 1.0)
        np.clip(band, 0.0, Z_MAX_CM, out=band)
        out[r0:r1] = np.rint(band).astype(np.uint16)
    Image.fromarray(out).save(out_path)
    return out


# ==================================================================================
# figures
# ==================================================================================
def style(ax, title):
    ax.set_xlabel("X (East-West) [metres]", fontsize=11, fontweight='bold')
    ax.set_ylabel("Y (North-South) [metres]", fontsize=11, fontweight='bold')
    ax.grid(True, which='both', color='white', linestyle='--', linewidth=0.5, alpha=0.35)
    ax.tick_params(colors='white')
    for spine in ax.spines.values():
        spine.set_color('white')
    ax.yaxis.label.set_color('white')
    ax.xaxis.label.set_color('white')
    ax.set_title(title, fontsize=15, fontweight='bold', pad=14, color='white')


def shade(sub, vmin, vmax):
    ls = LightSource(azdeg=315, altdeg=45)
    return ls.shade(sub, cmap=plt.get_cmap('terrain'), blend_mode='overlay',
                    vert_exag=2.0, vmin=vmin, vmax=vmax)


def draw_layout(ax):
    """The layout on top of the terrain: if the two disagree, it shows here."""
    for c in ml.corridors():
        if c['kind'] in ('track', 'street'):
            continue
        ax.plot([p[0] for p in c['axis']], [p[1] for p in c['axis']],
                color=('#F59E0B' if c['kind'] == 'rail' else '#E5E7EB'),
                lw=(1.6 if c['kind'] == 'rail' else 0.9),
                ls=('--' if c['kind'] == 'rail' else '-'), alpha=0.85)
    for w in ml.water():
        if w.get('ring'):
            ax.fill([p[0] for p in w['ring']], [p[1] for p in w['ring']],
                    color='#0284C7', alpha=0.75)
        elif w.get('axis'):
            ax.plot([p[0] for p in w['axis']], [p[1] for p in w['axis']],
                    color='#38BDF8', lw=1.8)
    for p in ml.pads():
        ax.plot([q[0] for q in p['ring']], [q[1] for q in p['ring']],
                color={'industry': '#6366F1', 'farm': '#22C55E'}.get(
                    p.get('kind'), '#DB2777'), lw=1.2)


def draw_figures(raw, out_vis, out_detail):
    n = CANVAS_M
    k = n // 1024
    vis = raw.reshape(1024, k, 1024, k).mean(axis=(1, 3)) / 100.0
    vmin, vmax = np.percentile(vis, 0.5), np.percentile(vis, 99.5)
    # A flat canvas has no range to stretch a colour map over, and the hillshade would
    # divide by zero. Half a metre either side gives it something to work with and reads
    # as the single flat tone it is.
    if vmax - vmin < 1e-6:
        vmin, vmax = vmin - 0.5, vmax + 0.5

    fig, ax = plt.subplots(figsize=(11, 11), dpi=150)
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    ax.imshow(shade(vis, vmin, vmax), extent=[0, n, n, 0])
    im = ax.imshow(vis, extent=[0, n, n, 0], cmap='terrain', vmin=vmin, vmax=vmax,
                   alpha=0.0)
    ax.set_xticks(np.arange(0, n + 1, 1024))
    ax.set_yticks(np.arange(0, n + 1, 1024))
    style(ax, f"Full DEM canvas ({n}x{n} px, 1 px = 1 m)")
    ax.add_patch(plt.Rectangle((OFFSET_M, OFFSET_M), PLAYABLE_M, PLAYABLE_M, fill=False,
                               edgecolor='white', linewidth=2, linestyle='--',
                               label=f'Playable border ({PLAYABLE_M / 1000:.1f} km)'))
    ax.legend(loc='upper right', facecolor='black', labelcolor='white', fontsize=9)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("height [m]", color='white')
    cb.ax.tick_params(colors='white')
    cb.outline.set_edgecolor('white')
    plt.savefig(out_vis, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()

    p0 = (OFFSET_M * 1024) // n
    p1 = ((OFFSET_M + PLAYABLE_M) * 1024) // n
    sub = vis[p0:p1, p0:p1]
    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    ax.imshow(shade(sub, vmin, vmax), extent=[0, PLAYABLE_M, PLAYABLE_M, 0])
    im = ax.imshow(sub, extent=[0, PLAYABLE_M, PLAYABLE_M, 0], cmap='terrain',
                   vmin=vmin, vmax=vmax, alpha=0.0)
    if sub.max() - sub.min() > 2.0:
        xs = np.linspace(0, PLAYABLE_M, sub.shape[1])
        ax.contour(xs, xs, sub, levels=np.arange(np.floor(sub.min()), sub.max(), 2.0),
                   colors='white', linewidths=0.4, alpha=0.25)
    draw_layout(ax)
    ax.set_xticks(np.arange(0, PLAYABLE_M + 1, 1024))
    ax.set_yticks(np.arange(0, PLAYABLE_M + 1, 1024))
    style(ax, f"Playable area ({PLAYABLE_M / 1000:.1f} x {PLAYABLE_M / 1000:.1f} km)")
    ax.set_xlim(0, PLAYABLE_M)
    ax.set_ylim(PLAYABLE_M, 0)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label("height [m]", color='white')
    cb.ax.tick_params(colors='white')
    cb.outline.set_edgecolor('white')
    plt.savefig(out_detail, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()


# ==================================================================================
def main():
    t_start = time.time()
    print(f"=== FS25 DEM generator ({CANVAS_M}x{CANVAS_M} m canvas, "
          f"{PLAYABLE_M} m playable) ===")
    print("   ", ml.summary())
    problems = ml.validate()
    if problems:
        print("!! layout problems:")
        for p in problems:
            print("   -", p)
        return 1

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dem = os.path.join(script_dir, "dem_new_12k.png")
    out_stats = os.path.join(script_dir, "terrain_stats.json")
    out_vis = os.path.join(script_dir, "dem_new_visual_12k.png")
    out_detail = os.path.join(script_dir, "dem_new_visual_detail_12k.png")

    # Canvas metres of every working pixel. Pixel j spans [4j, 4j+4) of the canvas and
    # its centre is 4j + 2, offset back into playable metres.
    ax1 = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)
    X, Y = np.meshgrid(ax1, ax1)

    print(f"1. Till plain about {BASE_ELEV_M:.0f} m ({WORK_PX}x{WORK_PX} working "
          f"grid, {WORK_DX:.0f} m/px)...")
    z = build_base(X, Y)

    print(f"2. Sculpting: water and its valley, {len(ml.pads())} town platforms, "
          f"{len(ml.corridors())} roads and streets, then the rim to "
          f"{ml.RIM_CREST_M:.0f} m...")
    z, wet, built = sculpt(z, X, Y)
    print(f"   {float(wet.mean()) * 100:.2f}% under water, "
          f"{float((built > 0.5).mean()) * 100:.2f}% graded for roads")

    print("3. Publishing terrain_stats.json...")
    write_stats(z, X, Y, out_stats)

    print(f"4. Resampling to {CANVAS_M}x{CANVAS_M} and writing 16-bit centimetres...")
    raw = write_dem(z, built, out_dem)

    print("5. Figures...")
    draw_figures(raw, out_vis, out_detail)

    lo, hi = raw.min() / 100.0, raw.max() / 100.0
    play = raw[OFFSET_M:OFFSET_M + PLAYABLE_M, OFFSET_M:OFFSET_M + PLAYABLE_M]
    print(f"\n   canvas {lo:.2f} .. {hi:.2f} m, playable "
          f"{play.min() / 100.0:.2f} .. {play.max() / 100.0:.2f} m, "
          f"relief {hi - lo:.2f} m")
    print(f"   [+] {out_dem}")
    print(f"   [+] {out_stats}")
    print(f"   [+] {out_vis}")
    print(f"   [+] {out_detail}")
    print(f"   done in {time.time() - t_start:.1f} s")
    return 0


if __name__ == '__main__':
    sys.exit(main())
