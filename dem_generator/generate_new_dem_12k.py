#!/usr/bin/env python3
"""FS25 heightmap generator - northwest Iowa farmland.

Builds the 12288x12288 m canvas (1 px = 1 m) with the 8192x8192 m playable area centred
in it, and sculpts it into the country around Royal, Clay County, Iowa: the western edge
of the Des Moines Lobe. Gently rolling till plain with closed prairie-pothole
depressions, one river in a broad shallow valley across the southwest, a glacial lake
beside it, and graded platforms under the road grid, the railway, three villages and
seven farmsteads.

Everything the terrain is shaped around - where the river runs, where the roads are,
where the yards sit - comes from `map_layout.py` at the root of the tree, which the OSM
generator reads too. Neither half invents its own geometry.

Heights are stored as 16-bit centimetres (raw / 100 = metres), matching the rest of the
project and Giants Editor's import convention.

Two decisions worth knowing before reading the code:

* The relief is synthesised at 3072x3072 (4 m per pixel) and resampled once to the full
  canvas. At full resolution a single Gaussian blur costs 7.2 s and one distance
  transform costs 12.7 s and 5.6 GB; the pipeline needs about twenty of them. Nothing in
  the terrain has a wavelength under 110 m, which is an order of magnitude above the
  Nyquist limit of the working grid, so the resampling loses nothing real.
* The river and the lake are cut with a smooth minimum, not blended in. Outside the
  valley the ground is then left exactly as the landscape made it, and the valley rim
  falls where the two surfaces cross - so the valley widens over low ground and pinches
  where a rise comes down to the water, the way a real one does.
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
BASE_Z_M = 100.0                      # nominal datum the project has always used
DATUM_P01_M = 86.0                    # the 0.1th percentile of the playable area lands here
Z_MAX_CM = 62000.0                    # Giants' working ceiling, in centimetres

# --- landscape -------------------------------------------------------------------------
MASTER_SEED = 20250902
# Named streams with fixed, spaced indices: adding an octave later must not shift the
# streams that already exist, or the whole terrain changes underneath you.
STREAMS = {'warp_x1': 0, 'warp_y1': 1, 'warp_x2': 2, 'warp_y2': 3,
           'fbm_2600': 10, 'fbm_1450': 11, 'fbm_900': 12, 'fbm_560': 13,
           'fbm_340': 14, 'fbm_210': 15, 'fbm_130': 16,
           'rough_90': 21, 'rough_55': 22, 'lakebed': 30,
           'micro': 40, 'dither': 41,
           'rim_ridge': 50, 'rim_spur': 51, 'rim_detail': 52}

# 0.45 m/km. Any more and the regional tilt alone eats a third of the relief budget
# while contributing 0.06 degrees of slope - it buys height difference the map cannot
# then spend on the swales that make the ground read as rolling.
REGIONAL_GRADE = 0.45e-3
REGIONAL_DIR = (0.887, 0.462)

# Wavelength in metres, amplitude as a standard deviation in metres. The short end is
# deliberately heavy: a median slope of 1.5 degrees and 32 m of relief cannot both come
# out of 400-1400 m swells, and in Iowa that slope lives in the swales anyway.
FBM = ((2600.0, 1.20), (1450.0, 1.10), (900.0, 0.90), (560.0, 3.30),
       (340.0, 2.25), (210.0, 1.43), (130.0, 0.68))
WARP = ((130.0, 1900.0), (40.0, 700.0))     # amplitude, wavelength

# Relief the landscape alone carries inside the playable area, before the valley is cut.
# The valley then takes another ~14 m out of the bottom of it; the total is checked and
# reported at the end.
LANDSCAPE_RELIEF_M = 22.0

# Rounding hilltops at sigma = 60 m wipes out everything under a ~370 m wavelength,
# which is where most of the working slope lives. 30 m takes the edge off the texture
# without flattening the swales.
TILLAGE_MIX = 0.12
TILLAGE_SIGMA_M = 30.0

# --- finishing -------------------------------------------------------------------------
SLOPE_LIMIT_DEG = 8.5                 # at 4 m/px; leaves room under the 10 deg ceiling
FINAL_SMOOTH_PX = 1.0
MICRO_AMP_M = 0.04                    # surface texture, added at full resolution
MICRO_LAM_M = 14.0
DITHER_CM = 0.7
PLATFORM_FEATHER_ANGLE = math.radians(4.0)
PIN_BLEND_M = 400.0                   # how far a level-crossing correction is spread

STATS_GRID = 128                      # terrain_stats.json resolution

# --- the non-playable rim --------------------------------------------------------------
# Iowa has no mountains. These are not Iowa: they are the wall that closes the horizon
# off past the boundary, so the player sees hills rather than the flat plate the canvas
# would otherwise end on. Everything about them is measured outwards from the playable
# boundary, and the apron in front of them is left exactly as the landscape made it -
# the ground must not change character at the edge of play.
RIM_APRON_M = ml.RIM_APRON_M          # 500 m of untouched ground first
RIM_HEIGHT_M = ml.RIM_HEIGHT_M        # the highest summits, above the ground they stand on
RIM_RISE_M = 900.0                    # run from the apron to full height: ~12 deg flanks
RIM_RIDGE_LAM_M = 1500.0              # summits and saddles along the rim
RIM_SPUR_LAM_M = 430.0                # spurs and gullies down the flanks
RIM_DETAIL_LAM_M = 170.0
RIM_DETAIL_M = 9.0
RIM_LOW = 0.34                        # a saddle is this fraction of a summit
# The river leaves the map twice, and the rim is not allowed to dam it. The lift is held
# off the valley the river has already cut - `wall_w` out from the centreline, the same
# width the valley wall uses inside the map - and comes back over RIM_GORGE_FEATHER_M,
# so the water runs out through a gorge in the mountains instead of into their flank.
RIM_GORGE_HALF_W_M = ml.RIVER['wall_w']
RIM_GORGE_FEATHER_M = 700.0


def rng_for(name):
    ss = np.random.SeedSequence(MASTER_SEED).spawn(64)
    return np.random.Generator(np.random.PCG64(ss[STREAMS[name]]))


# ==================================================================================
# landscape
# ==================================================================================
def build_landscape(X, Y):
    """Rolling till plain: regional fall, seven octaves of warped value noise, tillage
    rounding and the closed depressions."""
    Xc, Yc = X + OFFSET_M, Y + OFFSET_M          # canvas metres, for the noise lattices

    wx1 = WARP[0][0] * ops.value_noise(Xc, Yc, WARP[0][1], rng_for('warp_x1'), CANVAS_M)
    wy1 = WARP[0][0] * ops.value_noise(Xc, Yc, WARP[0][1], rng_for('warp_y1'), CANVAS_M)
    wx2 = WARP[1][0] * ops.value_noise(Xc + wx1, Yc + wy1, WARP[1][1],
                                       rng_for('warp_x2'), CANVAS_M)
    wy2 = WARP[1][0] * ops.value_noise(Xc + wx1, Yc + wy1, WARP[1][1],
                                       rng_for('warp_y2'), CANVAS_M)
    Xw, Yw = Xc + wx1 + wx2, Yc + wy1 + wy2

    z = np.zeros(X.shape, np.float32)
    for lam, amp in FBM:
        z += amp * ops.value_noise(Xw, Yw, lam, rng_for(f'fbm_{int(lam)}'), CANVAS_M)

    z += -REGIONAL_GRADE * (REGIONAL_DIR[0] * (X - ml.HALF_M)
                            + REGIONAL_DIR[1] * (Y - ml.HALF_M))
    return z


def apply_tillage(z, w_field):
    """A century of tillage takes the tops off the rises and fills the swales."""
    return z + TILLAGE_MIX * w_field * (
        ndimage.gaussian_filter(z, TILLAGE_SIGMA_M / WORK_DX) - z)


def apply_potholes(z, X, Y):
    """Closed depressions, the signature of the Des Moines Lobe.

    The profile is (1 - r^2/R^2)^2: flat at the centre, flat at the rim, so it never
    leaves an edge. The steepest of them works out at 1.1 degrees - still perfectly
    farmable, which is the point. They have no outlet, and nothing downstream should
    treat that as a defect.
    """
    x0, y0 = float(X[0, 0]), float(Y[0, 0])
    for p in ml.potholes():
        cx, cy = p['centre']
        r, d = p['radius'], p['depth']
        c0 = int(max(0, (cx - r - x0) / WORK_DX))
        c1 = int(min(z.shape[1] - 1, (cx + r - x0) / WORK_DX + 1))
        r0 = int(max(0, (cy - r - y0) / WORK_DX))
        r1 = int(min(z.shape[0] - 1, (cy + r - y0) / WORK_DX + 1))
        if c1 <= c0 or r1 <= r0:
            continue
        sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
        rr = np.hypot(X[sl] - cx, Y[sl] - cy) / r
        m = np.clip(1.0 - rr * rr, 0.0, 1.0)
        z[sl] -= d * m * m
    return z


# ==================================================================================
# water
# ==================================================================================
def river_profile(z, X, Y, axis, ds=25.0):
    """Thalweg height along the river, strictly falling.

    Anchored to the natural ground rather than prescribed outright, then forced monotone.
    The regional fall already runs downstream at about 0.9 m/km, so the clamp rarely has
    to do anything and the valley keeps an even depth along its whole length.
    """
    pts = ml.densify(axis, ds)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    z_ref = ops.sample_bilinear(z, float(X[0, 0]), float(Y[0, 0]), WORK_DX, WORK_DX,
                                xs, ys)
    z_ref = ops.smooth_1d(z_ref, (2500.0 / ds) / 2.5)
    raw = z_ref - ml.RIVER['incision_m']
    prof = ops.monotone_descent(raw, ds, ml.RIVER['grade_min'], ml.RIVER['grade_max'])
    return pts, ops.polyline_arclen(pts), prof


def valley_surface(d, z_thal, spec):
    """The cross-section, as a height above the thalweg.

    bed -> inner bank -> floodplain -> valley wall -> linear continuation. Every step is
    a smoothstep, so the whole profile is C1 and there is no vertical cut anywhere. The
    bank width follows from the 8 degree ceiling: a smoothstep's steepest gradient is
    1.5*rise/run, so 2.2 m needs at least 27 m of bank.
    """
    w_bed = spec['bed_half_w']
    w_bank = spec['bank_w']
    w_fp = spec['floodplain_w']
    w_wall = spec['wall_w']
    bank = spec['bank_h'] * ops.smoothstep((d - w_bed) / w_bank)
    fp = spec['floodplain_cross'] * np.minimum(d, w_bed + w_bank + w_fp) \
        if 'floodplain_cross' in spec else 0.0
    wall = spec['wall_h'] * ops.smoothstep((d - (w_bed + w_bank + w_fp)) / w_wall)
    ext = spec.get('ext_grade', 0.02) * np.maximum(
        0.0, d - (w_bed + w_bank + w_fp + w_wall))
    return z_thal + bank + fp + wall + ext


def carve_river(z, X, Y):
    axis = ml.river_axis()
    pts, s_prof, z_prof = river_profile(z, X, Y, axis)
    reach = (ml.RIVER['bed_half_w'] + ml.RIVER['bank_w'] + ml.RIVER['floodplain_w']
             + ml.RIVER['wall_w'] + 400.0)
    d, s = ops.polyline_field(X, Y, pts, reach)
    z_thal = np.interp(np.clip(s, s_prof[0], s_prof[-1]), s_prof, z_prof)
    # The nearest point jumps from one limb to another on the inside of a tight meander,
    # so the sampled thalweg is smoothed once it is a field - never the arc length
    # itself, which would wreck the geometry.
    z_thal = ndimage.gaussian_filter(z_thal.astype(np.float32), 32.0 / WORK_DX)
    surface = valley_surface(d, z_thal, ml.RIVER)
    z = ops.soft_min(surface, z, ml.RIVER['softmin_k'])
    return z, (pts, s_prof, z_prof), d


def carve_lake(z, X, Y, river_pts, s_prof, z_prof):
    """A deep lake sitting on the river, not beside it.

    The main stem runs in at the head and out at the foot, so the lake is part of the
    drainage rather than a pond that happens to be nearby: the surface is the thalweg at
    the outlet shore, the bed upstream is above it and the bed downstream below it, and
    the water level follows from that instead of being asserted.

    The basin is 40 m deep, which needs the profile in two stages. A single smoothstep
    from the shore to the bottom would put a 20 degree bank at the waterline; instead
    there is a shelf you can wade off - 2.5 m over the outer seventh of the radius, under
    4 degrees - and then the drop, all of which is under water.
    """
    spec = ml.LAKE
    cx, cy = ml.lake_centre()
    rot = ml.lake_rot_deg()

    # the outlet shore: the furthest-downstream river vertex still inside the lake
    inside = [k for k, p in enumerate(river_pts) if ml._in_lake(p)]
    j = inside[-1] if inside else int(len(river_pts) // 2)
    z_lake = float(z_prof[j])

    r = ops.ellipse_r(X, Y, cx, cy, spec['semi_a'], spec['semi_b'], rot, ml.LAKE_SHORE)
    shelf_r, shelf_d = spec['shelf_r'], spec['shelf_depth']
    # shore -> shelf, gentle
    depth = shelf_d * (1.0 - ops.smoothstep((r - shelf_r) / (1.0 - shelf_r)))
    # shelf -> bottom, steep, and entirely below the waterline
    depth = depth + (spec['max_depth'] - shelf_d) * (
        1.0 - ops.smoothstep((r - spec['flat_r']) / (shelf_r - spec['flat_r'])))
    bed_noise = 0.60 * ops.value_noise(X + OFFSET_M, Y + OFFSET_M, 110.0,
                                       rng_for('lakebed'), CANVAS_M)
    basin = z_lake - depth + bed_noise * np.clip(1.4 * (1.0 - r), 0.0, 1.0)

    apron = z_lake + spec['apron_grade'] * (r - 1.0) * spec['semi_b']
    beyond = z_lake + spec['apron_grade'] * (spec['apron_r'] - 1.0) * spec['semi_b'] \
        + 0.025 * (r - spec['apron_r']) * spec['semi_b']
    surface = np.where(r <= 1.0, basin, np.where(r > spec['apron_r'], beyond, apron))
    z = ops.soft_min(surface.astype(np.float32), z, spec['softmin_k'])
    return z, z_lake


def carve_creek(z, X, Y):
    """The tributary off the northern uplands, down to the head of the lake."""
    axis = ml.creek_axis()
    pts = ml.densify(axis, 25.0)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    z_ref = ops.sample_bilinear(z, float(X[0, 0]), float(Y[0, 0]), WORK_DX, WORK_DX,
                                xs, ys)
    z_ref = ops.smooth_1d(z_ref, (900.0 / 25.0) / 2.5)
    prof = ops.monotone_descent(z_ref - ml.CREEK['incision_m'], 25.0,
                                ml.CREEK['grade_min'], ml.CREEK['grade_max'])
    reach = (ml.CREEK['bed_half_w'] + ml.CREEK['bank_w'] + ml.CREEK['floodplain_w']
             + ml.CREEK['wall_w'] + 200.0)
    d, s = ops.polyline_field(X, Y, pts, reach)
    s_prof = ops.polyline_arclen(pts)
    z_thal = np.interp(np.clip(s, s_prof[0], s_prof[-1]), s_prof, prof)
    z_thal = ndimage.gaussian_filter(z_thal.astype(np.float32), 24.0 / WORK_DX)
    surface = valley_surface(d, z_thal, ml.CREEK)
    return ops.soft_min(surface.astype(np.float32), z, ml.CREEK['softmin_k'])


# ==================================================================================
# platforms
# ==================================================================================
def _axis_span(axis):
    """Corridors here are strictly axis aligned. Returns ('ns'|'ew', fixed, a, b)."""
    x0, y0 = axis[0]
    x1, y1 = axis[-1]
    if abs(x1 - x0) < abs(y1 - y0):
        return 'ns', 0.5 * (x0 + x1), y0, y1
    return 'ew', 0.5 * (y0 + y1), x0, x1


def corridor_profile(z, X, Y, c, pins, ds=10.0):
    """Longitudinal rail or road profile: sample, bridge, smooth, then limit the grade."""
    axis = ml.densify(c['axis'], ds)
    xs = np.array([p[0] for p in axis])
    ys = np.array([p[1] for p in axis])
    s = ops.polyline_arclen(axis)

    sigma_m = {'rail': 120.0, 'primary': 48.0}.get(c['kind'], 48.0)
    zs = ndimage.gaussian_filter(z, sigma_m / WORK_DX)
    raw = ops.sample_bilinear(zs, float(X[0, 0]), float(Y[0, 0]), WORK_DX, WORK_DX,
                              xs, ys)

    # A span is not graded: without this the road would be levelled down into the valley
    # and would sit there as a fifteen metre earth dam across the channel.
    for (s0, s1) in c['bridge_spans']:
        inside = (s > s0) & (s < s1)
        if inside.any():
            i0 = max(0, int(np.argmax(inside)) - 1)
            i1 = min(len(s) - 1, len(s) - 1 - int(np.argmax(inside[::-1])) + 1)
            raw[i0:i1 + 1] = np.linspace(raw[i0], raw[i1], i1 - i0 + 1)

    l_smooth = {'rail': 900.0, 'primary': 180.0}.get(c['kind'], 120.0)
    prof = ops.smooth_1d(raw, (l_smooth / ds) / 2.5)
    prof = ops.limit_grade(prof, ds, c['max_grade'])

    # Pinning by overwriting one sample does not survive limit_grade: the mean of the
    # two envelopes halves any spike, so the road ends up meeting the railway about half
    # the pin error out - which is what put a 2.7% step in the track at two of the level
    # crossings. Shifting the profile by a wide, smooth bump moves it onto the pin
    # exactly and adds a gradient of its own of under 0.1%.
    for (px, py), pz in pins:
        i = int(np.argmin((xs - px) ** 2 + (ys - py) ** 2))
        if math.hypot(xs[i] - px, ys[i] - py) > 60.0:
            continue
        for _ in range(2):
            err = pz - prof[i]
            if abs(err) < 1e-4:
                break
            bump = np.exp(-0.5 * (((np.arange(prof.size) - i) * ds) / PIN_BLEND_M) ** 2)
            prof = prof + err * bump
            prof = ops.limit_grade(prof, ds, c['max_grade'])
        if os.environ.get('DEM_DEBUG_PINS'):
            print(f"      pin {c['id']:<22} at {px:.0f},{py:.0f}: "
                  f"residual {prof[i] - pz:+.3f} m")
    return axis, s, prof


def apply_corridor(z, X, Y, c, s_prof, z_prof, claimed=None):
    """Blend the profile in, with a feather that widens with the depth of the cut.

    `feather = max(nominal, 1.5*|dz| / tan(4 deg))` is what keeps the priority the brief
    asks for - natural ground, smooth transition, working platform - instead of hill,
    abrupt cut, perfectly flat surface. A constant feather would carve a step wherever
    the platform happens to sit deep.
    """
    orient, fixed, a, b = _axis_span(c['axis'])
    lo, hi = min(a, b), max(a, b)
    r_out = c['feather_m'] * 4.0 + 260.0
    x0, y0 = float(X[0, 0]), float(Y[0, 0])

    if orient == 'ns':
        c0 = int(max(0, (fixed - r_out - x0) / WORK_DX))
        c1 = int(min(z.shape[1] - 1, (fixed + r_out - x0) / WORK_DX + 1))
        r0 = int(max(0, (lo - 120.0 - y0) / WORK_DX))
        r1 = int(min(z.shape[0] - 1, (hi + 120.0 - y0) / WORK_DX + 1))
    else:
        r0 = int(max(0, (fixed - r_out - y0) / WORK_DX))
        r1 = int(min(z.shape[0] - 1, (fixed + r_out - y0) / WORK_DX + 1))
        c0 = int(max(0, (lo - 120.0 - x0) / WORK_DX))
        c1 = int(min(z.shape[1] - 1, (hi + 120.0 - x0) / WORK_DX + 1))
    if c1 <= c0 or r1 <= r0:
        return z
    sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
    XX, YY = X[sl], Y[sl]

    if orient == 'ns':
        d = np.abs(XX - fixed)
        s = (YY - a) if b > a else (a - YY)
    else:
        d = np.abs(YY - fixed)
        s = (XX - a) if b > a else (a - XX)

    total = float(s_prof[-1])
    z_tgt = np.interp(np.clip(s, 0.0, total), s_prof, z_prof).astype(np.float32)
    taper = ops.smoothstep(s / 60.0) * ops.smoothstep((total - s) / 60.0)

    cur = z[sl]
    feather = np.maximum(c['feather_m'],
                         1.5 * np.abs(z_tgt - cur) / math.tan(PLATFORM_FEATHER_ANGLE))
    w = (1.0 - ops.smoothstep((d - c['half_width_m']) / feather)) * taper

    for (b0, b1) in c['bridge_spans']:
        # the deck floats over the valley: leave the ground alone under it
        gap = ops.smoothstep((s - (b0 - 60.0)) / 60.0) * \
            ops.smoothstep(((b1 + 60.0) - s) / 60.0)
        w = w * (1.0 - gap)

    if claimed is not None:
        # A minor road may not overwrite a major one's platform. Both are pinned to the
        # same height at the crossing, so they meet; but letting a section road hold its
        # own level across a descending railway flattens a 100 m stretch of track and
        # forces it to make the fall up either side, which is what broke the 1.5%
        # ruling grade. The higher class was built first, and it keeps what it took.
        w = w * (1.0 - claimed[sl])
        claimed[sl] = np.maximum(claimed[sl], w)

    z[sl] = (1.0 - w) * cur + w * z_tgt
    return z


def apply_crossing_plateaus(z, X, Y):
    """Level the ground where the railway meets a road, before either platform is built.

    Both profiles then sample flat ground at the crossing and converge on the same height
    by themselves, which is a great deal more robust than reconciling them afterwards.
    """
    x0, y0 = float(X[0, 0]), float(Y[0, 0])
    smooth = ndimage.gaussian_filter(z, 200.0 / WORK_DX)
    for k in ml.crossings():
        cx, cy = k['xy']
        rad = k['pad_radius_m']
        c0 = int(max(0, (cx - 2 * rad - x0) / WORK_DX))
        c1 = int(min(z.shape[1] - 1, (cx + 2 * rad - x0) / WORK_DX + 1))
        r0 = int(max(0, (cy - 2 * rad - y0) / WORK_DX))
        r1 = int(min(z.shape[0] - 1, (cy + 2 * rad - y0) / WORK_DX + 1))
        if c1 <= c0 or r1 <= r0:
            continue
        sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
        r = np.hypot(X[sl] - cx, Y[sl] - cy)
        w = 1.0 - ops.smoothstep((r - rad * 0.5) / (rad * 0.5))
        z[sl] = (1.0 - w) * z[sl] + w * smooth[sl]
    return z


def apply_pads(z, X, Y):
    """Village and farm platforms.

    Not a horizontal plane: each carries a 0.5% fall along the local downhill, which is
    both how a real yard drains and how the centimetre quantisation is kept from
    terracing the surface into contour bands.
    """
    x0, y0 = float(X[0, 0]), float(Y[0, 0])
    regional = ndimage.gaussian_filter(z, 250.0 / WORK_DX)
    gy, gx = np.gradient(regional, WORK_DX)
    built = np.zeros(z.shape, np.float32)

    for p in ml.pads():
        cx, cy = p['centre']
        w_m, h_m = p['size']
        f = p['feather_m']
        pad = 4.0 * f + 200.0
        c0 = int(max(0, (cx - w_m / 2 - pad - x0) / WORK_DX))
        c1 = int(min(z.shape[1] - 1, (cx + w_m / 2 + pad - x0) / WORK_DX + 1))
        r0 = int(max(0, (cy - h_m / 2 - pad - y0) / WORK_DX))
        r1 = int(min(z.shape[0] - 1, (cy + h_m / 2 + pad - y0) / WORK_DX + 1))
        sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
        XX, YY = X[sl], Y[sl]

        phi = ops.rect_sdf(XX, YY, cx - w_m / 2, cy - h_m / 2, cx + w_m / 2, cy + h_m / 2)
        core = phi <= 0.0
        zbar = float(z[sl][core].mean()) if core.any() else float(z[sl].mean())

        ci = int((cy - y0) / WORK_DX), int((cx - x0) / WORK_DX)
        ux, uy = float(gx[ci]), float(gy[ci])
        n = math.hypot(ux, uy) or 1.0
        ux, uy = ux / n, uy / n
        # A true plane, not a ramp that flattens off: clipping the projection leaves a
        # surface no plane fits, which reads as a residual the flatness check cannot
        # tell apart from a bumpy yard. On a long pad the fall is what limits the
        # gradient instead.
        span = abs(w_m * ux) + abs(h_m * uy)
        gd = min(p['drain_grade'], p['max_drop_m'] / max(span, 1.0))
        proj = (XX - cx) * ux + (YY - cy) * uy
        z_tgt = (zbar - gd * proj).astype(np.float32)

        cur = z[sl]
        feather = np.maximum(f, 1.5 * np.abs(z_tgt - cur)
                             / math.tan(math.radians(5.0)))
        w = np.where(phi <= 0.0, 1.0, 1.0 - ops.smoothstep(phi / feather))
        z[sl] = (1.0 - w) * cur + w * z_tgt
        built[sl] = np.maximum(built[sl], w)
    return z, built


# ==================================================================================
# output
# ==================================================================================
def apply_rim_mountains(z, X, Y):
    """Raise the non-playable border into mountains.

    Added last, on top of the finished terrain, and by addition rather than by blending:
    everything already built in the border - the roads leaving the map, the river valley
    running out of it - keeps its own shape and is carried up the flank with the ground,
    instead of being smeared out by a second surface fighting the first.

    The lift is driven by the distance outside the playable square, so the band has the
    same width along every side. It is exactly zero inside, which is what keeps the
    playable area and the first RIM_APRON_M the player can see past its edge untouched.

    The distance is a 4-norm rather than a plain maximum: the maximum is what a square
    ring is, but its gradient turns a corner along the diagonal and the ramp creases
    there - four straight seams running out of the corners of the map, plain to see
    under a hillshade. The 4-norm rounds the corner and is the perpendicular distance
    everywhere along a side, which is the only place it has to be exact.
    """
    dx = np.maximum(np.maximum(-X, X - PLAYABLE_M), 0.0)
    dy = np.maximum(np.maximum(-Y, Y - PLAYABLE_M), 0.0)
    d = (dx ** 4 + dy ** 4) ** 0.25
    t = ops.smoothstep((d - RIM_APRON_M) / RIM_RISE_M)
    if not np.any(t > 0.0):
        return z

    Xc, Yc = X + OFFSET_M, Y + OFFSET_M
    ridge = ops.value_noise(Xc, Yc, RIM_RIDGE_LAM_M, rng_for('rim_ridge'), CANVAS_M)
    spur = ops.value_noise(Xc, Yc, RIM_SPUR_LAM_M, rng_for('rim_spur'), CANVAS_M)
    detail = ops.value_noise(Xc, Yc, RIM_DETAIL_LAM_M, rng_for('rim_detail'), CANVAS_M)

    # Summits and saddles along the rim, spurs across it, texture on the flanks. Every
    # term goes into the shape factor before it is clipped at 1, so RIM_HEIGHT_M is a
    # ceiling the tallest summit reaches rather than a number the noise overshoots by
    # however much the last octave happened to add.
    shape = RIM_LOW + (1.0 - RIM_LOW) * ops.smoothstep(0.5 + 0.42 * ridge) \
        + 0.11 * spur + (RIM_DETAIL_M / RIM_HEIGHT_M) * detail
    h = RIM_HEIGHT_M * t * np.clip(shape, 0.10, 1.0)

    # The river cuts the rim rather than climbing it. Held off its own valley and
    # feathered back up, what the lift leaves behind is the valley itself, with 200 m of
    # mountain either side of it: the gorge the water runs out through.
    d_river, _ = ops.polyline_field(
        X, Y, ml.river_axis(), RIM_GORGE_HALF_W_M + RIM_GORGE_FEATHER_M + 100.0)
    h *= ops.smoothstep((d_river - RIM_GORGE_HALF_W_M) / RIM_GORGE_FEATHER_M)
    return z + h


def write_stats(z, X, Y, path):
    """A coarse height and roughness grid for the OSM generator.

    The parcelling wants smaller fields on broken ground. Rather than have it re-derive
    the terrain (two implementations of one landscape, guaranteed to drift) or pull a
    150 megapixel PNG through numpy in a standard-library-only folder, the DEM publishes
    what it already knows, in JSON.
    """
    p0 = int(OFFSET_M / WORK_DX)
    p1 = int((OFFSET_M + PLAYABLE_M) / WORK_DX)
    play = z[p0:p1, p0:p1]
    slope = np.tan(np.radians(ops.slope_deg(play, WORK_DX, baseline_m=40.0)))
    k = play.shape[0] // STATS_GRID
    hgt = play.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    slp = slope.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    rough = np.clip(slp / 0.030, 0.0, 1.0)
    with open(path, 'w') as fh:
        json.dump({'n': STATS_GRID, 'cell_m': PLAYABLE_M / STATS_GRID,
                   'origin': [0.0, 0.0],
                   'height': [round(float(v), 2) for v in hgt.ravel()],
                   'roughness': [round(float(v), 4) for v in rough.ravel()]}, fh)


def write_dem(z_work, built_work, out_path):
    """Resample to 1 m and quantise, one band at a time.

    Never materialises a full-resolution float array: the output is a preallocated uint16
    and each band is 1024 rows. Peak memory is about half a gigabyte instead of five.
    """
    n = CANVAS_M
    out = np.empty((n, n), dtype=np.uint16)
    cols = (np.arange(n, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX
    rng_micro = rng_for('micro')
    rng_dither = rng_for('dither')
    lattice_n = int(CANVAS_M / MICRO_LAM_M) + 4
    lattice = rng_micro.standard_normal((lattice_n, lattice_n)).astype(np.float32)
    lattice /= lattice.std()

    for b in range(n // BAND_ROWS):
        r0, r1 = b * BAND_ROWS, (b + 1) * BAND_ROWS
        rows = (np.arange(r0, r1, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX
        coords = np.stack(np.broadcast_arrays(rows[:, None], cols[None, :]))
        band = ndimage.map_coordinates(z_work, coords, order=3, mode='nearest',
                                       output=np.float32)
        blt = ndimage.map_coordinates(built_work, coords, order=1, mode='nearest',
                                      output=np.float32)
        mcoords = np.stack(np.broadcast_arrays(
            (np.arange(r0, r1, dtype=np.float32) / MICRO_LAM_M)[:, None],
            (np.arange(n, dtype=np.float32) / MICRO_LAM_M)[None, :]))
        micro = ndimage.map_coordinates(lattice, mcoords, order=3, mode='grid-wrap',
                                        output=np.float32)
        band += MICRO_AMP_M * micro * (1.0 - 0.85 * np.clip(blt, 0.0, 1.0))
        band *= 100.0
        # triangular dither: decorrelates the rounding error from the surface, so a flat
        # yard shows grain instead of contour bands
        band += DITHER_CM * (rng_dither.random(band.shape, dtype=np.float32)
                             + rng_dither.random(band.shape, dtype=np.float32) - 1.0)
        np.clip(band, 0.0, Z_MAX_CM, out=band)
        out[r0:r1] = np.rint(band).astype(np.uint16)
    Image.fromarray(out).save(out_path)
    return out


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


def draw_figures(raw, out_vis, out_detail):
    n = CANVAS_M
    k = n // 1024
    vis = raw.reshape(1024, k, 1024, k).mean(axis=(1, 3)) / 100.0
    vmin, vmax = np.percentile(vis, 0.5), np.percentile(vis, 99.5)

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
    xs = np.linspace(0, PLAYABLE_M, sub.shape[1])
    ax.contour(xs, xs, sub, levels=np.arange(np.floor(sub.min()), sub.max(), 2.0),
               colors='white', linewidths=0.4, alpha=0.25)
    # the layout on top: if the terrain and the vectors disagree, it shows here
    for c in ml.corridors():
        if c['kind'] in ('track', 'street'):
            continue
        xs2 = [p[0] for p in c['axis']]
        ys2 = [p[1] for p in c['axis']]
        ax.plot(xs2, ys2, color=('#F59E0B' if c['kind'] == 'rail' else '#E5E7EB'),
                lw=(1.6 if c['kind'] == 'rail' else 0.9),
                ls=('--' if c['kind'] == 'rail' else '-'), alpha=0.85)
    riv = ml.river_axis()
    ax.plot([p[0] for p in riv], [p[1] for p in riv], color='#38BDF8', lw=1.8)
    lake = ml.lake_ring()
    ax.fill([p[0] for p in lake], [p[1] for p in lake], color='#0284C7', alpha=0.75)
    for p in ml.pads():
        r = p['ring']
        ax.plot([q[0] for q in r], [q[1] for q in r],
                color=('#6366F1' if p['kind'] == 'village' else '#DB2777'), lw=1.2)
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
    print(f"=== FS25 Iowa DEM generator ({CANVAS_M}x{CANVAS_M} m canvas, "
          f"{PLAYABLE_M} m playable) ===")
    problems = ml.validate()
    if problems:
        print("!! layout problems:")
        for p in problems:
            print("   -", p)
        return 1
    print("   ", ml.summary())

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dem = os.path.join(script_dir, "dem_new_12k.png")
    out_vis = os.path.join(script_dir, "dem_new_visual_12k.png")
    out_detail = os.path.join(script_dir, "dem_new_visual_detail_12k.png")
    out_stats = os.path.join(script_dir, "terrain_stats.json")

    ax_ = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)
    X, Y = np.meshgrid(ax_, ax_)
    p0 = int(OFFSET_M / WORK_DX)
    p1 = int((OFFSET_M + PLAYABLE_M) / WORK_DX)

    print("1. Landscape: regional fall, warped fBm, tillage, potholes...")
    z = build_landscape(X, Y)
    river_d, _ = ops.polyline_field(X, Y, ml.river_axis(), 1200.0)
    w_field = ops.smoothstep((river_d - 500.0) / 500.0)
    z = apply_tillage(z, w_field)
    z = apply_potholes(z, X, Y)

    # Amplitudes fixed by hand do not hit a relief target: the layers add up. Measure
    # what came out and scale it, so the brief's number is met by construction.
    play = z[p0:p1, p0:p1]
    relief = float(play.max() - play.min())
    scale = LANDSCAPE_RELIEF_M / relief
    z = (z - float(play.mean())) * scale
    print(f"   landscape relief {relief:.1f} m -> scaled by {scale:.3f} "
          f"to {LANDSCAPE_RELIEF_M:.1f} m")

    print("2. Riparian roughness...")
    w_rough = ops.smoothstep((900.0 - river_d) / 650.0)
    z += w_rough * (0.50 * ops.value_noise(X + OFFSET_M, Y + OFFSET_M, 90.0,
                                           rng_for('rough_90'), CANVAS_M)
                    + 0.25 * ops.value_noise(X + OFFSET_M, Y + OFFSET_M, 55.0,
                                             rng_for('rough_55'), CANVAS_M))

    print("3. River valley...")
    z, (river_pts, s_prof, z_prof), river_d = carve_river(z, X, Y)
    fall = float(z_prof[0] - z_prof[-1])
    print(f"   thalweg falls {fall:.1f} m over {s_prof[-1] / 1000:.1f} km "
          f"({fall / (s_prof[-1] / 1000):.2f} m/km)")

    print("4. Tributary and lake...")
    z = carve_creek(z, X, Y)
    z, z_lake = carve_lake(z, X, Y, river_pts, s_prof, z_prof)
    print(f"   lake surface {z_lake:.2f} m, {ml.ring_area_ha(ml.lake_ring()):.1f} ha, "
          f"{ml.LAKE['max_depth']:.0f} m deep, on the main stem")

    print("5. Slope limiting...")
    z = ops.limit_slope(z, WORK_DX, SLOPE_LIMIT_DEG)

    print("6. Crossing plateaus, corridors, yards...")
    z = apply_crossing_plateaus(z, X, Y)

    # Yards before roads. The other way round the pad overwrites the road platform
    # inside it and leaves a step at its edge - which is exactly where the ruling grade
    # blew out when this was ordered the obvious way.
    z, built = apply_pads(z, X, Y)

    order = {'rail': 0, 'primary': 1, 'section': 2, 'track': 3, 'street': 4}
    pins = {}
    claimed = np.zeros(z.shape, np.float32)
    for c in sorted(ml.corridors(), key=lambda c: order[c['kind']]):
        my_pins = pins.get(c['id'], [])
        axis, s, prof = corridor_profile(z, X, Y, c, my_pins)
        z = apply_corridor(z, X, Y, c, s, prof, claimed)
        if c['kind'] == 'rail':
            for k in ml.crossings():
                if k['corridor_ids'][1] != c['id']:
                    continue
                i = int(np.argmin([math.dist(k['xy'], p) for p in axis]))
                pins.setdefault(k['corridor_ids'][0], []).append((k['xy'], float(prof[i])))

    # Road and rail platforms get the same treatment as the yards: no surface texture on
    # a running surface. Left in, the 4 cm micro-relief is 8 cm over the 25 m the ruling
    # grade is measured across, which is a quarter of the railway's entire budget.
    built = np.maximum(built, claimed)

    # The channel is smoothed the same way a platform is: at 25 m sampling the surface
    # texture is four times the fall between samples, which is enough to make the bed
    # read as rising in places even though the profile only ever descends.
    r_lake_w = ops.ellipse_r(X, Y, ml.lake_centre()[0], ml.lake_centre()[1],
                             ml.LAKE['semi_a'], ml.LAKE['semi_b'], ml.lake_rot_deg(),
                             ml.LAKE_SHORE)
    built = np.maximum(built, 1.0 - ops.smoothstep(
        (river_d - ml.RIVER['bed_half_w']) / 45.0))
    built = np.maximum(built, 1.0 - ops.smoothstep((r_lake_w - 0.98) / 0.12))

    print("7. Finishing...")
    z = ndimage.gaussian_filter(z, FINAL_SMOOTH_PX)
    # The datum is anchored on the land. With a 40 m basin in the map the lowest tenth
    # of a percent of the playable area is all lake bottom, and anchoring on that would
    # push the whole landscape 40 m into the air.
    play = z[p0:p1, p0:p1]
    r_play = r_lake_w[p0:p1, p0:p1]
    land = play[r_play > 1.02]
    z = z + (DATUM_P01_M - float(np.percentile(land, 0.1)))

    # The stats grid covers the playable area only, so the rim cannot reach it either
    # way; writing it first keeps that obvious.
    write_stats(z, X, Y, out_stats)
    print(f"   {os.path.basename(out_stats)}")

    print(f"8. Rim mountains: {RIM_APRON_M:.0f} m of apron, then up to "
          f"{RIM_HEIGHT_M:.0f} m...")
    z = apply_rim_mountains(z, X, Y)
    p_apron = int((OFFSET_M - RIM_APRON_M) / WORK_DX)
    print(f"   rim peaks at {float(z.max()):.1f} m, apron at most "
          f"{float(z[p_apron:-p_apron, p_apron:-p_apron].max()):.1f} m")

    print(f"9. Writing '{os.path.basename(out_dem)}'...")
    raw = write_dem(z, built, out_dem)
    play_cm = raw[OFFSET_M:OFFSET_M + PLAYABLE_M, OFFSET_M:OFFSET_M + PLAYABLE_M]
    print(f"   canvas   {raw.min() / 100:.2f} .. {raw.max() / 100:.2f} m")
    print(f"   playable {play_cm.min() / 100:.2f} .. {play_cm.max() / 100:.2f} m "
          f"(relief {(int(play_cm.max()) - int(play_cm.min())) / 100:.1f} m)")

    print("10. Visualisations...")
    draw_figures(raw, out_vis, out_detail)
    print(f"   {out_vis}\n   {out_detail}")

    print(f"\n=== Done in {time.time() - t_start:.1f} s "
          f"(seed {MASTER_SEED}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
