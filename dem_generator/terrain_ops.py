#!/usr/bin/env python3
"""Numeric primitives for building and for measuring the heightmap.

Both `generate_new_dem_12k.py` and `measure_elevation.py` use these. That is the point:
the measurer has to rebuild exactly the same zone masks the generator used, and a second
implementation of "where is the valley" is the shortest route to a report that passes a
heightmap which does not actually meet the brief.

Everything works in metres. The working grid is coarser than the output - see the
generator for why - so every function takes the grid's origin and pitch rather than
assuming one.
"""
import math

import numpy as np
from scipy import ndimage


# ----------------------------------------------------------------------------------
# grids
# ----------------------------------------------------------------------------------
def work_axis(n, dx, offset_m):
    """Playable-metre coordinate of each working column.

    The canvas metre of working pixel j is `dx*j + dx/2`; playable coordinates are canvas
    minus the margin. Getting this wrong shifts the terrain against the vectors by half a
    cell, which is invisible in the image and wrong everywhere.
    """
    return (np.arange(n, dtype=np.float32) * dx + dx * 0.5 - offset_m)


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def soft_min(a, b, k):
    """Polynomial smooth minimum. C1 everywhere, and equal to min(a, b) once the two are
    more than k apart.

    Carving with this instead of blending is what makes a river valley look like a
    valley: outside it the ground is left exactly as it was, and the valley rim falls
    where the valley surface cuts the natural surface - wider across low ground, pinched
    where a rise comes down to the water - rather than at a fixed offset from the
    centreline.
    """
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b * (1.0 - h) + a * h - k * h * (1.0 - h)


# ----------------------------------------------------------------------------------
# noise
# ----------------------------------------------------------------------------------
def value_noise(cx, cy, lam, rng, span_m):
    """Cubic-spline value noise at wavelength `lam`, normalised to unit variance.

    `cx`, `cy` are coordinates in metres. The normalisation is not cosmetic: the spline
    prefilter amplifies white noise by an amount that depends on the wavelength, so
    without it an "amplitude in metres" would mean nothing.
    """
    ny = int(span_m / lam) + 4
    lattice = rng.standard_normal((ny, ny)).astype(np.float32)
    coords = np.stack([cy / lam, cx / lam])
    f = ndimage.map_coordinates(lattice, coords, order=3, mode='grid-wrap',
                                prefilter=True, output=np.float32)
    s = float(f.std())
    return f / (s if s > 1e-9 else 1.0)


# ----------------------------------------------------------------------------------
# polylines
# ----------------------------------------------------------------------------------
def polyline_field(X, Y, pts, r_max):
    """Exact distance to a polyline, plus the arc length of the nearest point.

    A rasterised mask through `distance_transform_edt` would be faster but gives no arc
    length, and quantises the distance to the grid pitch - a 15% error across a channel
    this narrow. Windowing each segment to its own bounding box keeps the exact version
    cheap.
    """
    d = np.full(X.shape, np.float32(1e9))
    s = np.zeros(X.shape, np.float32)
    x0 = float(X[0, 0])
    y0 = float(Y[0, 0])
    dx = float(X[0, 1] - X[0, 0])
    dy = float(Y[1, 0] - Y[0, 0])
    h, w = X.shape
    acc = 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        seg = math.hypot(bx - ax, by - ay)
        if seg < 1e-9:
            continue
        c0 = int(max(0, math.floor((min(ax, bx) - r_max - x0) / dx)))
        c1 = int(min(w - 1, math.ceil((max(ax, bx) + r_max - x0) / dx)))
        r0 = int(max(0, math.floor((min(ay, by) - r_max - y0) / dy)))
        r1 = int(min(h - 1, math.ceil((max(ay, by) + r_max - y0) / dy)))
        if c1 < c0 or r1 < r0:
            acc += seg
            continue
        sl = (slice(r0, r1 + 1), slice(c0, c1 + 1))
        xx, yy = X[sl], Y[sl]
        t = np.clip(((xx - ax) * (bx - ax) + (yy - ay) * (by - ay)) / (seg * seg),
                    0.0, 1.0)
        dd = np.hypot(xx - (ax + t * (bx - ax)), yy - (ay + t * (by - ay)))
        cur = d[sl]
        upd = dd < cur
        cur[upd] = dd[upd]
        ss = s[sl]
        ss[upd] = (acc + t * seg)[upd]
        acc += seg
    return d, s


def polyline_arclen(pts):
    out = [0.0]
    for i in range(len(pts) - 1):
        out.append(out[-1] + math.dist(pts[i], pts[i + 1]))
    return np.asarray(out, dtype=np.float64)


def sample_bilinear(z, X0, Y0, dx, dy, xs, ys):
    """Sample a grid at arbitrary metre coordinates."""
    cols = (np.asarray(xs, dtype=np.float64) - X0) / dx
    rows = (np.asarray(ys, dtype=np.float64) - Y0) / dy
    return ndimage.map_coordinates(z, np.stack([rows, cols]), order=1, mode='nearest')


# ----------------------------------------------------------------------------------
# longitudinal profiles
# ----------------------------------------------------------------------------------
def lower_envelope(z, ds, g):
    """Largest g-Lipschitz function that stays at or below z."""
    out = np.asarray(z, dtype=np.float64).copy()
    step = g * ds
    for i in range(1, out.size):
        out[i] = min(out[i], out[i - 1] + step)
    for i in range(out.size - 2, -1, -1):
        out[i] = min(out[i], out[i + 1] + step)
    return out


def upper_envelope(z, ds, g):
    """Smallest g-Lipschitz function that stays at or above z."""
    out = np.asarray(z, dtype=np.float64).copy()
    step = g * ds
    for i in range(1, out.size):
        out[i] = max(out[i], out[i - 1] - step)
    for i in range(out.size - 2, -1, -1):
        out[i] = max(out[i], out[i + 1] - step)
    return out


def limit_grade(z, ds, g):
    """A profile with |dz/ds| <= g, balanced between cut and fill.

    The mean of the two Lipschitz envelopes. Clipping the slope forwards and then
    backwards, the obvious approach, is not idempotent and drags the whole profile
    downhill; the lower envelope alone would put the road permanently in cut and the
    upper envelope alone permanently on embankment.
    """
    return 0.5 * (lower_envelope(z, ds, g) + upper_envelope(z, ds, g))


def smooth_1d(z, sigma_samples):
    if sigma_samples <= 0:
        return np.asarray(z, dtype=np.float64)
    return ndimage.gaussian_filter1d(np.asarray(z, dtype=np.float64),
                                     sigma_samples, mode='nearest')


def monotone_descent(z_raw, ds, g_min, g_max):
    """Force a strictly falling profile with the gradient inside [g_min, g_max].

    One pass downstream. The clip guarantees each sample sits below the last, so the
    water runs; the bounds keep it from either pooling or turning into a waterfall.
    """
    out = np.asarray(z_raw, dtype=np.float64).copy()
    for i in range(1, out.size):
        lo = out[i - 1] - g_max * ds
        hi = out[i - 1] - g_min * ds
        out[i] = min(max(out[i], lo), hi)
    return out


# ----------------------------------------------------------------------------------
# shapes
# ----------------------------------------------------------------------------------
def rect_sdf(X, Y, x0, y0, x1, y1):
    """Signed distance to an axis-aligned rectangle: negative inside."""
    dx = np.maximum(x0 - X, X - x1)
    dy = np.maximum(y0 - Y, Y - y1)
    outside = np.hypot(np.maximum(dx, 0.0), np.maximum(dy, 0.0))
    inside = np.minimum(np.maximum(dx, dy), 0.0)
    return outside + inside


def ellipse_r(X, Y, cx, cy, a, b, rot_deg, harmonics=()):
    """Normalised elliptical radius: 1 on the shore.

    `harmonics` are (amplitude, lobes, phase) triples modulating the radius, so the
    shoreline is a lobed kettle rather than a drawn ellipse. They have to be the same
    ones the vector ring uses or the water will not sit in its own basin.
    """
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    u = (X - cx) * c + (Y - cy) * s
    v = -(X - cx) * s + (Y - cy) * c
    q = np.hypot(u / a, v / b)
    if not harmonics:
        return q
    theta = np.arctan2(v / b, u / a)
    m = np.ones_like(q)
    for amp, k, ph in harmonics:
        m = m + amp * np.sin(k * theta + ph)
    return q / m


# ----------------------------------------------------------------------------------
# measurement
# ----------------------------------------------------------------------------------
def slope_deg(z, dx, baseline_m=5.0):
    """Slope in degrees, measured over a baseline rather than pixel to pixel.

    A DEM quantised to the centimetre at one metre a pixel has a pure noise floor of
    about 0.29 degrees in its per-pixel gradient. Measuring that way on real relief
    overstates every slope on the map; a 5 m baseline is both more honest and closer to
    what a machine actually drives over.
    """
    sigma = max(0.6, (baseline_m / dx) / 3.0)
    zs = ndimage.gaussian_filter(z.astype(np.float32), sigma)
    gy, gx = np.gradient(zs, dx)
    return np.degrees(np.arctan(np.hypot(gx, gy)))


def limit_slope(z, dx, max_deg, iters=12, relax=0.60, blur_px=6.0):
    """Diffuse the ground wherever it is steeper than `max_deg`.

    The mask is a smoothstep of how far over the limit each pixel is, and is itself
    blurred, so there is no contour where the correction jumps from off to on. The update
    is `z += a*m*(blur(z) - z)`, which is diffusion: it can only reduce curvature, so it
    cannot introduce the step a hard threshold would.
    """
    lim = math.tan(math.radians(max_deg))
    out = z
    for _ in range(iters):
        gy, gx = np.gradient(out, dx)
        excess = np.hypot(gx, gy) - lim
        if float(excess.max()) < 1e-4:
            break
        m = smoothstep(excess / (0.30 * lim))
        m = ndimage.gaussian_filter(m.astype(np.float32), 3.0)
        out = out + relax * m * (ndimage.gaussian_filter(out, blur_px) - out)
    return out
