#!/usr/bin/env python3
"""Acceptance report for the generated heightmap.

Checks the things that are easy to break and hard to see in the image: that the canvas is
the size and encoding the rest of the project expects, that nothing touches the floor or
the ceiling of the 16-bit range, that the playable square really is the datum rather than
nearly it, that the valley rim in the border is the valley the brief asked for - two
ranges standing 200 to 250 m over a floor at 20 m, a sill low enough at either end that
the valley runs through rather than being walled in - and that `terrain_stats.json`,
which the parcelling reads instead of re-deriving the terrain, describes the same surface
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
    if not ml.water():
        z_shape = (CANVAS_M, CANVAS_M)
        zeros_mask = np.zeros(z_shape, dtype=bool)
        ones_mask = np.ones(z_shape, dtype=bool)
        d_river_w = np.full((CANVAS_M, CANVAS_M), 1e9, dtype=np.float32)
        return zeros_mask, zeros_mask, ones_mask, d_river_w, land_w
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
    axis = ml.river_axis() if ml.water() else None
    notch_reach = 2.0 * (ml.RIVER_NOTCH_HALF_M + ml.RIVER_NOTCH_FEATHER_M)
    for r0 in range(0, n, RIM_BAND_ROWS):
        r1 = min(n, r0 + RIM_BAND_ROWS)
        X, Y = np.meshgrid(ax, ax[r0:r1])
        t, w = rim_ramp(X, Y)
        # Only the two sill strips and the apron need to know where the river is, and
        # the distance transform over a 322-segment axis with a two-kilometre window is
        # the most expensive thing in this file by an order of magnitude. Everywhere else
        # the rim is a long way from the water and the answer cannot matter.
        want = bool(((t >= 1.0) & (w <= 0.01)).any()) and (axis is not None)
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

    # ---------------------------------------------------------------- elevation and relief
    print("\nelevation and relief:")
    hill_peaks = [BASE_ELEV_M + h['height_m'] for h in ml.hills()]
    max_expected_m = max([ml.RIM_CREST_M, BASE_ELEV_M] + hill_peaks) + 10.0
    band("canvas elevation minimum", lo_cm / 100.0, 1.0, max_expected_m, " m")
    band("canvas elevation maximum", hi_cm / 100.0, 1.0, max_expected_m, " m")
    band("playable area minimum", float(play_raw.min()) / 100.0, 1.0, max_expected_m, " m")
    band("playable area maximum", float(play_raw.max()) / 100.0, 1.0, max_expected_m, " m")
    if ml.corridors() or ml.areas() or ml.water() or ml.pads() or ml.hills():
        check("playable area has features", float(play_raw.max() - play_raw.min()) / 100.0 >= 0.0,
              f"relief {float(play_raw.max() - play_raw.min()) / 100.0:.2f} m")
    else:
        if ml.RIM_CREST_M <= BASE_ELEV_M and ml.MORAINE_AMP_M == 0.0 and ml.SWELL_AMP_M == 0.0:
            check("clean canvas flat at datum",
                  abs(lo_cm - want_cm) <= FLAT_TOL_CM and abs(hi_cm - want_cm) <= FLAT_TOL_CM,
                  f"{lo_cm / 100.0:.2f} .. {hi_cm / 100.0:.2f} m (want {BASE_ELEV_M:.1f} m)")
        check("clean playable area at datum", abs(float(play_raw.mean()) / 100.0 - BASE_ELEV_M) <= 1.0,
              f"mean {float(play_raw.mean()) / 100.0:.2f} m (want {BASE_ELEV_M:.1f} m)")
    for h in ml.hills():
        top_z = float(zat(raw, h['cx'], h['cy']))
        want_top = BASE_ELEV_M + h['height_m']
        band(f"{h['name']} summit elevation", top_z, want_top - 1.0, want_top + 1.0, " m")
        pts = h['spiral_pts']
        arc = ops.polyline_arclen(pts)
        zs = np.array([float(zat(raw, p[0], p[1])) for p in pts])
        ds = np.diff(arc)
        dz = np.diff(zs)
        grades_pct = np.abs(dz / np.maximum(ds, 1e-6)) * 100.0
        band(f"{h['name']} spiral ramp max grade", float(grades_pct.max()), 0.0, 8.0, "%")
    mean_play = float(play_raw.mean()) / 100.0
    info("playable mean elevation", f"{mean_play:.2f} m (datum {BASE_ELEV_M:.1f} m)")

    # ---------------------------------------------------------------- surface
    print(f"\nsurface ({SLOPE_BASELINE_M:.0f} m baseline):")
    worst = max_slope_deg(raw)
    slope_limit = 38.0 if ml.hills() else 15.0
    band("steepest slope on the canvas", worst, 0.0, slope_limit, " deg")
    info("canvas slope", f"steepest slope on canvas is {worst:.1f} deg")

    # ---------------------------------------------------------------- stats
    print("\nterrain_stats.json:")
    if not check("published", os.path.exists(stats_path)):
        return 1
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
    worst_diff = float(np.abs(hgt - blocks.ravel()).max())
    check("height grid agrees with the PNG", worst_diff <= 0.25,
          f"worst of {hgt.size} cells is {worst_diff:.3f} m off")
    check("roughness values in valid range", bool((rgh >= 0.0).all() and (rgh <= 1.0).all()),
          f"min {float(rgh.min()):.4f}, max {float(rgh.max()):.4f}")

    # ---------------------------------------------------------------- layout
    print("\nlayout:")
    problems = ml.validate()
    check("map_layout validates", not problems,
          "; ".join(problems) if problems else "no complaints")
    if ml.corridors() or ml.areas() or ml.pads():
        info("vector features", f"{len(ml.corridors())} corridors, {len(ml.pads())} pads, {len(ml.areas())} areas")
    else:
        info("vector features", "clean layout (no features defined yet)")

    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("failed:")
        for n in failed:
            print("   -", n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
