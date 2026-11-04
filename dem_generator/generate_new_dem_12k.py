#!/usr/bin/env python3
"""FS25 Heightmap Generator.

Builds the 12288x12288 m canvas (1 px = 1 m, 16-bit uint16 PNG) with the 8192x8192 m
playable square centred in it.

- Clean baseline: the entire 12288x12288 m canvas starts flat at `map_layout.BASE_ELEV_M` (25.0 m),
  ready to be sculpted by any corridors, pads, water bodies, or border relief defined in `map_layout.py`.
- Output: 16-bit centimetre heightmap (`raw / 100 = metres`), the format Giants Editor imports.
- Purely procedural: does not depend on any external PNG or OSM files.
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

# ==================================================================================
# 1. Canvas Geometry & Constants
# ==================================================================================
CANVAS_M = int(ml.CANVAS_M)
PLAYABLE_M = int(ml.PLAYABLE_M)
OFFSET_M = int(ml.OFFSET_M)

WORK_PX = 3072
WORK_DX = CANVAS_M / WORK_PX          # 4 m per working pixel
BAND_ROWS = 1024                      # output is written in bands to save RAM

BASE_ELEV_M = ml.BASE_ELEV_M          # 25.0 m datum from layout
Z_MAX_CM = 62000.0                    # Giants Editor working ceiling in centimetres

FEATHER_CAP_M = 120.0
BANK_DEG = ml.BANK_DEG

MASTER_SEED = ml.SEED
STREAMS = {
    'rim_spur': 10,
    'rim_rough': 11,
    'micro': 40,
    'dither': 41,
    'terrain': 50
}

MICRO_AMP_M = 0.0                     # surface micro-texture amplitude
MICRO_LAM_M = 14.0
DITHER_CM = 0.0

STATS_GRID = 128                      # resolution of terrain_stats.json
ROUGH_FULL_SCALE = 0.105              # tan(6 deg)


def rng_for(name):
    return np.random.default_rng([MASTER_SEED, STREAMS[name]])


# ==================================================================================
# 2. Base Surface Synthesis (Clay County, Iowa - Des Moines Lobe Till Plain)
# ==================================================================================
def build_base(X, Y):
    """Generate the Iowa glacial till plain & loess swell surface across the canvas."""
    rng = rng_for('terrain')

    # 1. Gentle regional uplift from WSW (lake basin) toward ENE uplands
    u_x = np.clip(X / float(PLAYABLE_M), -0.25, 1.25)
    u_y = np.clip((float(PLAYABLE_M) - Y) / float(PLAYABLE_M), -0.25, 1.25)
    regional_tilt = ml.IOWA_TILT_M * (0.72 * ops.smoothstep(u_x) + 0.28 * ops.smoothstep(u_y))

    # 2. Anisotropic NW-SE Des Moines Lobe glacial moraine swells (-35 deg grain)
    ang = math.radians(ml.MORAINE_GRAIN_DEG)
    ca, sa = math.cos(ang), math.sin(ang)
    u_mor = (X * ca + Y * sa) / ml.MORAINE_STRETCH
    v_mor = -X * sa + Y * ca
    n_moraine = ops.value_noise(u_mor, v_mor, ml.IOWA_MORAINE_LAM_M, rng, CANVAS_M)

    # 3. Intermediate loess swells and gentle prairie pothole swales
    n_loess = ops.value_noise(X, Y, ml.IOWA_LOESS_LAM_M, rng, CANVAS_M)
    n_swale = ops.value_noise(X, Y, ml.IOWA_SWALE_LAM_M, rng, CANVAS_M)

    undulation = (
        ml.IOWA_MORAINE_AMP_M * n_moraine
        + ml.IOWA_LOESS_AMP_M * n_loess
        + ml.IOWA_SWALE_AMP_M * n_swale
    )
    # Shift undulation so the lowest broad troughs sit slightly above 0 relative to BASE_ELEV_M
    delta = regional_tilt + undulation + 4.5

    # 4. Anchor shoreline of western lake smoothly to BASE_ELEV_M (25.0 m)
    for w in ml.water():
        if w.get('shape'):
            sh = w['shape']
            q_lake = ops.ellipse_r(
                X, Y, sh['cx'], sh['cy'], sh['a'], sh['b'],
                sh.get('rot_deg', 0.0), sh.get('harmonics', ())
            )
            w_shore = ops.smootherstep(np.clip((q_lake - 1.03) / 0.45, 0.0, 1.0))
            delta = delta * w_shore

    # 5. Anchor core of spiral hill datum to BASE_ELEV_M while blending outer foot into Iowa relief
    for h in ml.hills():
        q_hill = ops.ellipse_r(
            X, Y, h['cx'], h['cy'], h['a'], h['b'],
            h['rot_deg'], h['harmonics']
        )
        w_hill = ops.smootherstep(np.clip((q_hill - 0.88) / 0.25, 0.0, 1.0))
        delta = delta * w_hill

    return (BASE_ELEV_M + np.maximum(delta, -2.0)).astype(np.float32)


# ==================================================================================
# 3. Non-Playable Border (Royal, Clay County, Iowa - Des Moines Lobe Till Plain)
# ==================================================================================
def rim_crest(X, Y):
    """Calculate crest elevation profile around perimeter for rolling moraines."""
    along = ops.rim_along(X, Y, PLAYABLE_M)
    tau = 2.0 * np.pi
    n_spur = ops.value_noise(X, Y, ml.RIM_SPUR_LAM_M, rng_for('rim_spur'), CANVAS_M)
    n_rough = ops.value_noise(X, Y, ml.RIM_ROUGH_LAM_M, rng_for('rim_rough'), CANVAS_M)

    # Broad, gentle moraine rises
    ridge = (0.35 * np.sin(tau * ml.RIM_RIDGE_LOBES * along)
             + 0.15 * np.sin(tau * ml.RIM_RIDGE_BEAT * along + 1.1)
             + ml.RIM_SPUR_AMP * n_spur + ml.RIM_ROUGH_AMP * n_rough)

    ridge = ridge * (1.0 - (1.0 - ml.RIM_WEST_SMOOTH)
                     * ops.smoothstep((0.5 * PLAYABLE_M - X) / (0.5 * PLAYABLE_M)))
    u = 0.5 + 0.5 * np.tanh(1.5 * ridge)
    peak = ml.RIM_SADDLE_M + (ml.RIM_CREST_M - ml.RIM_SADDLE_M) * u
    sill = ml.RIM_MOUTH_M + ml.RIM_MOUTH_VAR_M * (0.6 * n_spur + 0.4 * n_rough)
    return u, peak, sill


def rim_ramp(X, Y):
    """Calculate ramp distance t and directional weight w."""
    _, t, w = ops.rim_field(X, Y, PLAYABLE_M, ml.RIM_APRON_M, ml.RIM_BACK_M, OFFSET_M)
    return t, w


def build_rim(X, Y, d_river=None):
    """Construct non-playable border relief.

    On the clean baseline (all rim amplitudes zero and RIM_CREST_M == BASE_ELEV_M),
    returns zero lift so the entire canvas stays flat at BASE_ELEV_M (25.0 m).
    """
    if (ml.RIM_CREST_M <= BASE_ELEV_M and ml.RIM_SADDLE_M <= BASE_ELEV_M
            and ml.RIM_MOUTH_M <= BASE_ELEV_M and ml.MORAINE_AMP_M == 0.0
            and ml.SWELL_AMP_M == 0.0):
        return np.zeros(X.shape, dtype=np.float32)

    _, peak, sill = rim_crest(X, Y)
    t, w = rim_ramp(X, Y)
    crest = sill + (peak - sill) * w
    lift = t * np.maximum(crest - BASE_ELEV_M, 0.0)

    # Add Des Moines Lobe moraine grain:
    # Stretched along NW-SE grain (-35 deg)
    a = math.radians(ml.MORAINE_GRAIN_DEG)
    c, sn = math.cos(a), math.sin(a)
    u = (X * c + Y * sn) / ml.MORAINE_STRETCH
    v = -X * sn + Y * c

    n_moraine = ops.value_noise(u, v, ml.MORAINE_LAM_M, rng_for('rim_spur'), CANVAS_M)
    n_swell = ops.value_noise(X, Y, ml.SWELL_LAM_M, rng_for('rim_rough'), CANVAS_M)
    moraine_relief = t * (ml.MORAINE_AMP_M * n_moraine + ml.SWELL_AMP_M * n_swell)

    total_lift = np.maximum(lift + moraine_relief, 0.0)

    if d_river is not None:
        total_lift = total_lift * ops.smoothstep((d_river - ml.RIVER_NOTCH_HALF_M)
                                                 / ml.RIVER_NOTCH_FEATHER_M)
    return total_lift


# ==================================================================================
# 4. Feature Grading: Pads, Corridors, Water
# ==================================================================================
def grade_pads(z, X, Y, wet):
    """Level platforms for farmyards and building pads."""
    tan_bank = math.tan(math.radians(BANK_DEG))
    built = np.zeros(z.shape, dtype=np.float32)

    for p in ml.pads():
        cx, cy = p['centre']
        w, h = p['size']
        d = ops.rect_sdf(X, Y, cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        on = (d <= 0.0) & (wet < 0.25)
        if not bool(on.any()):
            continue

        sy = np.clip(cy - Y, -h / 2.0, h / 2.0)
        target = (float(np.median(z[on])) + p.get('drain_grade', 0.003) * sy).astype(np.float32)
        dz = target - z
        dry_dz = np.where(wet > 0.25, 0.0, dz)
        feather = np.clip(1.5 * np.abs(dry_dz) / tan_bank, p.get('feather_m', 20.0), FEATHER_CAP_M)
        wgt = (1.0 - ops.smoothstep(d / feather)) * (1.0 - np.clip(wet, 0.0, 1.0))
        z = z + wgt * dz
        np.maximum(built, ops.smoothstep(-d / 8.0), out=built)

    return z, built


def grade_corridors(z, X, Y, wet):
    """Cut and grade road and railway corridors into the terrain."""
    tan_bank = math.tan(math.radians(BANK_DEG))
    x0, y0 = float(X[0, 0]), float(Y[0, 0])
    dx = float(X[0, 1] - X[0, 0])
    built = np.zeros(z.shape, dtype=np.float32)
    rank = {'track': 0, 'street': 0, 'tertiary': 1, 'section': 1, 'secondary': 2, 'rail': 2, 'primary': 3}

    for c in sorted(ml.corridors(), key=lambda c: rank.get(c['kind'], 0)):
        dense = ml.densify(c['axis'], 20.0)
        arc = ops.polyline_arclen(dense)
        xs = np.array([p[0] for p in dense])
        ys = np.array([p[1] for p in dense])
        ground = ops.sample_bilinear(z, x0, y0, dx, dx, xs, ys)

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

        w = w * (1.0 - np.clip(wet, 0.0, 1.0))
        for s0, s1 in c.get('bridge_spans', ()):
            w = w * (1.0 - ops.smoothstep((sarc - s0) / 15.0)
                     * ops.smoothstep((s1 - sarc) / 15.0))
        z = z + w * dz
        np.maximum(built, ops.smoothstep((half + 0.5 * c['feather_m'] - d) / 8.0), out=built)

    return z, built


def water_fields(X, Y, land):
    """Carve water bodies and channels."""
    if not ml.water():
        return np.zeros(X.shape, dtype=np.float32), land, None

    wet = np.zeros(X.shape, dtype=np.float32)
    water_z = land.copy()
    d_river = None

    for w in ml.water():
        if w.get('shape'):
            sh = w['shape']
            q = ops.ellipse_r(
                X, Y, sh['cx'], sh['cy'], sh['a'], sh['b'],
                sh.get('rot_deg', 0.0), sh.get('harmonics', ())
            )
            depth = float(w.get('depth_m', 15.0))
            t_basin = np.clip((q - 0.35) / (1.0 - 0.35), 0.0, 1.0)
            carve = depth * (1.0 - ops.smootherstep(t_basin))
            water_z = np.minimum(water_z, land - carve)
            wet = np.maximum(wet, ops.smootherstep((1.02 - q) / 0.04))
        if w.get('axis'):
            axis = w['axis']
            hw = w.get('water_half_w', 20.0)
            d, _ = ops.polyline_field(X, Y, axis, hw + 100.0)
            if d_river is None:
                d_river = d.copy()
            else:
                d_river = np.minimum(d_river, d)
            depth = w.get('depth_m', 3.0)
            channel_mask = d < hw
            wet = np.maximum(wet, (d < hw).astype(np.float32))
            carve = depth * (1.0 - ops.smoothstep(d / hw))
            water_z = np.minimum(water_z, land - carve)

    return wet, water_z, d_river


def build_hills(z, X, Y):
    """Sculpt natural hills with a continuous drivable spiral terrace to the summit."""
    built = np.zeros(z.shape, dtype=np.float32)
    if not ml.hills():
        return z, built

    rng = rng_for('terrain')
    n_broad = ops.value_noise(X, Y, 300.0, rng, CANVAS_M)
    n_fine = ops.value_noise(X, Y, 120.0, rng, CANVAS_M)

    for h in ml.hills():
        cx, cy = h['cx'], h['cy']
        a, b = h['a'], h['b']
        rot_deg = h['rot_deg']
        harmonics = h['harmonics']
        q_start = h['q_start']
        q_top = h['q_top']
        height = h['height_m']
        turns = h['spiral_turns']
        theta_start = h['theta_start']
        w = h['shelf_frac']

        c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
        u_rot = (X - cx) * c + (Y - cy) * s
        v_rot = -(X - cx) * s + (Y - cy) * c
        theta = np.arctan2(v_rot / b, u_rot / a)
        q = ops.ellipse_r(X, Y, cx, cy, a, b, rot_deg, harmonics)

        u_hill = np.clip((q_start - q) / (q_start - q_top), 0.0, 1.0)
        nu = turns * u_hill
        f = np.mod(nu - (theta - theta_start) / (2.0 * np.pi), 1.0)

        span = 0.5 - w
        t1 = np.clip(f / span, 0.0, 1.0)
        t2 = np.clip((f - (0.5 + w)) / span, 0.0, 1.0)
        S = np.where(f < 0.5, 0.5 * ops.smoothstep(t1), 0.5 + 0.5 * ops.smoothstep(t2))

        env = ops.smootherstep(u_hill / 0.14) * ops.smootherstep((1.0 - u_hill) / 0.14)
        u_eff = np.clip(u_hill + env * (S - f) / turns, 0.0, 1.0)

        prof = 0.65 * ops.smoothstep(u_eff) + 0.35 * ops.smootherstep(u_eff)

        on_shelf = ops.smoothstep((w + 0.03 - np.abs(f - 0.5)) / 0.03) * env
        flank_env = (1.0 - on_shelf) * (4.0 * u_hill * (1.0 - u_hill)) ** 1.5
        flank_noise = flank_env * (1.8 * n_broad + 0.8 * n_fine)

        z_hill = z + height * prof + flank_noise
        z = np.maximum(z, z_hill)
        np.maximum(built, on_shelf, out=built)

    return z, built


def sculpt(z, X, Y):
    """Execute all terrain sculpting stages in order."""
    wet = np.zeros(z.shape, dtype=np.float32)
    d_river = None

    if ml.water():
        wet, water_z, d_river = water_fields(X, Y, z)
        z = np.minimum(z, water_z)

    if ml.hills():
        z, built_hills = build_hills(z, X, Y)
    else:
        built_hills = np.zeros(z.shape, dtype=np.float32)

    if ml.pads():
        z, built_pads = grade_pads(z, X, Y, wet)
    else:
        built_pads = np.zeros(z.shape, dtype=np.float32)

    if ml.corridors():
        z, built = grade_corridors(z, X, Y, wet)
    else:
        built = np.zeros(z.shape, dtype=np.float32)

    np.maximum(built, built_pads, out=built)
    np.maximum(built, built_hills, out=built)
    return z + build_rim(X, Y, d_river), wet, built


# ==================================================================================
# 5. Output Writers & Previews
# ==================================================================================
def write_stats(z, X, Y, path):
    """Write coarse height and roughness grid for OSM generation."""
    p0 = int(OFFSET_M / WORK_DX)
    p1 = int((OFFSET_M + PLAYABLE_M) / WORK_DX)
    play = z[p0:p1, p0:p1]
    slope = np.tan(np.radians(ops.slope_deg(play, WORK_DX, baseline_m=40.0)))
    k = play.shape[0] // STATS_GRID
    hgt = play.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    slp = slope.reshape(STATS_GRID, k, STATS_GRID, k).mean(axis=(1, 3))
    rough = np.clip(slp / ROUGH_FULL_SCALE, 0.0, 1.0)

    with open(path, 'w') as fh:
        json.dump({
            'n': STATS_GRID,
            'cell_m': PLAYABLE_M / STATS_GRID,
            'origin': [0.0, 0.0],
            'height': [round(float(v), 2) for v in hgt.ravel()],
            'roughness': [round(float(v), 4) for v in rough.ravel()]
        }, fh, indent=2)


def write_dem(z_work, built_work, out_path):
    """Resample to 1 m/px (12288x12288) and quantise to 16-bit uint16 PNG."""
    n = CANVAS_M
    out = np.empty((n, n), dtype=np.uint16)
    cols = (np.arange(n, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX

    for b in range(n // BAND_ROWS):
        r0, r1 = b * BAND_ROWS, (b + 1) * BAND_ROWS
        rows = (np.arange(r0, r1, dtype=np.float32) + 0.5 - WORK_DX * 0.5) / WORK_DX
        coords = np.stack(np.broadcast_arrays(rows[:, None], cols[None, :]))
        band = ndimage.map_coordinates(z_work, coords, order=3, mode='nearest',
                                       output=np.float32)
        band *= 100.0  # metres to centimetres
        np.clip(band, 0.0, Z_MAX_CM, out=band)
        out[r0:r1] = np.rint(band).astype(np.uint16)

    Image.fromarray(out).save(out_path)
    return out


def style(ax, title):
    ax.set_xlabel("X (East-West) [metres]", fontsize=11, fontweight='bold', color='white')
    ax.set_ylabel("Y (North-South) [metres]", fontsize=11, fontweight='bold', color='white')
    ax.grid(True, which='both', color='white', linestyle='--', linewidth=0.5, alpha=0.35)
    ax.tick_params(colors='white')
    for spine in ax.spines.values():
        spine.set_color('white')
    ax.set_title(title, fontsize=15, fontweight='bold', pad=14, color='white')


def shade(sub, vmin, vmax):
    ls = LightSource(azdeg=315, altdeg=45)
    return ls.shade(sub, cmap=plt.get_cmap('terrain'), blend_mode='overlay',
                    vert_exag=2.0, vmin=vmin, vmax=vmax)


def draw_figures(raw, out_vis, out_detail):
    """Generate 2D visual previews of canvas and playable square."""
    n = CANVAS_M
    k = n // 1024
    vis = raw.reshape(1024, k, 1024, k).mean(axis=(1, 3)) / 100.0
    vmin, vmax = np.percentile(vis, 0.5), np.percentile(vis, 99.5)
    if vmax - vmin < 1e-6:
        vmin, vmax = vmin - 0.5, vmax + 0.5

    fig, ax = plt.subplots(figsize=(11, 11), dpi=150)
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    ax.imshow(shade(vis, vmin, vmax), extent=[0, n, n, 0])
    im = ax.imshow(vis, extent=[0, n, n, 0], cmap='terrain', vmin=vmin, vmax=vmax, alpha=0.0)
    ax.set_xticks(np.arange(0, n + 1, 1024))
    ax.set_yticks(np.arange(0, n + 1, 1024))
    style(ax, f"Full DEM Canvas ({n}x{n} px, 1 px = 1 m)")
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
    vmin_d, vmax_d = float(sub.min()), float(sub.max())
    if vmax_d - vmin_d < 1e-6:
        vmin_d, vmax_d = vmin_d - 0.5, vmax_d + 0.5

    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    ax.imshow(shade(sub, vmin_d, vmax_d), extent=[0, PLAYABLE_M, PLAYABLE_M, 0])
    im2 = ax.imshow(sub, extent=[0, PLAYABLE_M, PLAYABLE_M, 0], cmap='terrain',
                    vmin=vmin_d, vmax=vmax_d, alpha=0.0)
    ax.set_xticks(np.arange(0, PLAYABLE_M + 1, 1024))
    ax.set_yticks(np.arange(0, PLAYABLE_M + 1, 1024))
    style(ax, f"Playable Square ({PLAYABLE_M}x{PLAYABLE_M} m)")
    cb2 = fig.colorbar(im2, ax=ax, fraction=0.046, pad=0.04)
    cb2.set_label("height [m]", color='white')
    cb2.ax.tick_params(colors='white')
    cb2.outline.set_edgecolor('white')
    plt.savefig(out_detail, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()


# ==================================================================================
# 6. Main Pipeline
# ==================================================================================
def main():
    t_start = time.time()
    print(f"=== FS25 DEM Generator ({CANVAS_M}x{CANVAS_M} m canvas, "
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

    ax1 = ops.work_axis(WORK_PX, WORK_DX, OFFSET_M)
    X, Y = np.meshgrid(ax1, ax1)

    print(f"1. Base terrain at datum {BASE_ELEV_M:.1f} m ({WORK_PX}x{WORK_PX} grid, {WORK_DX:.0f} m/px)...")
    z = build_base(X, Y)

    print(f"2. Sculpting features & border (rim target {ml.RIM_CREST_M:.1f} m)...")
    z, wet, built = sculpt(z, X, Y)
    print(f"   {float(wet.mean()) * 100:.2f}% under water, "
          f"{float((built > 0.5).mean()) * 100:.2f}% graded for roads/pads")

    print(f"3. Resampling to {CANVAS_M}x{CANVAS_M} (1 px = 1 m) and writing 16-bit PNG...")
    raw = write_dem(z, built, out_dem)

    print("4. Publishing terrain_stats.json...")
    write_stats(z, X, Y, out_stats)

    print("5. Generating preview figures...")
    draw_figures(raw, out_vis, out_detail)

    lo, hi = raw.min() / 100.0, raw.max() / 100.0
    play = raw[OFFSET_M:OFFSET_M + PLAYABLE_M, OFFSET_M:OFFSET_M + PLAYABLE_M]
    print(f"\n   Canvas: {lo:.2f} .. {hi:.2f} m | Playable: "
          f"{play.min() / 100.0:.2f} .. {play.max() / 100.0:.2f} m (relief {hi - lo:.2f} m)")
    print(f"   [+] {out_dem}")
    print(f"   [+] {out_stats}")
    print(f"   [+] {out_vis}")
    print(f"   [+] {out_detail}")
    print(f"   done in {time.time() - t_start:.1f} s")
    return 0


if __name__ == '__main__':
    sys.exit(main())
