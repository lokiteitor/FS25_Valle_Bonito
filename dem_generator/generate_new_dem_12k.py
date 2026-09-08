#!/usr/bin/env python3
"""FS25 heightmap generator - the blank base.

Builds the 12288x12288 m canvas (1 px = 1 m) with the 8192x8192 m playable area centred
in it and lays the datum down across all of it: a surface flat at `map_layout.BASE_ELEV_M`
metres, playable area and border alike. That is the sheet the new map gets drawn on.

Everything the terrain would be shaped around - where the water runs, where the roads
are, where the yards sit - comes from `map_layout.py` at the root of the tree, which the
OSM generator reads too. Neither half invents its own geometry. The layout is empty
today, so there is nothing to sculpt and the surface comes out as the datum itself; if
features are added there without the matching sculpting stages being brought back here,
`sculpt()` stops the build rather than quietly shipping a heightmap that disagrees with
the vectors.

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

MASTER_SEED = ml.SEED
# Named streams with fixed, spaced indices: adding one later must not shift the streams
# that already exist, or the whole terrain changes underneath you.
STREAMS = {'micro': 40, 'dither': 41}

STATS_GRID = 128                      # terrain_stats.json resolution


def rng_for(name):
    return np.random.default_rng([MASTER_SEED, STREAMS[name]])


# ==================================================================================
# the surface
# ==================================================================================
def build_base(X, Y):
    """The datum, everywhere. X and Y are the canvas-metre coordinates of each working
    pixel; they are unused here and are the arguments every sculpting stage takes."""
    return np.full(X.shape, BASE_ELEV_M, dtype=np.float32)


def sculpt(z, X, Y):
    """Where the terrain work goes.

    Nothing to do on an empty layout. When features come back, the build order is
    load-bearing and this is the order:

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
                                land and not against water five metres under it
        5. the rim              last, and by addition (`z + h`), so everything already in
                                the border rides up the flank intact

    It refuses rather than ignoring: a layout that carries features this generator does
    not sculpt is the exact failure the shared-geometry rule exists to prevent - the
    vectors would show a river the ground knows nothing about.
    """
    todo = ml.water() + ml.pads() + ml.corridors()
    if todo:
        raise SystemExit(
            f"!! map_layout carries {len(todo)} feature(s) ({len(ml.water())} water, "
            f"{len(ml.pads())} pads, {len(ml.corridors())} corridors) but this is the "
            "blank-base generator: it only lays the datum down. Bring the sculpting "
            "stages back before adding geometry, or the OSM will draw features the "
            "terrain knows nothing about.")
    return z


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
                color=('#6366F1' if p.get('kind') == 'village' else '#DB2777'), lw=1.2)


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

    print(f"1. Datum at {BASE_ELEV_M:.2f} m ({WORK_PX}x{WORK_PX} working grid, "
          f"{WORK_DX:.0f} m/px)...")
    z = build_base(X, Y)

    print("2. Sculpting...")
    z = sculpt(z, X, Y)
    built = np.zeros_like(z)          # graded ground: none yet

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
