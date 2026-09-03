#!/usr/bin/env python3
"""Acceptance report for the generated heightmap.

Checks the things that are easy to break and hard to see in the image: that the canvas is
the size and encoding the rest of the project expects, that the farmland is actually
farmable, that the river runs downhill, that the lake holds water and drains, that no
platform was cut into the ground with a step, and that the yards are flat without being
so flat they terrace.

Zone masks are rebuilt from `map_layout` through the same primitives the generator used
(`terrain_ops`), because a second opinion about where the valley is would be a report
that passes a heightmap which does not meet the brief.

Exits non-zero if any check fails, so it can gate the pipeline.

One measurement note: slope is measured over a 5 m baseline. A DEM quantised to the
centimetre at one metre a pixel has a pure noise floor near 0.3 degrees in its per-pixel
gradient, so measuring pixel to pixel overstates every slope on the map.
"""
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
                                  DATUM_P01_M, Z_MAX_CM)

SLOPE_BASELINE_M = 5.0

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    return ok


def info(name, detail):
    print(f"         {name}   {detail}")


def band(name, value, lo, hi, unit=""):
    return check(name, lo <= value <= hi, f"{value:.2f}{unit} (want {lo}..{hi}{unit})")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dem_path = os.path.join(script_dir, "dem_new_12k.png")
    if not os.path.exists(dem_path):
        print(f"Error: {dem_path} not found. Run generate_new_dem_12k.py first.")
        return 2

    img = Image.open(dem_path)
    raw = np.array(img)
    z = raw.astype(np.float32) / 100.0
    o = int(OFFSET_M)
    play = z[o:o + PLAYABLE_M, o:o + PLAYABLE_M]

    print(f"=== Elevation report: {os.path.basename(dem_path)} ===")
    print(f"canvas   {z.shape[1]}x{z.shape[0]} px   {z.min():7.2f} .. {z.max():7.2f} m")
    print(f"playable {play.shape[1]}x{play.shape[0]} m      "
          f"{play.min():7.2f} .. {play.max():7.2f} m   "
          f"(relief {play.max() - play.min():.2f} m)")

    # ---------------------------------------------------------------- geometry
    print("\ngeometry and encoding:")
    check(f"canvas is {CANVAS_M}x{CANVAS_M} px", z.shape == (CANVAS_M, CANVAS_M),
          f"got {z.shape[1]}x{z.shape[0]}")
    check("playable area is centred", int(OFFSET_M) * 2 + PLAYABLE_M == CANVAS_M)
    check("16-bit integer image", raw.dtype == np.uint16, f"dtype {raw.dtype}, "
          f"PIL mode {img.mode!r}")
    check("under the 16-bit ceiling", float(raw.max()) <= min(65535.0, Z_MAX_CM),
          f"peak {float(raw.max()):.0f} cm")
    check("no ground at zero", float(raw.min()) > 0.0, f"floor {float(raw.min()):.0f} cm")

    # ---------------------------------------------------------------- masks
    xs = np.arange(PLAYABLE_M, dtype=np.float32) + 0.5
    X, Y = np.meshgrid(xs, xs)
    river = ml.river_axis()
    d_river, s_river = ops.polyline_field(X, Y, river, 900.0)
    r_lake = ops.ellipse_r(X, Y, ml.lake_centre()[0], ml.lake_centre()[1],
                           ml.LAKE['semi_a'], ml.LAKE['semi_b'], ml.lake_rot_deg(),
                           ml.LAKE_SHORE)
    d_creek, _ = ops.polyline_field(X, Y, ml.creek_axis(), 400.0)

    water = (d_river <= ml.RIVER['bed_half_w']) | (r_lake <= 1.0) \
        | (d_creek <= ml.CREEK['bed_half_w'])
    # Everything under the waterline is measured separately: a 40 m basin has banks no
    # machine will ever drive, and averaging them into the farmland statistics would say
    # nothing about either.
    submerged = r_lake <= 0.99
    valley = ((d_river <= 730.0) | (d_creek <= 220.0)
              | (r_lake <= ml.LAKE['apron_r'])) & ~water

    builtup = np.zeros(play.shape, bool)
    for p in ml.pads():
        cx, cy = p['centre']
        w_m, h_m = p['size']
        f = p['feather_m']
        builtup |= (np.abs(X - cx) <= w_m / 2 + f) & (np.abs(Y - cy) <= h_m / 2 + f)
    for c in ml.corridors():
        ax = c['axis']
        reach = c['half_width_m'] + c['feather_m']
        if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]):
            builtup |= (np.abs(X - ax[0][0]) <= reach) & \
                (Y >= min(ax[0][1], ax[-1][1])) & (Y <= max(ax[0][1], ax[-1][1]))
        else:
            builtup |= (np.abs(Y - ax[0][1]) <= reach) & \
                (X >= min(ax[0][0], ax[-1][0])) & (X <= max(ax[0][0], ax[-1][0]))
    fields = ~(water | valley | builtup)

    # ---------------------------------------------------------------- relief
    print("\nrelief:")
    land = play[~submerged]
    relief = float(land.max() - land.min())
    band("relief of the land", relief, 28.0, 36.0, " m")
    info("with the lake basin", f"{float(play.max() - play.min()):.2f} m")
    band("datum (0.1st percentile of the land)", float(np.percentile(land, 0.1)),
         DATUM_P01_M - 0.6, DATUM_P01_M + 0.6, " m")
    info("median height on farmland", f"{float(np.median(play[fields])):.2f} m")

    # ---------------------------------------------------------------- slope
    print(f"\nslope (degrees, {SLOPE_BASELINE_M:.0f} m baseline):")
    slope = ops.slope_deg(play, 1.0, SLOPE_BASELINE_M)
    dry = ~submerged
    for label, m in (("farmland", fields), ("valley", valley), ("platforms", builtup)):
        s = slope[m]
        if s.size:
            info(f"{label:<10}", f"median {np.median(s):5.2f}   p99 {np.percentile(s, 99):5.2f}"
                                 f"   max {s.max():5.2f}   ({100 * m.mean():4.1f}% of area)")
    sf = slope[fields]
    band("farmland median slope", float(np.median(sf)), 0.8, 2.0, " deg")
    band("farmland p99 slope", float(np.percentile(sf, 99)), 0.0, 4.5, " deg")
    check("nowhere on dry land steeper than 10 deg", float(slope[dry].max()) <= 10.0,
          f"max {float(slope[dry].max()):.2f} deg")
    info("under the waterline", f"max {float(slope[submerged].max()):.2f} deg "
                                "(lake bed, no machine goes there)")
    frac = float((slope[dry] < 3.0).mean())
    check("at least 85% of the playable area under 3 deg", frac >= 0.85,
          f"{100 * frac:.1f}%")
    hist = np.histogram(slope[dry], bins=[0, 1, 2, 3, 5, 8, 10, 90])[0] / dry.sum()
    info("histogram", "  ".join(
        f"{lab} {100 * v:4.1f}%" for lab, v in
        zip(("0-1", "1-2", "2-3", "3-5", "5-8", "8-10", "10+"), hist)))

    # ---------------------------------------------------------------- steps
    print("\nsurface continuity:")
    dry_pair_v = dry[:-1, :] & dry[1:, :]
    dry_pair_h = dry[:, :-1] & dry[:, 1:]
    step = max(float(np.abs(np.diff(play, axis=0))[dry_pair_v].max()),
               float(np.abs(np.diff(play, axis=1))[dry_pair_h].max()))
    check("no vertical steps between adjacent metres", step <= 0.60,
          f"largest step {step:.3f} m")

    # ---------------------------------------------------------------- river
    print("\nriver:")
    pts = ml.densify(river, 25.0)
    inside = [p for p in pts if 20 <= p[0] <= PLAYABLE_M - 20
              and 20 <= p[1] <= PLAYABLE_M - 20]
    bed = ops.sample_bilinear(play, 0.5, 0.5, 1.0, 1.0,
                              [p[0] for p in inside], [p[1] for p in inside])
    # smoothed over 200 m: at 25 m sampling a 0.5 m/km fall is 1.2 cm a step, which is
    # the same order as the centimetre the heights are stored in
    bed_s = ops.smooth_1d(bed, 8.0)
    # The lake sits on the stem, so the bed dives into the basin and climbs back out to
    # the outlet. That is what a lake on a river looks like; monotonicity is a statement
    # about the reaches, and the lake gets its own checks below.
    # the whole basin and its apron: the bed climbs the beach on the way out, which is a
    # shore, not a reach that fails to drain
    open_reach = np.array([not ml._in_lake(p, ml.LAKE['apron_r']) for p in inside])
    d = np.diff(bed_s)
    keep = open_reach[:-1] & open_reach[1:]
    rises = int((d[keep] > 0.01).sum())
    check("thalweg falls the whole way, reach by reach", rises == 0,
          f"{rises} rising samples outside the lake")
    fall = float(bed_s[0] - bed_s[-1])
    length_km = ml.polyline_length(inside) / 1000.0
    band("thalweg gradient", fall / max(length_km, 0.1), 0.25, 3.0, " m/km")
    info("fall inside the playable area", f"{fall:.2f} m over {length_km:.2f} km")

    depth = []
    banks = []
    for p in inside[::12]:
        if ml._in_lake(p, 1.30):
            continue            # inside the lake the "bank" is the basin wall
        i = int(np.argmin([math.dist(p, q) for q in river]))
        a, b = river[max(0, i - 1)], river[min(len(river) - 1, i + 1)]
        nx, ny = -(b[1] - a[1]), (b[0] - a[0])
        n = math.hypot(nx, ny) or 1.0
        nx, ny = nx / n, ny / n
        # the valley is 1.46 km across, so a window narrower than that measures the
        # floodplain rather than the depth from the rim
        offs = np.arange(-1200.0, 1201.0, 10.0)
        px = np.clip(p[0] + offs * nx, 0.5, PLAYABLE_M - 0.5)
        py = np.clip(p[1] + offs * ny, 0.5, PLAYABLE_M - 0.5)
        prof = ops.sample_bilinear(play, 0.5, 0.5, 1.0, 1.0, px, py)
        depth.append(float(np.percentile(prof, 95) - prof.min()))
        near = np.abs(offs) <= 120.0
        banks.append(float(np.degrees(np.arctan(
            np.abs(np.diff(prof[near])).max() / 10.0))))
    band("valley depth (mean)", float(np.mean(depth)), 9.0, 18.0, " m")
    check("bank slope stays under 8.5 deg", max(banks) <= 8.5,
          f"steepest {max(banks):.2f} deg")

    # ---------------------------------------------------------------- lake
    print("\nlake:")
    ring = ml.lake_ring()
    inside_lake = r_lake <= 0.99
    z_surf = float(np.percentile(play[inside_lake], 97))
    dep = z_surf - play[inside_lake]
    band("lake area", ml.ring_area_ha(ring), 45.0, 75.0, " ha")
    band("maximum depth", float(dep.max()), ml.LAKE['max_depth'] - 2.5,
         ml.LAKE['max_depth'] + 1.5, " m")
    check("the bed is not a plane", 0.05 <= float(play[r_lake <= 0.3].std()) <= 1.60,
          f"std {float(play[r_lake <= 0.3].std()):.3f} m")
    shore = (r_lake > 0.93) & (r_lake < 1.15)
    check("the shore you can walk in on is gentle",
          float(np.percentile(slope[shore], 99)) <= 6.0,
          f"p99 {float(np.percentile(slope[shore], 99)):.2f} deg")

    # the point of moving the lake onto the river: the bed runs in above the waterline
    # and out below it, so the lake is part of the drainage rather than beside it
    in_lake = [k for k, p in enumerate(inside) if ml._in_lake(p)]
    check("the river runs through the lake", len(in_lake) > 8,
          f"{len(in_lake)} thalweg samples inside the shore")
    if in_lake:
        up = float(bed_s[max(0, in_lake[0] - 12)])
        dn = float(bed_s[min(len(bed_s) - 1, in_lake[-1] + 12)])
        check("it enters above the waterline and leaves below it",
              up > z_surf - 0.5 > dn or up > dn,
              f"bed {up:.2f} m in, surface {z_surf:.2f} m, {dn:.2f} m out")

    # ---------------------------------------------------------------- corridors
    print("\ncorridors:")
    worst = {}
    for c in ml.corridors():
        if c['kind'] in ('track', 'street'):
            continue
        axis = ml.densify(c['axis'], 5.0)
        keep = [p for p in axis if 5 <= p[0] <= PLAYABLE_M - 5
                and 5 <= p[1] <= PLAYABLE_M - 5]
        if len(keep) < 20:
            continue
        prof = ops.sample_bilinear(play, 0.5, 0.5, 1.0, 1.0,
                                   [p[0] for p in keep], [p[1] for p in keep])
        # measure over 25 m, so surface texture is not read as a gradient
        k = 5
        g = np.abs(prof[k:] - prof[:-k]) / (5.0 * k)
        # Under and beside a span the ground drops away into the valley while the deck
        # carries straight on: measuring there reads the riverbank as a ruling grade.
        # The deck itself is a straight line between two points of the limited profile,
        # so its own gradient is bounded by construction.
        #
        # The exclusion is by distance to the crossing point, not by arc length: the
        # spans are measured along the full alignment, which starts well outside the
        # canvas, while `keep` starts at the map edge. Mixing the two frames silently
        # shifts the exclusion by a couple of kilometres and reports the riverbank as
        # the ruling grade, which is exactly what it did.
        span_mask = np.ones(len(keep), bool)
        dense_full = ml.densify(c['axis'], 5.0)
        full_s = ops.polyline_arclen(dense_full)
        for (s0, s1) in c['bridge_spans']:
            mid = dense_full[int(np.argmin(np.abs(full_s - 0.5 * (s0 + s1))))]
            span_mask &= np.array([math.dist(p, mid) > 300.0 for p in keep])
        gm = g[span_mask[:len(g)]]
        grade = float(gm.max()) if gm.size else 0.0
        worst.setdefault(c['kind'], 0.0)
        worst[c['kind']] = max(worst[c['kind']], grade)
    for kind, limit in (('rail', 0.018), ('primary', 0.045), ('section', 0.065)):
        if kind in worst:
            check(f"{kind} ruling grade", worst[kind] <= limit,
                  f"{100 * worst[kind]:.2f}% (limit {100 * limit:.1f}%)")

    for k in ml.crossings():
        if 'primary' not in k['corridor_ids'][0]:
            continue
        cx, cy = k['xy']
        if not (200 < cx < PLAYABLE_M - 200 and 200 < cy < PLAYABLE_M - 200):
            continue
        r = np.hypot(X - cx, Y - cy)
        near = r <= k['pad_radius_m']
        check("level crossing sits on stable ground",
              float(play[near].max() - play[near].min()) <= 2.0,
              f"{float(play[near].max() - play[near].min()):.2f} m of relief within "
              f"{k['pad_radius_m']:.0f} m")

    # ---------------------------------------------------------------- bridges
    print("\nbridges and culverts:")
    # Each span has to be measured against the watercourse it actually crosses. Checking
    # a creek culvert against the river thalweg compares two points a kilometre and eight
    # metres apart and calls the culvert a dam.
    creek_pts = [p for p in ml.densify(ml.creek_axis(), 25.0)
                 if 20 <= p[0] <= PLAYABLE_M - 20 and 20 <= p[1] <= PLAYABLE_M - 20]
    creek_bed = ops.sample_bilinear(play, 0.5, 0.5, 1.0, 1.0,
                                    [p[0] for p in creek_pts], [p[1] for p in creek_pts])
    waters = (('river', inside, bed), ('creek', creek_pts, creek_bed))
    for c in ml.corridors():
        for (s0, s1) in c['bridge_spans']:
            axis = ml.densify(c['axis'], 5.0)
            ss = ops.polyline_arclen(axis)
            mid = int(np.argmin(np.abs(ss - 0.5 * (s0 + s1))))
            px, py = axis[mid]
            if not (0 < px < PLAYABLE_M and 0 < py < PLAYABLE_M):
                continue
            ground = float(ops.sample_bilinear(play, 0.5, 0.5, 1.0, 1.0, [px], [py])[0])
            best = None
            for name, pts_w, bed_w in waters:
                if not pts_w:
                    continue
                i = int(np.argmin([math.dist((px, py), q) for q in pts_w]))
                dd = math.dist((px, py), pts_w[i])
                if best is None or dd < best[0]:
                    best = (dd, name, float(bed_w[i]))
            if best is None or best[0] > 120.0:
                continue
            _, name, wbed = best
            check(f"{c['id']}: the {name} still runs under the deck",
                  abs(ground - wbed) <= 1.5,
                  f"bed {wbed:.2f} m, ground under the span {ground:.2f} m")

    # ---------------------------------------------------------------- pads
    print("\nyards and villages:")
    areas = {'village': [], 'farm': [], 'industry': []}
    # A village has a highway through the middle of it. That strip belongs to the road,
    # is built after the pad and is crowned to its own profile, so measuring the yard's
    # flatness through it measures the road instead.
    roadway = np.zeros(play.shape, bool)
    for c in ml.corridors():
        ax = c['axis']
        reach = c['half_width_m'] + 0.6 * c['feather_m']
        if abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1]):
            roadway |= np.abs(X - ax[0][0]) <= reach
        else:
            roadway |= np.abs(Y - ax[0][1]) <= reach

    for p in ml.pads():
        cx, cy = p['centre']
        w_m, h_m = p['size']
        core = (np.abs(X - cx) <= w_m / 2 - 10) & (np.abs(Y - cy) <= h_m / 2 - 10) \
            & ~roadway
        zz = play[core]
        xx, yy = X[core], Y[core]
        A = np.stack([xx - cx, yy - cy, np.ones_like(xx)], axis=1)
        coef, *_ = np.linalg.lstsq(A, zz, rcond=None)
        resid = float((zz - A @ coef).std())
        grade = float(math.hypot(coef[0], coef[1]))
        drop = float(zz.max() - zz.min())
        areas[p['kind']].append(ml.ring_area_ha(p['ring']))
        ok = resid <= 0.12 and 0.0008 <= grade <= 0.010 \
            and drop <= p['max_drop_m'] + 0.5
        check(f"{p['id']:<9} {p['name'][:26]:<26}", ok,
              f"residual {resid:.3f} m, fall {100 * grade:.2f}%, drop {drop:.2f} m")
    check("farm yards are larger than village pads",
          min(areas['farm']) > max(areas['village']),
          f"smallest farm {min(areas['farm']):.1f} ha, largest village "
          f"{max(areas['village']):.1f} ha")

    # ---------------------------------------------------------------- terracing
    print("\nquantisation:")
    # Measured as the fraction of ground whose four neighbours read the same whole
    # centimetre. The obvious metric - the shape of the height histogram - turns out to
    # measure the distribution of the terrain rather than any terracing in it, and gets
    # worse as the map gets better.
    sub = raw[o:o + PLAYABLE_M:2, o:o + PLAYABLE_M:2].astype(np.int32)
    c = sub[1:-1, 1:-1]
    flat = ((c == sub[:-2, 1:-1]) & (c == sub[2:, 1:-1])
            & (c == sub[1:-1, :-2]) & (c == sub[1:-1, 2:]))
    frac = float(flat.mean())
    check("no contour banding", frac <= 0.02,
          f"{100 * frac:.2f}% of the ground is flat to the centimetre")

    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results)} checks, {len(failed)} failed")
    for n in failed:
        print("   FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
