#!/usr/bin/env python3
"""Acceptance report for the generated heightmap.

Checks the things that are easy to break and hard to see in the image: that the canvas is
the size and encoding the rest of the project expects, that nothing touches the floor or
the ceiling of the 16-bit range, that the playable square really is the datum rather than
nearly it, that the valley rim in the border is the valley the brief asked for - two
ranges standing 200 to 250 m over a floor at 20 m, a sill low enough at either end that
the valley runs through rather than being walled in - and that `terrain_stats.json`,
which the OSM side reads instead of re-deriving the terrain, describes the same surface
the PNG does.

The rim is the interesting half, and every number it is judged on is read back out of the
PNG. Only the *masks* come from the layout, through `terrain_ops.rim_field` - the same
call the generator shaped the rim with - because a second opinion about where the west
range is would be a report that passes a rim which is not the one that got built.

Exits non-zero if any check fails, so it can gate the pipeline.

One measurement note that outlives the blank map: slope is measured over a 5 m baseline.
A DEM quantised to the centimetre at one metre a pixel has a pure noise floor near 0.3
degrees in its per-pixel gradient, so measuring pixel to pixel overstates every slope on
the map. And dry land starts one baseline back from any water's edge - a 5 m window
straddling the lip reads the submerged bank off a pixel that is itself dry.
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402
import terrain_ops as ops                                           # noqa: E402
from generate_new_dem_12k import (CANVAS_M, PLAYABLE_M, OFFSET_M,    # noqa: E402
                                  BASE_ELEV_M, Z_MAX_CM, STATS_GRID,
                                  WORK_PX, WORK_DX, FEATHER_CAP_M, BANK_DEG,
                                  water_fields, build_base, rim_ramp)
from scipy import ndimage                                            # noqa: E402

SLOPE_BASELINE_M = 5.0
# The steepest dry ground the playable square is allowed. Not the valley side's own
# 4.9 deg: where the lake's valley and the river's merge, both sections fall the same way
# at once and the shoulder between them reaches 20%. That is a landform - a tributary
# valley meeting a basin - and the profile through it is smooth, which is the difference
# between a steep place and a defect. 12.5 deg is still ground a tractor works.
VALLEY_SLOPE_MAX_DEG = 12.5
BAND_ROWS = 1024
RIM_BAND_ROWS = 256           # `rim_field` holds a dozen arrays at once; 1024 rows of
                              # them is half a gigabyte, and none of this is in a hurry
FLAT_TOL_CM = 0.5             # half a centimetre: the quantisation step is one

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    return ok


def info(name, detail):
    print(f"         {name}   {detail}")


def band(name, value, lo, hi, unit=""):
    return check(name, lo <= value <= hi,
                 f"{value:.2f}{unit} (want {lo:g}..{hi:.1f}{unit})")


def max_slope_deg(raw, mask=None):
    """The steepest 5 m slope anywhere on the canvas, measured band by band.

    Banded because a float copy of the whole canvas is 600 MB and the Gaussian behind
    `slope_deg` wants another. The bands overlap by four baselines so no slope is missed
    across a seam, and the blur has settled well inside the overlap.

    `mask` restricts where the answer is *read* while still measuring on the whole
    surface, which is the only honest way to ask about dry land: a 5 m window that
    straddles the water's edge reads the submerged bank off a pixel that is itself dry,
    and reported the inside of a channel as a 15 degree field.
    """
    pad = int(4 * SLOPE_BASELINE_M)
    worst = 0.0
    for r0 in range(0, raw.shape[0], BAND_ROWS):
        a = max(0, r0 - pad)
        b = min(raw.shape[0], r0 + BAND_ROWS + pad)
        z = raw[a:b].astype(np.float32) / 100.0
        s = ops.slope_deg(z, 1.0, baseline_m=SLOPE_BASELINE_M)[r0 - a:r0 - a + BAND_ROWS]
        if mask is not None:
            m = mask[r0:r0 + s.shape[0]]
            s = s[m] if m.any() else s[:0]
        if s.size:
            worst = max(worst, float(s.max()))
    return worst


def water_masks():
    """The zone masks, from the same call the generator shaped the water with.

    `water_fields` on the 4 m synthesis grid, upsampled: identical by construction to
    what got built, which is the whole point - a second opinion about where the valley is
    would be a report that passes a heightmap that does not meet the brief. 4 m is plenty
    to answer "is this pixel in the valley", and the one place it is not - where dry land
    starts - is handled by growing the wet mask a baseline and a half instead of trusting
    its edge.

    Returns `(valley, wet, dry, d_river)` on the full canvas, plus the till plain on
    the working grid - the ground everything under the uplands is quoted against, which
    the valley and apron checks need at arbitrary points.
    """
    ax = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)
    X, Y = np.meshgrid(ax, ax)
    land_w = build_base(X, Y)
    wet_w, water_z_w, d_river_w = water_fields(X, Y, land_w)
    k = CANVAS_M // WORK_PX

    def up(a):
        return np.repeat(np.repeat(a, k, axis=0), k, axis=1)

    # Grown by four coarse cells - sixteen metres, the support of the cubic kernel that
    # resamples the 4 m synthesis grid to 1 m. On the synthesis grid the floodplain is
    # exactly 75.000000 right up to the valley rim; the spline that interpolates between
    # those samples rings against the curvature there and leaves about 8 cm of swell
    # either side of it. Sixteen metres is how far that kernel can reach, so it is the
    # honest width of "not floodplain any more", and it is a bound rather than a number
    # tuned until the check went green.
    valley = up(ndimage.binary_dilation(water_z_w < land_w - 0.01, iterations=4))
    wet = up(wet_w > 0.5)
    # One 5 m baseline back from the water, rounded up to the 8 m the coarse grid can
    # resolve: dry land begins where a slope window cannot see under the waterline.
    dry = ~up(ndimage.binary_dilation(wet_w > 0.5, iterations=2))
    return valley, wet, dry, up(d_river_w), land_w


def zat(raw, x, y):
    """Height in metres at a playable-metre coordinate."""
    c = np.clip(np.rint(np.asarray(x) + OFFSET_M).astype(int), 0, CANVAS_M - 1)
    r = np.clip(np.rint(np.asarray(y) + OFFSET_M).astype(int), 0, CANVAS_M - 1)
    return raw[r, c] / 100.0


def river_stations():
    """Arc length, point, unit normal and water-surface height along the river axis,
    every 40 m, with the stretch inside the lake dropped.

    The lake is dropped because a lake is not a channel: its bed is 38 m under a flat
    sheet and its surface does not fall, so every question this file asks about a
    channel - how deep is the trough, where is the bank, is it still going downhill -
    has no meaning across it.
    """
    axis = ml.river_axis()
    s_in, s_out, grade, length = ml.river_profile()
    ring = ml.lake_ring()
    # The axis runs EXTEND_M past the canvas so it does not end at a cliff, and there is
    # no ground out there to read. Sampling it anyway is what made the river appear to
    # run 2.35 m uphill: the clamp in `zat` folded every one of those stations onto the
    # canvas edge, and a bed sampled off the sill beside it is not a bed.
    lo, hi = -ml.OFFSET_M + 5.0, ml.PLAYABLE_M + ml.OFFSET_M - 5.0
    out = []
    acc = 0.0
    for i, p in enumerate(axis):
        if i:
            acc += math.dist(axis[i - 1], p)
        if not (lo <= p[1] <= hi):
            continue
        if s_in <= acc <= s_out or ml.point_in_ring(p, ring):
            continue
        a, b = axis[max(0, i - 1)], axis[min(len(axis) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = math.hypot(dx, dy) or 1.0
        ws = (ml.LAKE_WS_M + grade * (s_in - acc) if acc < s_in
              else ml.LAKE_WS_M - grade * (acc - s_out))
        out.append((acc, p, (-dy / ll, dx / ll), ws))
    return out


def rim_profile(raw, valley, land_band):
    """Read the rim back off the PNG, band by band.

    Returns the crest line of each of the four rims - the highest ground in the shoulder
    strip, per row for the two ranges and per column for the two sills - and the range
    the ground covers on the flat: inside the playable square and across the apron beyond
    it, which the rim is not allowed to have touched.

    The strips come from `terrain_ops.rim_field`, the call the generator built the rim
    with, so "the west range" means here exactly what it meant there. `t >= 1` is the
    crest shoulder, `w` says which way the rim faces, and `t <= 0` is the flat.
    """
    n = CANVAS_M
    ax = ops.work_axis(n, 1.0, OFFSET_M)          # playable metre of each pixel centre
    none = -1e9
    crest = {'west': np.full(n, none), 'east': np.full(n, none),
             'north': np.full(n, none), 'south': np.full(n, none)}
    near = {'north': np.full(n, 1e9), 'south': np.full(n, 1e9)}
    # The ground the rim stands on, taken in the apron strip on the same line. The rim is
    # added by addition, so a summit over high ground stands that much higher and its
    # absolute height says as much about the till plain as about the rim. Its *lift* is
    # the thing the constants set, and this is what makes the lift measurable.
    a0, a1 = int(OFFSET_M - ml.RIM_APRON_M), int(OFFSET_M)
    b0, b1 = int(OFFSET_M + PLAYABLE_M), int(OFFSET_M + PLAYABLE_M + ml.RIM_APRON_M)
    foot = {'west': np.median(raw[:, a0:a1], axis=1) / 100.0,
            'east': np.median(raw[:, b0:b1], axis=1) / 100.0,
            'north': np.median(raw[a0:a1, :], axis=0) / 100.0,
            'south': np.median(raw[b0:b1, :], axis=0) / 100.0}
    flat_lo, flat_hi = 1e9, -1e9
    axis = ml.river_axis()
    notch_reach = 2.0 * (ml.RIVER_NOTCH_HALF_M + ml.RIVER_NOTCH_FEATHER_M)
    for r0 in range(0, n, RIM_BAND_ROWS):
        r1 = min(n, r0 + RIM_BAND_ROWS)
        X, Y = np.meshgrid(ax, ax[r0:r1])
        t, w = rim_ramp(X, Y)
        # Only the two sill strips and the apron need to know where the river is, and
        # the distance transform over a 322-segment axis with a two-kilometre window is
        # the most expensive thing in this file by an order of magnitude. Everywhere else
        # the rim is a long way from the water and the answer cannot matter.
        want = bool(((t >= 1.0) & (w <= 0.01)).any())
        d_river = (ops.polyline_field(X, Y, axis, notch_reach)[0] if want
                   else np.full(X.shape, np.float32(1e9)))
        z = raw[r0:r1].astype(np.float32) / 100.0
        flat = (t <= 0.0) & ~valley[r0:r1] & (np.abs(Y) < 1e9)
        flat &= ((X < 0.0) | (X > ml.PLAYABLE_M)
                 | (Y < 0.0) | (Y > ml.PLAYABLE_M))     # the apron, not the playable
        if flat.any():
            off = z[flat] - land_band(X[flat], Y[flat])
            flat_lo = min(flat_lo, float(off.min()))
            flat_hi = max(flat_hi, float(off.max()))
        shoulder = t >= 1.0
        rng_m, sill_m = shoulder & (w >= 0.99), shoulder & (w <= 0.01)
        for key, m in (('west', rng_m & (X < 0.0)),
                       ('east', rng_m & (X > ml.PLAYABLE_M))):
            crest[key][r0:r1] = np.where(m, z, none).max(axis=1)
        for key, m in (('north', sill_m & (Y < 0.0)),
                       ('south', sill_m & (Y > ml.PLAYABLE_M))):
            np.maximum(crest[key], np.where(m, z, none).max(axis=0), out=crest[key])
            np.minimum(near[key], np.where(m, d_river, 1e9).min(axis=0),
                       out=near[key])
    live = {k: v > none / 2.0 for k, v in crest.items()}
    foot = {k: v[live[k]] for k, v in foot.items()}
    # The feather counts as notch: the rim is only back to full height RIVER_NOTCH_HALF_M
    # plus RIVER_NOTCH_FEATHER_M out, and columns inside that are a shoulder coming down
    # to the river, not a sill that has failed to reach its height.
    reach = ml.RIVER_NOTCH_HALF_M + ml.RIVER_NOTCH_FEATHER_M
    notch = {k: near[k][live[k]] > reach for k in near}
    return ({k: v[live[k]] for k, v in crest.items()}, notch, foot, flat_lo, flat_hi)


def road_stations(c, raw):
    """Every 20 m along a road inside the playable square, off the decks.

    Both restrictions are the measurement frame and not the road. The alignment runs 2 km
    out into the border, where the rim is added on top of it by addition and carries it
    170 m up the flank - measure there and every section road reports a 13% ruling grade
    that belongs to a mountain. And a station on a deck reads the riverbed through the
    bridge, which is the other half of the same mistake.
    """
    dense = ml.densify(c['axis'], 20.0)
    arc = ops.polyline_arclen(dense)
    spans = list(c.get('bridge_spans', ()))
    keep = [(a, p) for a, p in zip(arc, dense)
            if 0.0 <= p[0] <= ml.PLAYABLE_M and 0.0 <= p[1] <= ml.PLAYABLE_M
            and not any(s0 - 25.0 <= a <= s1 + 25.0 for s0, s1 in spans)]
    pts = [q[1] for q in keep]
    return (np.array([q[0] for q in keep]),
            np.array([float(zat(raw, p[0], p[1])) for p in pts]), pts)


def built_mask(shape):
    """Everything a graded platform and its feather can reach, on the canvas.

    The roads and the town platforms are ground now, so the checks that ask what the
    *landscape* is doing have to be able to leave them out: an embankment is not the till
    plain failing to be flat, and a cutting through the apron is not the rim leaking
    inwards.

    Every alignment on this map is axis-aligned, so the reach of one is its own bounding
    box grown by the reach - which has to be the box and not an infinite stripe. Taken as
    a stripe, a 270 m town street masked the full 12 km of canvas it happens to be
    parallel to, and eight of them per town would have written off a quarter of the
    uplands as road.

    The reach is more than the nominal feather, because the generator widens a feather to
    `1.5*|dz|/tan(4 deg)` wherever the cut is deep, and the ring a mask sized at the
    nominal misses is exactly the steepest ground the platform made. A corridor gets
    three nominal feathers: its profile is fitted to the ground it crosses, so its cut
    stays small and 42 m covers it. A pad gets the generator's own cap, because its
    platform does *not* follow the ground - it is a plane, and how far it stands off the
    till plain at the far end of a 910 m town is a property of the till plain, not of
    anything in the layout. `FEATHER_CAP_M` is the only honest bound on it.
    """
    m = np.zeros(shape, dtype=bool)
    xs = np.arange(shape[1], dtype=np.float32) + 0.5 - OFFSET_M
    ys = np.arange(shape[0], dtype=np.float32) + 0.5 - OFFSET_M

    def box(x0, y0, x1, y1):
        return (((xs >= x0) & (xs <= x1))[None, :]
                & ((ys >= y0) & (ys <= y1))[:, None])

    for c in ml.corridors():
        ax = c['axis']
        reach = c['half_width_m'] + 3.0 * c['feather_m']
        m |= box(min(p[0] for p in ax) - reach, min(p[1] for p in ax) - reach,
                 max(p[0] for p in ax) + reach, max(p[1] for p in ax) + reach)
    for p in ml.pads():
        cx, cy = p['centre']
        w, h = p['size']
        m |= box(cx - w / 2.0 - FEATHER_CAP_M, cy - h / 2.0 - FEATHER_CAP_M,
                 cx + w / 2.0 + FEATHER_CAP_M, cy + h / 2.0 + FEATHER_CAP_M)
    return m


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dem_path = os.path.join(script_dir, "dem_new_12k.png")
    stats_path = os.path.join(script_dir, "terrain_stats.json")
    if not os.path.exists(dem_path):
        print(f"Error: {dem_path} not found. Run generate_new_dem_12k.py first.")
        return 2

    img = Image.open(dem_path)
    raw = np.array(img)
    o = int(OFFSET_M)
    play_raw = raw[o:o + PLAYABLE_M, o:o + PLAYABLE_M]
    lo_cm, hi_cm = float(raw.min()), float(raw.max())
    want_cm = BASE_ELEV_M * 100.0

    print(f"=== Elevation report: {os.path.basename(dem_path)} ===")
    print(f"layout   {ml.summary()}")
    print(f"canvas   {raw.shape[1]}x{raw.shape[0]} px   "
          f"{lo_cm / 100.0:7.2f} .. {hi_cm / 100.0:7.2f} m")
    print(f"playable {play_raw.shape[1]}x{play_raw.shape[0]} m      "
          f"{play_raw.min() / 100.0:7.2f} .. {play_raw.max() / 100.0:7.2f} m   "
          f"(relief {(float(play_raw.max()) - float(play_raw.min())) / 100.0:.2f} m)")

    # ---------------------------------------------------------------- geometry
    print("\ngeometry and encoding:")
    check(f"canvas is {CANVAS_M}x{CANVAS_M} px", raw.shape == (CANVAS_M, CANVAS_M),
          f"got {raw.shape[1]}x{raw.shape[0]}")
    check("playable area is centred", int(OFFSET_M) * 2 + PLAYABLE_M == CANVAS_M,
          f"{OFFSET_M:.0f} m of margin on every side")
    check("16-bit integer image", raw.dtype == np.uint16,
          f"dtype {raw.dtype}, PIL mode {img.mode!r}")
    check("under the 16-bit ceiling", hi_cm <= min(65535.0, Z_MAX_CM),
          f"peak {hi_cm:.0f} cm, ceiling {min(65535.0, Z_MAX_CM):.0f} cm")
    check("no ground at zero", lo_cm > 0.0, f"floor {lo_cm:.0f} cm")
    info("scale", "raw / 100 = metres, which is what Giants Editor imports")

    valley, wet, dry, d_river, land_w = water_masks()
    # Two masks, not one. `valley` is the water's valley and nothing else; `natural` is
    # everything that is not the till plain doing its own thing - the valley plus every
    # road platform and levelled yard on the map.
    #
    # They were one mask, `valley |= built`, and it was wrong in a way that got quietly
    # worse with every yard added: the roughness report asks whether the valley reads as
    # broken ground *against* the uplands, and folding a hundred hectares of dead-flat
    # platform into "the valley" drags that average toward the flats until the check
    # stops separating anything. It had already fallen from 0.350 to 0.299 against a
    # 0.310 floor. A check that a feature can turn green by being added to the map is not
    # a check. Every question about the *landscape* takes `natural`; every question about
    # the *water's valley* takes `valley`.
    built = built_mask(raw.shape)
    natural = valley | built
    wax = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)

    def land_at(xs, ys):
        """The till plain at arbitrary playable metres, from the same call that built
        it. Sampled rather than re-derived: two answers to "how high is the upland
        here" is how a report comes to pass a heightmap that misses the brief."""
        return ops.sample_bilinear(land_w, float(wax[0]), float(wax[0]),
                                   WORK_DX, WORK_DX, xs, ys)

    crest, notch, foot, flat_lo, flat_hi = rim_profile(raw, natural, land_at)
    play = (slice(o, o + PLAYABLE_M), slice(o, o + PLAYABLE_M))

    # ---------------------------------------------------------------- the till plain
    # Des Moines Lobe ground: gentle, aimless, and low. These are checks of character
    # rather than of any one height - that the relief is the order of the real place,
    # that its mean is still the datum the water hangs off, and that nothing on it is
    # steep enough to be a hill or low enough for the river to flood.
    upl = play_raw[~natural[play]] / 100.0
    print(f"\nthe till plain (mean {BASE_ELEV_M:.0f} m, after Royal, Clay County, "
          "Iowa):")
    print(f"         uplands   {upl.min():.2f} .. {upl.max():.2f} m, mean "
          f"{upl.mean():.2f} m, sd {upl.std():.2f} m")
    band("relief across the uplands", float(upl.max() - upl.min()), 14.0, 28.0, " m")
    band("the mean upland sits on the datum", float(upl.mean()) - BASE_ELEV_M,
         -1.5, 1.5, " m")
    bank_top = ml.LAKE_WS_M + ml.WATER_BANK_M
    check("the lowest upland still stands over the bank top",
          float(upl.min()) > bank_top + 2.0,
          f"{float(upl.min()) - bank_top:.1f} m of freeboard over the {bank_top:.0f} m "
          "bank - under that the river comes out of its valley into the low ground")
    inv = float(valley[play].mean())
    info("how much of the map the water's valley takes", f"{inv * 100:.1f}%")

    # The apron is the whole point of the rim starting where it starts: a mountain that
    # began at the boundary would put its own toe inside the playable square, and the
    # last ground on the map would be a hillside. The river's own valley crosses it,
    # and is not the rim's doing.
    # The apron is the whole point of the rim starting where it starts: a mountain that
    # began at the boundary would put its toe inside the playable square. With relief
    # under it the question is no longer "is it 75.00" but "is it still the till plain",
    # and the only way to tell a rim that has leaked inwards from ground that is simply
    # high is to measure against the surface the till plain was built from.
    check(f"the rim adds nothing to the {ml.RIM_APRON_M:.0f} m apron past the boundary",
          max(abs(flat_hi), abs(flat_lo)) <= 0.10,
          f"the apron stands {flat_lo:+.2f} .. {flat_hi:+.2f} m off the till plain it "
          "is cut from")
    info("headroom", f"{float(lo_cm) / 100.0:.0f} m under the deepest water before the "
                     f"encoding clips at zero, "
                     f"{min(65535.0, Z_MAX_CM) / 100.0 - float(hi_cm) / 100.0:.0f} m of "
                     "fill left over the summits")

    # ---------------------------------------------------------------- the river
    # A channel that holds water has its bed under its own waterline, and the profile the
    # DEM carries along it is the water surface. Every check here is against that
    # waterline rather than against the ground, which is the distinction the whole wet
    # mask exists to keep straight.
    st = river_stations()
    hw, bank_m = ml.RIVER_HALF_W_M, ml.WATER_BANK_M
    beds = np.array([zat(raw, p[0], p[1]) for _, p, _, _ in st])
    wss = np.array([w for _, _, _, w in st])
    lips = np.array([max(zat(raw, p[0] + n[0] * (hw + ml.BANK_RUN_M),
                             p[1] + n[1] * (hw + ml.BANK_RUN_M)),
                         zat(raw, p[0] - n[0] * (hw + ml.BANK_RUN_M),
                             p[1] - n[1] * (hw + ml.BANK_RUN_M))) for _, p, n, _ in st])
    edges = np.array([0.5 * (zat(raw, p[0] + n[0] * hw, p[1] + n[1] * hw)
                             + zat(raw, p[0] - n[0] * hw, p[1] - n[1] * hw))
                      for _, p, n, _ in st])
    cut = lips - beds
    print(f"\nthe river ({2 * hw:.0f} m wide, cut "
          f"{ml.WATER_BANK_M + ml.RIVER_DEPTH_M:.0f} m into its valley floor):")
    band("the bed sits under the waterline by", float((wss - beds).mean()),
         ml.RIVER_DEPTH_M - 0.25, ml.RIVER_DEPTH_M + 0.25, " m")
    band("the trough is cut", float(cut.mean()),
         ml.WATER_BANK_M + ml.RIVER_DEPTH_M - 0.4,
         ml.WATER_BANK_M + ml.RIVER_DEPTH_M + 0.4, " m")
    # If the drawn half-width and the carved one disagree the map paints water over dry
    # bank at one and leaves an open trough with nothing in it at the other.
    check(f"the drawn bank at {hw:.0f} m sits on the waterline",
          float(np.abs(edges - wss).max()) <= 0.5,
          f"worst station is {float(np.abs(edges - wss).max()) * 100:.0f} cm out")
    rise = float(np.diff(beds).max())
    check("the water runs downhill the whole way", rise <= 0.02,
          f"the worst rise between stations is {rise * 100:.1f} cm")
    # Over ten stations and not one: the grade is 0.016%, so consecutive stations 40 m
    # apart differ by six millimetres and the centimetre the heightmap is quantised to
    # cannot tell them apart. Asking a strict inequality of two adjacent samples is
    # asking the encoding a question it has no way to answer.
    check("the river is still falling where it leaves the map",
          beds[-1] < beds[-10] and beds[0] > beds[9] and beds[0] > beds[-1],
          f"north edge {beds[0]:.2f} m, south edge {beds[-1]:.2f} m, "
          f"{beds[0] - beds[-1]:.2f} m of fall over "
          f"{(st[-1][0] - st[0][0]) / 1000.0:.1f} km")
    # The rim is added by addition on top of the channel, so without a notch the sill
    # simply rides up on the water and the river runs uphill to leave the map.
    sill_rows = [i for i, (_, p, _, _) in enumerate(st)
                 if p[1] < 0.0 or p[1] > ml.PLAYABLE_M]
    over = max(float(beds[i] - wss[i]) for i in sill_rows)
    check("the rim lets the river through both sills", over <= 0.0,
          f"the highest ground on the channel out in the border is {over:.2f} m "
          "relative to its own waterline")

    # ---------------------------------------------------------------- the lake
    ring = ml.lake_ring()
    isl = ml.island_ring()
    # Ring nodes the river's channel crosses are left out: the channel is three metres
    # under the waterline where it enters and leaves, which is a river scouring its way
    # across a littoral shelf and not the shore being in the wrong place.
    axis = ml.river_axis()
    clear = [(x, y) for x, y in ring
             if ml.dist_to_polyline((x, y), axis) > ml.RIVER_HALF_W_M + ml.BANK_RUN_M]
    shore = np.array([zat(raw, x, y) for x, y in clear])
    isl_shore = np.array([zat(raw, x, y) for x, y in isl])
    inside = np.zeros((PLAYABLE_M, PLAYABLE_M), dtype=bool)
    x0 = int(min(p[0] for p in ring)); x1 = int(max(p[0] for p in ring)) + 1
    y0 = int(min(p[1] for p in ring)); y1 = int(max(p[1] for p in ring)) + 1
    yy, xx = np.mgrid[y0:y1, x0:x1]
    q = ops.ellipse_r(xx.astype(np.float32), yy.astype(np.float32), *ml.LAKE_C,
                      ml.LAKE_A, ml.LAKE_B, ml.LAKE_ROT, ml.LAKE_HARMONICS)
    inside[y0:y1, x0:x1] = q <= 1.0
    deep = float(play_raw[inside].min() / 100.0)
    print(f"\nthe lake ({ml.ring_area_ha(ring):.0f} ha at {ml.LAKE_WS_M:.0f} m, "
          f"{ml.LAKE_DEPTH_M:.0f} m of water at its deepest):")
    band("the deepest water measures", ml.LAKE_WS_M - deep,
         ml.LAKE_DEPTH_M - 0.5, ml.LAKE_DEPTH_M + 0.1, " m")
    check("the bed clears the floor of the 16-bit range", deep >= 15.0,
          f"the deepest point is {deep:.2f} m, {deep:.0f} m of headroom")
    check("the drawn shore sits on the waterline",
          float(np.abs(shore - ml.LAKE_WS_M).max()) <= 0.4,
          f"worst of {len(clear)}/{len(ring)} nodes clear of the river's channel is "
          f"{float(np.abs(shore - ml.LAKE_WS_M).max()) * 100:.0f} cm out")
    check("the island's drawn shore sits on the waterline too",
          float(np.abs(isl_shore - ml.LAKE_WS_M).max()) <= 0.4,
          f"worst node is {float(np.abs(isl_shore - ml.LAKE_WS_M).max()) * 100:.0f} cm "
          "out")
    top = float(zat(raw, *ml.LAKE_C))
    band("the island stands over the water", top - ml.LAKE_WS_M,
         ml.ISLAND_H_M - 0.2, ml.ISLAND_H_M + 0.2, " m")
    check("the river does not cut a notch through the island",
          top - ml.LAKE_WS_M >= ml.ISLAND_H_M - 0.2,
          "the axis runs straight over it, so the channel has to be switched off there")

    # ---------------------------------------------------------------- the valley
    print(f"\nthe valley either side of the water ({ml.VALLEY_HALF_W_M:.0f} m):")
    # Only the offsets that land on ground this river is the nearest water to. A meander
    # belt 2.2 km long and 700 m across folds back on itself, so 500 m out from the
    # outside of one bend is inside the valley of the next - and that is the valleys
    # merging across the meander belt, which is what a floodplain does, not the section
    # coming out wrong.
    reach = hw + ml.VALLEY_HALF_W_M
    axis = ml.river_axis()
    ring = ml.lake_ring()
    outs = []
    for _, p, n, _ in st:
        if not 0.0 <= p[1] <= ml.PLAYABLE_M:
            continue                       # out in the border the rim owns the ground
        for sgn in (1.0, -1.0):
            q = (p[0] + sgn * n[0] * reach, p[1] + sgn * n[1] * reach)
            if not (0.0 <= q[0] <= ml.PLAYABLE_M and 0.0 <= q[1] <= ml.PLAYABLE_M):
                continue
            if ml.dist_to_polyline(q, axis) < reach - 2.0:
                continue
            if (ml.point_in_ring(q, ring)
                    or min(math.dist(q, r) for r in ring) < ml.VALLEY_HALF_W_M):
                continue
            outs.append(float(zat(raw, q[0], q[1]) - land_at(q[0], q[1])))
    outs = np.array(outs)
    mids = np.array([0.5 * (zat(raw, p[0] + n[0] * (hw + 0.5 * ml.VALLEY_HALF_W_M),
                                p[1] + n[1] * (hw + 0.5 * ml.VALLEY_HALF_W_M))
                            + zat(raw, p[0] - n[0] * (hw + 0.5 * ml.VALLEY_HALF_W_M),
                                  p[1] - n[1] * (hw + 0.5 * ml.VALLEY_HALF_W_M)))
                     for _, p, n, _ in st])
    # Against the till plain at that point, not against the datum: the datum is only the
    # mean of the uplands, and the section is built to close on the ground it is cut
    # into. Measuring it against 82 m would just be re-measuring the relief.
    check(f"the ground is back on the till plain {ml.VALLEY_HALF_W_M:.0f} m out from "
          "the water", outs.size > 100 and float(np.abs(outs).max()) <= 0.5,
          f"worst of {outs.size} offsets clear of the rest of the water is "
          f"{float(np.abs(outs).max()) * 100:.0f} cm off the till plain")
    check("and is well down into the valley half way there",
          float(mids.max()) <= BASE_ELEV_M - 3.0,
          f"the shallowest station measures {BASE_ELEV_M - float(mids.max()):.1f} m "
          "below the floodplain")
    side_deg = math.degrees(math.atan(
        1.875 * (ml.VALLEY_DEPTH_M - ml.WATER_BANK_M)
        / (ml.VALLEY_HALF_W_M - ml.BANK_RUN_M)))
    slope_valley = max_slope_deg(raw, mask=dry & valley)
    band("steepest dry slope in the valley", slope_valley, 0.0, VALLEY_SLOPE_MAX_DEG,
         " deg")
    info("what sets it", "the valley side falls "
         f"{ml.VALLEY_DEPTH_M - ml.WATER_BANK_M:.0f} m over "
         f"{ml.VALLEY_HALF_W_M - ml.BANK_RUN_M:.0f} m, and a smootherstep is steepest at "
         f"1.875*rise/run - {side_deg:.1f} deg. The till plain tilts under it, and where "
         "the lake's valley and the river's merge north of the lake both sections fall "
         "the same way at once; that shoulder is the steepest dry ground on the map and "
         "it is half a percent of the valley. Anything much over the band is a crease, "
         "and a crease shows as a step in the profile rather than as a slope.")

    # ---------------------------------------------------------------- the roads
    # The survey is a mile and the numbers that say so are asserted in `map_layout`. What
    # is measured here is whether the ground agrees: that each road holds the ruling grade
    # its class was given, that its running surface is flat across, and that the five
    # bridges are holes in the terrain rather than five places the channel was filled in
    # by the road that was meant to cross it.
    print(f"\nthe roads ({len(ml.corridors())}: the trunks a mile in from the east and "
          f"west edges, the section lines on the {ml.MILE_M:.0f} m grid, and "
          f"{sum(1 for c in ml.corridors() if c['kind'] == 'street')} town streets):")
    worst_cf, wetted = 0.0, 0
    for c in ml.corridors():
        a, h, pts = road_stations(c, raw)
        da = np.diff(a)
        g = float((np.abs(np.diff(h))[da < 25.0] / da[da < 25.0]).max())
        hw = c['half_width_m'] - 1.0
        vertical = abs(c['axis'][0][0] - c['axis'][-1][0]) < 1.0
        o1, o2 = ((hw, 0.0), (-hw, 0.0)) if vertical else ((0.0, hw), (0.0, -hw))
        cf = max(abs(float(zat(raw, p[0] + o1[0], p[1] + o1[1]))
                     - float(zat(raw, p[0] + o2[0], p[1] + o2[1]))) for p in pts[::10])
        wetted += sum(1 for p in pts[::5]
                      if wet[int(p[1] + OFFSET_M), int(p[0] + OFFSET_M)])
        check(f"{c['id']} holds its {c['grade_max'] * 100:.0f}% ruling grade",
              g <= c['grade_max'] + 1e-4,
              f"{g * 100:.2f}% over {(a[-1] - a[0]) / 1000.0:.1f} km, {h.min():.1f} .. "
              f"{h.max():.1f} m, cross-fall {cf * 100:.0f} cm")
        worst_cf = max(worst_cf, cf)
    band("the worst running surface is flat across", worst_cf, 0.0, 0.20, " m")
    check("no road platform is graded onto open water off a deck", wetted == 0,
          f"{wetted} station(s) off a bridge stand on water")

    sills = []
    for c in ml.corridors():
        dense = ml.densify(c['axis'], 5.0)
        arc = ops.polyline_arclen(dense)
        for s0, s1 in c.get('bridge_spans', ()):
            mid = [p for q, p in zip(arc, dense) if s0 + 5.0 <= q <= s1 - 5.0]
            if mid:
                sills.append(min(float(zat(raw, p[0], p[1])) for p in mid))
    check(f"the channel survives under all {len(sills)} decks",
          bool(sills) and max(sills) <= ml.LAKE_WS_M - 1.0,
          f"the highest ground under any span is {max(sills):.2f} m, against a waterline "
          f"near {ml.LAKE_WS_M:.0f} m")

    # ---------------------------------------------------------------- the towns
    # Four platforms levelled out of the till plain, each one a grid of blocks with a
    # trunk road up the middle of it. What is measured is that the ground under a block
    # is ground you could stand a building on: flat to the drain grade and no more, at
    # the height of the country round it rather than on a table, and with the streets
    # that were cut in afterwards not having stepped it.
    towns = [p for p in ml.pads() if p['kind'] == 'town']
    print(f"\nthe towns ({len(towns)} on the corners where a trunk road meets a "
          f"bridged section line, {ml.TOWN_COLS}x{ml.TOWN_ROWS} blocks of "
          f"{ml.TOWN_BLOCK_W_M:.0f}x{ml.TOWN_BLOCK_H_M:.0f} m):")
    worst_pad, worst_blk, worst_off = 0.0, 0.0, 0.0
    for pad in [p for p in ml.pads() if p['kind'] == 'town']:
        cx, cy = pad['centre']
        pw, ph = pad['size']
        # Inside the outermost street, so the pad's own feather is never in the window.
        gx0 = int(cx + ml.TOWN_COL_LINES[0]) + o
        gx1 = int(cx + ml.TOWN_COL_LINES[-1]) + o
        gy0 = int(cy + ml.TOWN_ROW_LINES[0]) + o
        gy1 = int(cy + ml.TOWN_ROW_LINES[-1]) + o
        sub = raw[gy0:gy1, gx0:gx1].astype(np.float32) / 100.0
        worst_pad = max(worst_pad,
                        float(ops.slope_deg(sub, 1.0,
                                            baseline_m=SLOPE_BASELINE_M).max()))
        worst_off = max(worst_off, abs(float(sub.mean())
                                       - float(land_at(cx, cy))))
        fall = float(sub[0].mean() - sub[-1].mean())
        info(f"{pad['id']}", f"{pw:.0f} x {ph:.0f} m at ({cx:.0f}, {cy:.0f}), "
             f"{sub.min():.2f} .. {sub.max():.2f} m, {fall * 100:.0f} cm of fall north "
             f"to south (want {pad['drain_grade'] * (gy1 - gy0) * 100:.0f})")
    worst_across = 0.0
    for a in ml.areas():
        if not a['id'].startswith('town_'):
            continue
        xs = [q[0] for q in a['ring']]
        ys = [q[1] for q in a['ring']]
        blk = raw[int(min(ys)) + o:int(max(ys)) + o,
                  int(min(xs)) + o:int(max(xs)) + o] / 100.0
        worst_blk = max(worst_blk, float(blk.max() - blk.min()))
        col = blk.mean(axis=0)
        worst_across = max(worst_across, float(col.max() - col.min()))
    # The drain grade over the depth of a block, and slack for the two things that
    # are not the drain: the cubic resample from the 4 m synthesis grid, and the road
    # bounding the block, whose feather at the kerb line - one nominal feather out from
    # its centreline - is still a third live and pulls that edge a few centimetres
    # toward the road's own profile. Which is what a verge is.
    blk_tol = ml.TOWN_DRAIN_GRADE * ml.TOWN_BLOCK_H_M + 0.08
    check("every block falls its own drain grade and no more", worst_blk <= blk_tol,
          f"the worst of {ml.TOWN_COLS * ml.TOWN_ROWS * len(towns)} blocks stands "
          f"{worst_blk * 100:.0f} cm corner to corner, against "
          f"{ml.TOWN_DRAIN_GRADE * ml.TOWN_BLOCK_H_M * 100:.0f} cm of drain "
          f"(want under {blk_tol * 100:.0f})")
    check("and is flat across, where nothing is draining", worst_across <= 0.03,
          f"the worst block tilts {worst_across * 100:.1f} cm along its 100 m side")
    # The platform is levelled to the median of the land it replaces, so it lands on the
    # till plain rather than on the datum - which is only the *mean* of the uplands and
    # would stand a town on low ground a storey proud of the country round it.
    band("each platform sits on the country it replaced", worst_off, 0.0, 1.5, " m")
    # The window takes in the streets as well as the blocks, so the ceiling has to be
    # the steepest *road* the platform is allowed to carry rather than the drain grade:
    # what actually sets this number is the corner where the trunk road, having been
    # graded across eight kilometres of till plain, ties into a platform falling at its
    # own third of a percent. A few percent over five metres there is a road junction,
    # not a defect. The blocks - the ground anything would be built on - are measured
    # tightly and separately just above; this one is here to catch a step at the pad
    # edge or a road that dived into it, and it is written against a number the layout
    # already carries rather than one tuned until it went green.
    kerb_deg = math.degrees(math.atan(max(c['grade_max'] for c in ml.corridors()
                                          if c['kind'] == 'street')))
    band("nothing on a platform is steeper than the streets it carries", worst_pad,
         0.0, kerb_deg, " deg")
    info("what sets it", f"the pad falls {ml.TOWN_DRAIN_GRADE * 100:.1f}% to the south "
         f"so it drains - {ml.TOWN_DRAIN_GRADE * 2 * ml.TOWN_HALF_H_M:.1f} m end to end "
         f"over a platform {2 * ml.TOWN_HALF_H_M:.0f} m long - and the streets were "
         "graded onto it afterwards. On ground already flat they have nothing to cut, "
         "which is the only reason a 6 m street with an 8 m feather is allowed on a 4 m "
         "synthesis grid at all; what is left is the tie-in where the trunk road meets "
         f"the platform, held under the {kerb_deg:.1f} deg a town street may run at")

    # -------------------------------------------------------- the roadside yards
    # The industrial aprons and the farms: square platforms hung off the road grid, all
    # of them the same shape of thing and all measured the same way. The question each
    # one has to answer is the one a yard asks - is the ground under it flat enough to
    # stand a shed and a hardstanding on, and is it at the height of the country round it
    # rather than on a table. The setback from the road is the layout's business and
    # `validate()` asserts it; what is measured here is only what the heightmap did.
    yards = [p for p in ml.pads() if 'road' in p]
    sizes = sorted({p['area_ha'] for p in yards})
    print(f"\nthe roadside yards ({len(yards)} square, "
          + ", ".join(f"{sum(1 for p in yards if p['area_ha'] == a)} of {a:.0f} ha "
                      f"({ml.yard_side(a):.0f} m a side)" for a in sizes)
          + f", standing {ml.ROADSIDE_SETBACK_M:.0f} m off the edge of the road):")
    tan_bank = math.tan(math.radians(BANK_DEG))
    worst_slope, worst_off, worst_res, worst_id = 0.0, 0.0, 0.0, ''
    worst_gate, worst_reach = 0.0, 0.0
    for pad in yards:
        cx, cy = pad['centre']
        w, h = pad['size']
        # Two metres in from the fence, so the pad's own feather never enters the window.
        gx0, gx1 = int(cx - w / 2) + 2 + o, int(cx + w / 2) - 2 + o
        gy0, gy1 = int(cy - h / 2) + 2 + o, int(cy + h / 2) - 2 + o
        sub = raw[gy0:gy1, gx0:gx1].astype(np.float32) / 100.0
        worst_slope = max(worst_slope,
                          float(ops.slope_deg(sub, 1.0,
                                              baseline_m=SLOPE_BASELINE_M).max()))
        worst_off = max(worst_off, abs(float(sub.mean()) - float(land_at(cx, cy))))

        # What the platform was built to be: a plane falling at the drain grade to the
        # south. The residual off it is everything the yard is not supposed to have.
        ys = np.arange(sub.shape[0], dtype=np.float32) + (gy0 - o) + 0.5
        plane = pad['drain_grade'] * (cy - ys)
        base = float(np.median(sub - plane[:, None]))
        res = sub - plane[:, None] - base

        def plane_at(y):
            return base + pad['drain_grade'] * (cy - y)

        # The road's platform reaches into the yard, and it is meant to: corridors are
        # graded *after* pads precisely so the road survives where the two meet. How far
        # in is not a guess - it is the feather, `1.5*|dz|/tan(4 deg)`, less the setback
        # that already stands between the fence and the running surface, with `dz` read
        # off the DEM at the fence rather than assumed. Everything inside that band is
        # the road's; the yard proper is what is left, and that is what gets measured.
        # Left in, a 447 m farm whose plane sits 0.9 m under its own access road failed
        # a check about drainage for a reason that was a driveway.
        c = ml.corridor_by_id(pad['road'])
        ax = c['axis']
        xs = np.arange(sub.shape[1], dtype=np.float32) + (gx0 - o) + 0.5
        near = c['half_width_m'] + pad['setback_m']      # centreline to the near fence
        if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]):
            d_in = np.abs(xs - ax[0][0])[None, :] - near
        else:
            d_in = np.abs(ys - ax[0][1])[:, None] - near
        d_in = np.broadcast_to(d_in, res.shape)          # metres in from the fence
        # How deep the gate is, taken **at the road** and not at the fence. That is
        # where the generator sized the feather from - `max(nominal, 1.5*|dz|/tan(4
        # deg))` against the difference between the road's profile and the ground it is
        # cutting - so it is the only place the number means anything. Read at the fence
        # instead and it comes back already attenuated by the very smoothstep whose
        # width is being solved for: a road running 98 cm under a farm's platform showed
        # 29 cm at the fence, which sized the band at 1 m instead of 11 and handed the
        # check the driveway to judge as yard.
        if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]):
            stations = [(ax[0][0], q) for q in np.arange(cy - h / 2, cy + h / 2, 10.0)]
        else:
            stations = [(q, ax[0][1]) for q in np.arange(cx - w / 2, cx + w / 2, 10.0)]
        gate = max(abs(float(zat(raw, sx, sy)) - plane_at(sy)) for sx, sy in stations)
        # The generator clips the feather to the class's *nominal* as a floor, so a
        # shallow cut does not narrow it: a primary's platform reaches 14 m off its
        # centreline however little it is cutting.
        feather = max(c['feather_m'], 1.5 * gate / tan_bank)
        reach = max(0.0, feather - pad['setback_m'])
        keep = d_in >= reach
        r = float(np.abs(res[keep]).max()) if keep.any() else 0.0
        if r > worst_res:
            worst_res, worst_id = r, pad['id']
        worst_gate, worst_reach = max(worst_gate, gate), max(worst_reach, reach)
    # Four centimetres: the platform is a plane on the 4 m synthesis grid and the PNG is
    # a cubic resample of it, so what is left is the resample and the centimetre the
    # heightmap is quantised to.
    check("every yard is the plane it was graded to, clear of the road's own platform",
          worst_res <= 0.04,
          f"the worst of {len(yards)} is {worst_id}, {worst_res * 100:.1f} cm off its "
          f"own drain plane (want under 4)")
    band("each yard sits on the country it replaced", worst_off, 0.0, 1.5, " m")
    band("nothing on a yard is steeper than the road at its gate", worst_slope, 0.0,
         math.degrees(math.atan(max(ml.corridor_by_id(p['road'])['grade_max']
                                    for p in yards))), " deg")
    info("the gate", f"the deepest a yard's platform runs under the road at its gate "
         f"is {worst_gate * 100:.0f} cm, which the road's feather carries in over "
         f"{worst_reach:.0f} m past the fence at {BANK_DEG:.0f} deg - a driveway, and "
         "why the flatness check starts where the road's platform ends rather than at "
         "the fence")

    # ---------------------------------------------------------------- the rim
    # Two ranges and two sills. The ranges are what the brief asked for; the sills are
    # what makes it a valley rather than a bowl, and they are the check most likely to
    # go quietly wrong, because a rim that closes at both ends still looks like a rim.
    print(f"\nthe valley rim ({ml.RIM_SADDLE_M:.0f} .. {ml.RIM_CREST_M:.0f} m ranges "
          f"east and west, a {ml.RIM_MOUTH_M:.0f} m sill north and south, all as lifts "
          f"over a {BASE_ELEV_M:.0f} m datum):")
    # Measured as lift, `crest - foot`, because the rim rides on the till plain: a summit
    # standing on a moraine stands the moraine's height higher, and its absolute
    # elevation then says as much about the ground under it as about the rim. The slack
    # in the bands is the till plain's own swing between the apron and the crest, two
    # kilometres away.
    slack = 12.0
    band("the summits reach the crest", hi_cm / 100.0 - BASE_ELEV_M,
         ml.RIM_HEIGHT_M - 24.0, ml.RIM_HEIGHT_M + slack, " m")
    sill_lo = ml.RIM_MOUTH_M - BASE_ELEV_M - 3.0 * ml.RIM_MOUTH_VAR_M - slack
    sill_hi = ml.RIM_MOUTH_M - BASE_ELEV_M + 3.0 * ml.RIM_MOUTH_VAR_M + slack
    for side in ('west', 'east'):
        c = crest[side] - foot[side]
        check(f"the {side} range never drops under its saddle",
              float(c.min()) >= ml.RIM_SADDLE_M - BASE_ELEV_M - slack,
              f"lifts {float(c.min()):.1f} .. {float(c.max()):.1f} m along "
              f"{c.size / 1000.0:.1f} km, mean {float(c.mean()):.1f} m")
    for side in ('north', 'south'):
        # Skip the columns the river runs out through. The notch is the rim doing what it
        # is told; measuring the sill across it would report the river as a hole in the
        # mountains, and worse, a rim that had actually dammed the channel would then
        # pass this check by filling that hole in.
        c = (crest[side] - foot[side])[notch[side]]
        check(f"the valley runs out over the {side} sill",
              sill_lo <= float(c.min()) and float(c.max()) <= sill_hi,
              f"{float(c.min()):.1f} .. {float(c.max()):.1f} m along "
              f"{c.size / 1000.0:.1f} km clear of the river's notch "
              f"(want {sill_lo:.0f}..{sill_hi:.0f} m)")
    for side in ('north', 'south'):
        # The floor of the notch, not its shoulders: the mask covers the feather too, and
        # at the outer edge of a feather the rim is back to full height by definition.
        # What has to be true is that somewhere in there the sill comes right down to the
        # water it is letting past.
        c = crest[side][~notch[side]]
        check(f"the {side} sill comes down to the water where the river crosses it",
              c.size > 0 and float(c.min()) <= BASE_ELEV_M,
              f"the notch is {c.size} m wide and its floor is {float(c.min()):.1f} m, "
              f"against a sill lifted {ml.RIM_MOUTH_M - BASE_ELEV_M:.0f} m"
              if c.size else "no notch at all")
    ranges = min((crest['west'] - foot['west']).min(),
                 (crest['east'] - foot['east']).min())
    sills = max((crest['north'] - foot['north'])[notch['north']].max(),
                (crest['south'] - foot['south'])[notch['south']].max())
    check("the ranges stand clear over the sills", ranges - sills >= 60.0,
          f"the lowest saddle is {ranges - sills:.0f} m over the highest sill")

    # ---------------------------------------------------------------- surface
    print(f"\nsurface ({SLOPE_BASELINE_M:.0f} m baseline):")
    worst = max_slope_deg(raw)
    band("steepest slope on the canvas", worst, 0.0, ml.RIM_MAX_FLANK_DEG, " deg")
    info("what sets it",
         f"the flank climbs {ml.RIM_HEIGHT_M:.0f} m over {ml.RIM_RAMP_M:.0f} m of "
         f"border, and a smoothstep is steepest at 1.5*rise/run - "
         f"{math.degrees(math.atan(1.5 * ml.RIM_HEIGHT_M / ml.RIM_RAMP_M)):.1f} deg. The "
         "spurs riding on it are long enough that they add almost nothing; if this "
         "reads well over the ramp, something has put a crease in the rim.")
    dry_play = np.zeros_like(dry)
    dry_play[o:o + PLAYABLE_M, o:o + PLAYABLE_M] = dry[o:o + PLAYABLE_M,
                                                       o:o + PLAYABLE_M]
    band("steepest slope on dry land inside the playable square",
         max_slope_deg(raw, mask=dry_play), 0.0, VALLEY_SLOPE_MAX_DEG, " deg")
    band("steepest slope on the uplands, clear of the valley",
         max_slope_deg(raw, mask=dry_play & ~natural), 0.0, 4.0, " deg")
    info("and under the water", f"{max_slope_deg(raw, mask=wet):.1f} deg on the "
         "submerged banks, which no machine ever drives and which is the only reason "
         "the lake is allowed to be 38 m deep inside its own shore")

    # ---------------------------------------------------------------- stats
    # The OSM side sizes what it places off this file rather than re-deriving the
    # terrain. If it and the PNG disagree the two halves of the pipeline are describing
    # different ground, which is invisible in either output on its own.
    print("\nterrain_stats.json:")
    if not check("published", os.path.exists(stats_path)):
        return 1
    rough = ml.load_roughness(stats_path)
    with open(stats_path) as fh:
        stats = json.load(fh)
    hgt = np.array(stats['height'], dtype=np.float64)
    rgh = np.array(stats['roughness'], dtype=np.float64)
    check(f"{STATS_GRID}x{STATS_GRID} grid", stats['n'] == STATS_GRID,
          f"n = {stats['n']}")
    check("covers the playable area", abs(stats['n'] * stats['cell_m'] - PLAYABLE_M)
          < 1e-6, f"{stats['n']} x {stats['cell_m']:.0f} m = "
                  f"{stats['n'] * stats['cell_m']:.0f} m")
    check("origin at the north-west corner of the playable area",
          stats['origin'] == [0.0, 0.0], f"{stats['origin']}")
    k = PLAYABLE_M // STATS_GRID
    blocks = (play_raw.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3)) / 100.0)
    worst = float(np.abs(hgt - blocks.ravel()).max())
    # Not a centimetre: the stats are block means of the 4 m synthesis grid and the PNG
    # is a cubic resample of the same surface, so they part company by a little wherever
    # the ground is curved. A quarter of a metre against a 55 m range is the two
    # describing one landscape; a metre would be two.
    check("height grid agrees with the PNG", worst <= 0.25,
          f"worst of {hgt.size} cells is {worst:.3f} m off")
    cell = stats['cell_m']
    cx = (np.arange(STATS_GRID) + 0.5) * cell
    # Any of the cell, not its centre. A 64 m cell whose centre is 30 m outside the
    # valley still has half of it inside, and the 40 m slope baseline the roughness is
    # measured over reaches further than that again.
    kk = PLAYABLE_M // STATS_GRID

    def cells(m):
        return m[o:o + PLAYABLE_M, o:o + PLAYABLE_M].reshape(
            STATS_GRID, kk, STATS_GRID, kk).any(axis=(1, 3)).ravel()

    in_valley = cells(valley)
    # Graded ground is left out of *both* sides. The comparison is between the natural
    # valley and the natural uplands: a road or a yard is flat by construction, so
    # counting it as valley makes the valley look tamer and counting it as upland makes
    # the uplands look flatter than the ground they are cut from. Either way the number
    # says something about how much has been built rather than about the landscape.
    on_built = cells(built)
    # One cell of margin, because roughness is a slope measured over a 40 m baseline and
    # a 64 m cell sitting against the valley legitimately sees down into it. Without the
    # margin the "floodplain" includes cells whose measuring window does not.
    clear = (~ndimage.binary_dilation(in_valley.reshape(STATS_GRID, STATS_GRID)).ravel()
             & ~on_built)
    in_valley = in_valley & ~on_built
    up_r, val_r = rgh.reshape(-1)[clear], rgh.reshape(-1)[in_valley]
    # It is not enough that roughness is non-zero somewhere: anything that sizes itself
    # to the ground reads this file, so it has to *separate* the ground a machine works
    # from the ground it does not. A scale that saturates on the till plain's own swells
    # reads 1.000 everywhere and says nothing.
    check("the uplands read as ground you can farm",
          float(up_r.mean()) < 0.35 and float(np.percentile(up_r, 90)) < 0.6,
          f"{int(clear.sum())} cells a whole baseline clear of the valley: mean "
          f"{float(up_r.mean()):.3f}, p90 {float(np.percentile(up_r, 90)):.3f}")
    check("the valley reads as broken ground",
          float(val_r.mean()) > float(up_r.mean()) + 0.25,
          f"{int(in_valley.sum())} cells in the valley average "
          f"{float(val_r.mean()):.3f} against the uplands' {float(up_r.mean()):.3f}")
    check("nothing is saturated everywhere", float((rgh >= 0.999).mean()) < 0.15,
          f"{float((rgh >= 0.999).mean()) * 100:.1f}% of cells are pinned at 1.000")

    # ---------------------------------------------------------------- layout
    print("\nlayout:")
    problems = ml.validate()
    check("map_layout validates", not problems,
          "; ".join(problems) if problems else "no complaints")
    drawn = {a['id'].rsplit('_b', 1)[0] for a in ml.areas() if '_b' in a['id']}
    undrawn = [p['id'] for p in ml.pads()
               if not p.get('tags') and p['id'].rsplit('_pad', 1)[0] not in drawn]
    check("nothing sculpted that the vectors do not carry", not undrawn,
          f"{len(ml.water())} water bodies, {len(ml.pads())} platforms and "
          f"{len(ml.corridors())} corridors all carved and drawn"
          if not undrawn else f"{len(undrawn)} pad(s) levelled with nothing over them: "
          + ", ".join(undrawn))
    info("the rim is the exception",
         "it carries no vectors because none of it is ground the player can reach; it is "
         "parametric, and its constants and its one implementation both live outside "
         "this file")

    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("failed:")
        for n in failed:
            print("   -", n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
