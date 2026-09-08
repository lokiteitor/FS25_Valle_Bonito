#!/usr/bin/env python3
"""Acceptance report for the generated heightmap.

Checks the things that are easy to break and hard to see in the image. On the blank base
that is a short list, and every item on it is one the map still has to pass once terrain
is sculpted back into it: that the canvas is the size and encoding the rest of the
project expects, that the surface really is the datum rather than nearly it, that nothing
touches the floor or the ceiling of the 16-bit range, and that `terrain_stats.json` -
which the parcelling reads instead of re-deriving the terrain - describes the same
surface the PNG does.

Zone masks are rebuilt from `map_layout` through the same primitives the generator used
(`terrain_ops`), because a second opinion about where the valley is would be a report
that passes a heightmap which does not meet the brief. There are no zones on an empty
layout, so what is left is the whole canvas.

Exits non-zero if any check fails, so it can gate the pipeline.

One measurement note that outlives the blank map: slope is measured over a 5 m baseline.
A DEM quantised to the centimetre at one metre a pixel has a pure noise floor near 0.3
degrees in its per-pixel gradient, so measuring pixel to pixel overstates every slope on
the map. And dry land starts one baseline back from any water's edge - a 5 m window
straddling the lip reads the submerged bank off a pixel that is itself dry.
"""
import json
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
                                  BASE_ELEV_M, Z_MAX_CM, STATS_GRID)

SLOPE_BASELINE_M = 5.0
BAND_ROWS = 1024
FLAT_TOL_CM = 0.5             # half a centimetre: the quantisation step is one

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    return ok


def info(name, detail):
    print(f"         {name}   {detail}")


def band(name, value, lo, hi, unit=""):
    return check(name, lo <= value <= hi, f"{value:.2f}{unit} (want {lo}..{hi}{unit})")


def max_slope_deg(raw):
    """The steepest 5 m slope anywhere on the canvas, measured band by band.

    Banded because a float copy of the whole canvas is 600 MB and the Gaussian behind
    `slope_deg` wants another. The bands overlap by four baselines so no slope is missed
    across a seam, and the blur has settled well inside the overlap.
    """
    pad = int(4 * SLOPE_BASELINE_M)
    worst = 0.0
    for r0 in range(0, raw.shape[0], BAND_ROWS):
        a = max(0, r0 - pad)
        b = min(raw.shape[0], r0 + BAND_ROWS + pad)
        z = raw[a:b].astype(np.float32) / 100.0
        s = ops.slope_deg(z, 1.0, baseline_m=SLOPE_BASELINE_M)
        worst = max(worst, float(s[r0 - a:r0 - a + BAND_ROWS].max()))
    return worst


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

    # ---------------------------------------------------------------- the datum
    # "Everything at the base elevation" has to mean every pixel, not the average of
    # them: a mean of 20.00 is exactly what a surface 5 cm either side of the datum
    # reports, and that surface is not a blank sheet.
    print(f"\nthe datum ({BASE_ELEV_M:.2f} m):")
    off = float(np.abs(raw.astype(np.float64) - want_cm).max())
    check(f"every pixel within {FLAT_TOL_CM:.1f} cm of {BASE_ELEV_M:.2f} m",
          off <= FLAT_TOL_CM, f"worst pixel is {off:.2f} cm off")
    check("canvas has no relief at all", hi_cm == lo_cm,
          f"{lo_cm / 100.0:.2f} .. {hi_cm / 100.0:.2f} m")
    check("the border sits at the same height as the playable area",
          float(play_raw.min()) == lo_cm and float(play_raw.max()) == hi_cm,
          "no step at the boundary")
    check("the rim is down", ml.RIM_HEIGHT_M == 0.0,
          f"RIM_HEIGHT_M = {ml.RIM_HEIGHT_M:.0f} m")
    info("headroom", f"{BASE_ELEV_M:.0f} m of cut before the encoding clips at zero, "
                     f"{min(65535.0, Z_MAX_CM) / 100.0 - BASE_ELEV_M:.0f} m of fill "
                     "before the ceiling")

    # ---------------------------------------------------------------- surface
    print(f"\nsurface ({SLOPE_BASELINE_M:.0f} m baseline):")
    worst = max_slope_deg(raw)
    band("steepest slope on the canvas", worst, 0.0, 0.10, " deg")
    info("why the tolerance is not zero",
         "a 5 m Gaussian over a constant field is constant, so a flat canvas measures "
         "exactly 0; the band leaves room for the quantisation step")

    # ---------------------------------------------------------------- stats
    # The parcelling sizes fields off this file rather than re-deriving the terrain. If
    # it and the PNG disagree the two halves of the pipeline are describing different
    # ground, which is invisible in either output on its own.
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
    check("height grid agrees with the PNG",
          float(np.abs(hgt - play_raw.mean() / 100.0).max()) <= 0.01,
          f"worst cell {float(np.abs(hgt - play_raw.mean() / 100.0).max()):.3f} m off")
    check("flat ground reads as zero roughness", float(rgh.max()) == 0.0,
          f"max {float(rgh.max()):.4f}")
    check("the lookup works", rough is not None and rough(ml.HALF_M, ml.HALF_M) == 0.0,
          "roughness at the centre of the map is 0.0")

    # ---------------------------------------------------------------- layout
    print("\nlayout:")
    problems = ml.validate()
    check("map_layout validates", not problems,
          "; ".join(problems) if problems else "no complaints")
    check("nothing sculpted that the vectors do not carry",
          not (ml.water() or ml.pads() or ml.corridors()),
          f"{len(ml.water())} water, {len(ml.pads())} pads, "
          f"{len(ml.corridors())} corridors in the layout")

    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    if failed:
        print("failed:")
        for n in failed:
            print("   -", n)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
