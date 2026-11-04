#!/usr/bin/env python3
"""The layout of the map: where everything is, in playable metres.

This is the single source of truth shared by both halves of the pipeline:
- The DEM generator sculpts terrain around the geometry defined here.
- The OSM generator writes out the same geometry as vector data for Giants Editor.

Neither half may define geometry of its own.

Coordinates are playable metres:
- x east, y south from the north edge, so the centre of the playable area is (4096, 4096).
- Playable area: 8192 x 8192 m.
- Canvas area: 12288 x 12288 m (2048 m margin on all sides).
- Standard library only: nothing here imports numpy or external dependencies.
"""
import json
import math
import os
import random

# ==================================================================================
# 1. Map Coordinates & Canvas Dimensions
# ==================================================================================
# The projection anchor (geographic centre of the map)
LAT_CENTER = 43.14569235
LON_CENTER = -95.14507865

# Map dimensions (in metres)
PLAYABLE_M = 8192.0
HALF_M = PLAYABLE_M / 2.0
CANVAS_M = 12288.0
OFFSET_M = (CANVAS_M - PLAYABLE_M) / 2.0      # 2048 m margin on each side

# Extension for alignments leaving the canvas
EXTEND_M = 300.0
EDGE_MIN = -OFFSET_M - EXTEND_M               # -2348 m
EDGE_MAX = PLAYABLE_M + OFFSET_M + EXTEND_M   # 10540 m

# A clean strip inside the playable boundary: no fields/buildings in this margin
EDGE_CLEAR_M = 50.0

# Base elevation datum (16-bit DEM units: raw / 100 = metres)
BASE_ELEV_M = 25.0

# ==================================================================================
# 2. Iowa Geomorphology & Non-Playable Border (Des Moines Lobe / NW Iowa Plains)
# ==================================================================================
# Playable area relief (Clay County, Iowa glacial till plain & loess swells)
IOWA_TILT_M = 11.0            # regional WSW -> ENE uplift across playable area (m)
IOWA_MORAINE_AMP_M = 6.5      # NW-SE anisotropic glacial moraine undulations (m)
IOWA_MORAINE_LAM_M = 2400.0   # primary moraine wavelength (m)
IOWA_LOESS_AMP_M = 2.8        # intermediate loess swells amplitude (m)
IOWA_LOESS_LAM_M = 950.0      # intermediate loess wavelength (m)
IOWA_SWALE_AMP_M = 0.9        # gentle prairie swale / pothole relief (m)
IOWA_SWALE_LAM_M = 380.0      # swale wavelength (m)

# Non-playable perimeter moraine horizon
RIM_APRON_M = 100.0           # flat transition apron beyond boundary
RIM_BACK_M = 150.0            # shoulder inside canvas edge
RIM_CREST_M = 54.0            # moraine ridge crest on horizon (m)
RIM_SADDLE_M = 36.0           # low moraine saddle (m)
RIM_MOUTH_M = 28.0            # valley mouth sill elevation (m)
RIM_MOUTH_VAR_M = 2.5         # natural sill variance (m)
RIM_RIDGE_LOBES = 9           # perimeter lobes
RIM_RIDGE_BEAT = 15           # secondary harmonic
RIM_SPUR_LAM_M = 1800.0       # spur wavelength
RIM_SPUR_AMP = 0.25           # spur modulation amplitude
RIM_ROUGH_LAM_M = 800.0       # secondary wavelength
RIM_ROUGH_AMP = 0.15          # secondary roughness amplitude
RIM_WEST_SMOOTH = 0.25        # keep western lake exit low and open

# Glacial moraine / border relief characteristics (Des Moines Lobe NW-SE grain)
MORAINE_GRAIN_DEG = -35.0
MORAINE_STRETCH = 2.6
MORAINE_AMP_M = 6.0
MORAINE_LAM_M = 2800.0
SWELL_AMP_M = 2.5
SWELL_LAM_M = 1000.0

RIM_HEIGHT_M = RIM_CREST_M - BASE_ELEV_M
RIM_RAMP_M = OFFSET_M - RIM_APRON_M - RIM_BACK_M
RIM_PERIM_M = 4.0 * PLAYABLE_M
RIM_RIDGE_LAM_M = RIM_PERIM_M / RIM_RIDGE_LOBES
RIM_MAX_FLANK_DEG = 6.0

# Notch dimensions for rivers cutting through rim (if any)
RIVER_NOTCH_HALF_M = 120.0
RIVER_NOTCH_FEATHER_M = 200.0

# ==================================================================================
# 3. Projection & Units
# ==================================================================================
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))
SEED = 20250902

# Platform and corridor standards
TOWN_PAD_FEATHER_M = 25.0
TOWN_DRAIN_GRADE = 0.003
YARD_FEATHER_M = 20.0
YARD_DRAIN_GRADE = 0.003
BANK_DEG = 4.0

HIGHWAY_CLASS = {
    'primary': 'primary',
    'secondary': 'secondary',
    'tertiary': 'tertiary',
    'section': 'secondary',
    'track': 'track',
    'street': 'tertiary'
}

# Closed vocabulary of OSM tags recognized by FS25 pipelines and renderers
RENDERED_TAGS = (
    ('natural', 'water'),
    ('water', None),
    ('natural', 'wood'),
    ('landuse', 'forest'),
    ('landuse', 'farmyard'),
    ('landuse', 'farmland'),
    ('railway', None),
    ('highway', None)
)


# ==================================================================================
# 4. Geometry & Coordinate Utilities
# ==================================================================================
def local_to_global(x, y):
    """Playable metres -> (lat, lon). y grows southwards."""
    return (LAT_CENTER - (y - HALF_M) / M_PER_DEG,
            LON_CENTER + (x - HALF_M) / M_PER_DEG_LON)


def global_to_local(lat, lon):
    """(lat, lon) -> playable metres. Inverse of local_to_global."""
    return (HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON,
            HALF_M - (lat - LAT_CENTER) * M_PER_DEG)


def bounds():
    """OSM <bounds> element: (minlat, minlon, maxlat, maxlon)."""
    minlat, minlon = local_to_global(0.0, PLAYABLE_M)
    maxlat, maxlon = local_to_global(PLAYABLE_M, 0.0)
    return minlat, minlon, maxlat, maxlon


def to_canvas(x, y):
    """Playable metres -> canvas metres."""
    return x + OFFSET_M, y + OFFSET_M


def from_canvas(xc, yc):
    """Canvas metres -> playable metres."""
    return xc - OFFSET_M, yc - OFFSET_M


def polyline_length(pts):
    """Length of a polyline in metres."""
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def ring_area_ha(ring):
    """Area of a closed ring in hectares (1 ha = 10,000 m^2)."""
    n = len(ring)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n - 1):
        a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(a) * 0.5 / 10000.0


def ring_perimeter(ring):
    """Perimeter of a closed ring in metres."""
    return sum(math.dist(ring[i], ring[i + 1]) for i in range(len(ring) - 1))


def close_ring(ring):
    """Ensure ring closes on its first point."""
    pts = list(ring)
    if not pts:
        return pts
    if math.dist(pts[0], pts[-1]) > 1e-6:
        pts.append(pts[0])
    return pts


def rect_ring(x0, y0, x1, y1):
    """Counter-clockwise closed rectangle ring."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def clip_ring_to_rect(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman polygon clipping to an axis-aligned box."""
    poly = ring[:-1] if (len(ring) > 1 and ring[0] == ring[-1]) else list(ring)

    def clip_edge(p, inside_fn, intersect_fn):
        out = []
        if not p:
            return out
        s = p[-1]
        for c in p:
            if inside_fn(c):
                if not inside_fn(s):
                    out.append(intersect_fn(s, c))
                out.append(c)
            elif inside_fn(s):
                out.append(intersect_fn(s, c))
            s = c
        return out

    def _cut_x(a, b, x):
        dx = b[0] - a[0]
        t = 0.0 if abs(dx) < 1e-12 else (x - a[0]) / dx
        return (x, a[1] + t * (b[1] - a[1]))

    def _cut_y(a, b, y):
        dy = b[1] - a[1]
        t = 0.0 if abs(dy) < 1e-12 else (y - a[1]) / dy
        return (a[0] + t * (b[0] - a[0]), y)

    poly = clip_edge(poly, lambda pt: pt[0] >= x0, lambda a, b: _cut_x(a, b, x0))
    poly = clip_edge(poly, lambda pt: pt[0] <= x1, lambda a, b: _cut_x(a, b, x1))
    poly = clip_edge(poly, lambda pt: pt[1] >= y0, lambda a, b: _cut_y(a, b, y0))
    poly = clip_edge(poly, lambda pt: pt[1] <= y1, lambda a, b: _cut_y(a, b, y1))

    if not poly:
        return []
    poly.append(poly[0])
    return poly


def ellipse_ring(cx, cy, a, b, rot_deg=0.0, n=48, harmonics=()):
    """Sample an ellipse (optionally modulated by radial harmonics) into a closed polygon ring.

    Matches `terrain_ops.ellipse_r` so the vector shoreline sits on the r = 1 level set
    of the sculpted lake basin.
    """
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    pts = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        m = 1.0
        for amp, k, ph in harmonics:
            m += amp * math.sin(k * th + ph)
        u = a * m * math.cos(th)
        v = b * m * math.sin(th)
        pts.append((cx + u * cos_r - v * sin_r,
                    cy + u * sin_r + v * cos_r))
    pts.append(pts[0])
    return pts


def point_in_ring(pt, ring):
    """Ray casting point-in-polygon test."""
    x, y = pt
    inside = False
    n = len(ring)
    for i in range(n - 1):
        xi, yi = ring[i]
        xj, yj = ring[i + 1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
    return inside


def segs_cross(a, b, c, d):
    """True if open line segments a-b and c-d cross."""
    def ccw(p1, p2, p3):
        return (p3[1] - p1[1]) * (p2[0] - p1[0]) > (p2[1] - p1[1]) * (p3[0] - p1[0])
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def rings_overlap(p, q):
    """True if two closed rings overlap."""
    if point_in_ring(p[0], q) or point_in_ring(q[0], p):
        return True
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            if segs_cross(p[i], p[i + 1], q[j], q[j + 1]):
                return True
    return False


def ring_is_simple(ring):
    """True if closed ring does not self-intersect."""
    n = len(ring) - 1
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if segs_cross(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                return False
    return True


def seg_point_dist(p, a, b):
    """Distance from point p to segment a-b."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def dist_to_polyline(p, pts):
    """Minimum distance from point p to polyline pts."""
    return min(seg_point_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def densify(pts, step):
    """Subdivide segments longer than step."""
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        d = math.dist(a, b)
        if d > step:
            n = int(math.ceil(d / step))
            for k in range(1, n):
                t = k / n
                out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        out.append(b)
    return out


def offset_polyline(pts, dist):
    """Offset polyline along segment normals."""
    n = len(pts)
    if n < 2:
        return list(pts)
    normals = []
    for i in range(n - 1):
        dx = pts[i + 1][0] - pts[i][0]
        dy = pts[i + 1][1] - pts[i][1]
        l = math.hypot(dx, dy) or 1.0
        normals.append((-dy / l, dx / l))
    out = [(pts[0][0] + dist * normals[0][0], pts[0][1] + dist * normals[0][1])]
    for i in range(1, n - 1):
        nx = 0.5 * (normals[i - 1][0] + normals[i][0])
        ny = 0.5 * (normals[i - 1][1] + normals[i][1])
        l = math.hypot(nx, ny) or 1.0
        out.append((pts[i][0] + dist * nx / l, pts[i][1] + dist * ny / l))
    out.append((pts[-1][0] + dist * normals[-1][0], pts[-1][1] + dist * normals[-1][1]))
    return out


def buffer_ring(pts, half_w):
    """Create a polygon buffer around a polyline."""
    left = offset_polyline(pts, half_w)
    right = offset_polyline(pts[::-1], half_w)
    return left + right + [left[0]]


def clip_polyline(pts, x0, y0, x1, y1):
    """Clip polyline into runs lying inside [x0, x1] x [y0, y1]."""
    def inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    runs = []
    current = []
    for p in pts:
        if inside(p):
            current.append(p)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = []
    if len(current) >= 2:
        runs.append(current)
    return runs


def playable_sdf(x, y):
    """Signed distance to playable boundary: negative inside, positive outside."""
    dx = max(0.0 - x, x - PLAYABLE_M)
    dy = max(0.0 - y, y - PLAYABLE_M)
    if dx <= 0.0 and dy <= 0.0:
        return max(dx, dy)
    return math.hypot(max(0.0, dx), max(0.0, dy))


def wood_tags(leaf_type='mixed'):
    """Standard OSM tags for timber / woods."""
    return {'natural': 'wood', 'landuse': 'farmyard', 'leaf_type': leaf_type}


# ==================================================================================
# 5. Core Feature Registries
# ==================================================================================
# The registries define what exists on the map.
# Clean baseline: all registries start empty, ready for new programmatic features.
CORRIDORS = []
WATER = []
PADS = []
AREAS = []
FIELDS = []
SHELTERBELTS = []
HILLS = []


# ==================================================================================
# 6. Builder Helper Functions
# ==================================================================================
def add_corridor(cid, name, kind, axis, half_width_m=3.0, feather_m=6.0,
                 grade_max=0.08, bridge_spans=None, tags=None):
    """Register a road or railway alignment.

    axis: [(x, y), ...] in playable metres.
    """
    record = {
        'id': cid,
        'name': name,
        'kind': kind,
        'axis': [(float(x), float(y)) for x, y in axis],
        'half_width_m': float(half_width_m),
        'feather_m': float(feather_m),
        'grade_max': float(grade_max),
        'bridge_spans': list(bridge_spans or []),
        'tags': dict(tags or {'highway': HIGHWAY_CLASS.get(kind, 'tertiary')})
    }
    CORRIDORS.append(record)
    return record


def add_water(wid, name, kind='lake', ring=None, axis=None, water_half_w=20.0,
              depth_m=15.0, shape=None, tags=None):
    """Register a water body (lake/pond with ring, or river with axis)."""
    t = dict(tags or {'natural': 'water', 'water': kind})
    record = {
        'id': wid,
        'name': name,
        'kind': kind,
        'depth_m': float(depth_m),
        'tags': t
    }
    if shape:
        record['shape'] = dict(shape)
        if ring is None:
            raw_ring = ellipse_ring(
                shape['cx'], shape['cy'], shape['a'], shape['b'],
                rot_deg=shape.get('rot_deg', 0.0),
                n=shape.get('n', 240),
                harmonics=shape.get('harmonics', ())
            )
            ring = clip_ring_to_rect(raw_ring, 0.0, 0.0, PLAYABLE_M, PLAYABLE_M)
    if ring:
        record['ring'] = close_ring([(float(x), float(y)) for x, y in ring])
    if axis:
        record['axis'] = [(float(x), float(y)) for x, y in axis]
        record['water_half_w'] = float(water_half_w)
    WATER.append(record)
    return record


def add_pad(pid, name, kind, centre, size, ring=None, feather_m=20.0,
            drain_grade=0.003, level=True, tags=None):
    """Register a levelled building pad / yard."""
    cx, cy = float(centre[0]), float(centre[1])
    w, h = float(size[0]), float(size[1])
    r = ring if ring else rect_ring(cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    record = {
        'id': pid,
        'name': name,
        'kind': kind,
        'centre': (cx, cy),
        'size': (w, h),
        'ring': close_ring([(float(x), float(y)) for x, y in r]),
        'feather_m': float(feather_m),
        'drain_grade': float(drain_grade),
        'level': bool(level),
        'tags': dict({'landuse': 'farmyard'} if tags is None else tags)
    }
    PADS.append(record)
    return record


def add_area(aid, name, kind, ring, tags=None, is_border=False):
    """Register a generic enclosed area (forest, farmyard, etc.)."""
    record = {
        'id': aid,
        'name': name,
        'kind': kind,
        'ring': close_ring([(float(x), float(y)) for x, y in ring]),
        'tags': dict(tags or {}),
        'is_border': bool(is_border),
    }
    AREAS.append(record)
    return record


def add_field(fid, name, ring, tags=None):
    """Register an agricultural field."""
    t = dict(tags or {'landuse': 'farmland'})
    record = add_area(fid, name, 'farmland', ring, tags=t)
    FIELDS.append(record)
    return record


def add_wood(wid, name, ring, leaf_type='mixed', tags=None, is_border=False):
    """Register a forest / wood lot."""
    t = dict(tags or wood_tags(leaf_type))
    return add_area(wid, name, 'wood', ring, tags=t, is_border=is_border)


def add_hill(hid, name, cx, cy, a, b, height_m=200.0, rot_deg=0.0,
             harmonics=(), spiral_turns=2.0, theta_start=0.5 * math.pi,
             q_start=0.95, q_top=0.08, shelf_frac=0.08):
    """Register a natural hill with a continuous drivable spiral terrace ascending to the summit."""
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    n_pts = 500
    spiral_pts = []
    spiral_u = []
    # The spiral road centerline sits at the middle of the terrace band (nu = turns * u)
    for i in range(n_pts):
        u = i / (n_pts - 1)
        nu = spiral_turns * u
        # Along the flat terrace band f = 0.5: nu - (theta - theta_start)/(2*pi) = -0.5
        th = theta_start + 2.0 * math.pi * (nu - 0.5)
        q_s = q_start * (1.0 - u) + q_top * u
        m = 1.0
        for amp, k, ph in harmonics:
            m += amp * math.sin(k * th + ph)
        u_loc = a * q_s * m * math.cos(th)
        v_loc = b * q_s * m * math.sin(th)
        x = cx + u_loc * cos_r - v_loc * sin_r
        y = cy + u_loc * sin_r + v_loc * cos_r
        spiral_pts.append((float(x), float(y)))
        spiral_u.append(float(u))

    record = {
        'id': hid,
        'name': name,
        'cx': float(cx),
        'cy': float(cy),
        'a': float(a),
        'b': float(b),
        'height_m': float(height_m),
        'rot_deg': float(rot_deg),
        'harmonics': tuple(harmonics),
        'spiral_turns': float(spiral_turns),
        'theta_start': float(theta_start),
        'q_start': float(q_start),
        'q_top': float(q_top),
        'shelf_frac': float(shelf_frac),
        'spiral_pts': spiral_pts,
        'spiral_u': spiral_u,
    }
    HILLS.append(record)
    return record


# ==================================================================================
# 6b. Map Features
# ==================================================================================
# Natural lake extending from playable area (x=[0, 1000], y=[3000, 6000])
# out westward beyond the playable boundary into the non-playable margin (x < 0),
# with a depth of 15.0 m.
LAKE_WEST_SHAPE = {
    'cx': -180.0,
    'cy': 4500.0,
    'a': 960.0,
    'b': 1340.0,
    'rot_deg': -3.0,
    'n': 240,
    'harmonics': (
        (0.11, 2, 0.45),
        (0.09, 3, -0.85),
        (0.06, 4, 1.65),
        (0.05, 5, -2.10),
        (0.035, 7, 0.80),
    ),
}

add_water(
    wid='lake_west',
    name='Lago Valle Bonito',
    kind='lake',
    depth_m=15.0,
    shape=LAKE_WEST_SHAPE,
)

# Natural 200 m spiral hill in x=[5000, 7000], y=[1000, 3000]
HILL_SPIRAL_HARMONICS = (
    (0.03, 3, 0.50),
    (0.02, 5, -1.20),
    (0.015, 7, 2.00),
)

add_hill(
    hid='hill_spiral',
    name='Colina Espiral',
    cx=6000.0,
    cy=2000.0,
    a=930.0,
    b=930.0,
    height_m=200.0,
    rot_deg=15.0,
    harmonics=HILL_SPIRAL_HARMONICS,
    spiral_turns=2.0,
    theta_start=0.5 * math.pi,
    q_start=0.95,
    q_top=0.08,
    shelf_frac=0.08,
)

add_wood(
    wid='wood_hill_spiral',
    name='Bosque Colina Espiral',
    ring=ellipse_ring(
        6000.0, 2000.0, 930.0, 930.0,
        rot_deg=15.0, n=180, harmonics=HILL_SPIRAL_HARMONICS
    ),
    leaf_type='needleleaved',
)

# Primary roads along y = 800 and x = 7200
add_corridor(
    cid='road_primary_y800',
    name='Ruta Principal Norte (Y=800)',
    kind='primary',
    axis=[(0.0, 800.0), (PLAYABLE_M, 800.0)],
    half_width_m=5.0,
    feather_m=10.0,
    grade_max=0.06,
    tags={'highway': 'primary', 'name': 'Ruta Principal Norte'},
)

add_corridor(
    cid='road_primary_x7200',
    name='Ruta Principal Este (X=7200)',
    kind='primary',
    axis=[(7200.0, 0.0), (7200.0, PLAYABLE_M)],
    half_width_m=5.0,
    feather_m=10.0,
    grade_max=0.06,
    tags={'highway': 'primary', 'name': 'Ruta Principal Este'},
)

# Dirt roads (terracerías) along x = 1000 and y = 7000
add_corridor(
    cid='track_x1000',
    name='Terracería Oeste (X=1000)',
    kind='track',
    axis=[(1000.0, 0.0), (1000.0, PLAYABLE_M)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Oeste'},
)

add_corridor(
    cid='track_y7000',
    name='Terracería Sur (Y=7000)',
    kind='track',
    axis=[(0.0, 7000.0), (PLAYABLE_M, 7000.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.08,
    tags={'highway': 'track', 'surface': 'dirt', 'name': 'Terracería Sur'},
)

# 50 ha (~707.11 x 707.11 m) square farmyard at the (7200, 7000) intersection inside the internal area
_FARMYARD_SE_SIDE = math.sqrt(50.0 * 10000.0)
add_pad(
    pid='pad_farmyard_se',
    name='Granja Principal Sureste',
    kind='farmyard',
    centre=(7200.0 - _FARMYARD_SE_SIDE / 2.0, 7000.0 - _FARMYARD_SE_SIDE / 2.0),
    size=(_FARMYARD_SE_SIDE, _FARMYARD_SE_SIDE),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

# 8x8 town of 200x100 m blocks (X x Y) with secondary streets at the NW intersection (x=1000, y=800)
_TOWN_NW_X0 = 1000.0
_TOWN_NW_Y0 = 800.0
_TOWN_NW_N = 8
_TOWN_NW_BLOCK_X_M = 200.0
_TOWN_NW_BLOCK_Y_M = 100.0
_TOWN_NW_STREET_GAP_M = 16.0
_TOWN_NW_PITCH_X_M = _TOWN_NW_BLOCK_X_M + _TOWN_NW_STREET_GAP_M  # 216.0 m
_TOWN_NW_PITCH_Y_M = _TOWN_NW_BLOCK_Y_M + _TOWN_NW_STREET_GAP_M  # 116.0 m
_TOWN_NW_SPAN_X_M = _TOWN_NW_N * _TOWN_NW_PITCH_X_M              # 1728.0 m
_TOWN_NW_SPAN_Y_M = _TOWN_NW_N * _TOWN_NW_PITCH_Y_M              # 928.0 m

add_pad(
    pid='pad_town_nw',
    name='Pueblo Valle Bonito',
    kind='village',
    centre=(_TOWN_NW_X0 + _TOWN_NW_SPAN_X_M / 2.0, _TOWN_NW_Y0 + _TOWN_NW_SPAN_Y_M / 2.0),
    size=(_TOWN_NW_SPAN_X_M, _TOWN_NW_SPAN_Y_M),
    feather_m=25.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard'},
)

for _k in range(1, _TOWN_NW_N + 1):
    _sx = _TOWN_NW_X0 + _k * _TOWN_NW_PITCH_X_M
    add_corridor(
        cid=f'road_town_nw_v{_k}',
        name=f'Calle Norte-Sur {_k}',
        kind='secondary',
        axis=[(_sx, _TOWN_NW_Y0), (_sx, _TOWN_NW_Y0 + _TOWN_NW_SPAN_Y_M)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Norte-Sur {_k}'},
    )
    _sy = _TOWN_NW_Y0 + _k * _TOWN_NW_PITCH_Y_M
    add_corridor(
        cid=f'road_town_nw_h{_k}',
        name=f'Calle Este-Oeste {_k}',
        kind='secondary',
        axis=[(_TOWN_NW_X0, _sy), (_TOWN_NW_X0 + _TOWN_NW_SPAN_X_M, _sy)],
        half_width_m=3.5,
        feather_m=6.0,
        grade_max=0.05,
        tags={'highway': 'secondary', 'name': f'Calle Este-Oeste {_k}'},
    )

# Industrial farmyard north of the town across the primary road (x=1000..2728, y=600..800)
_IND_NW_HEIGHT_M = 200.0
add_pad(
    pid='pad_industrial_nw',
    name='Zona Industrial Norte',
    kind='industry',
    centre=(_TOWN_NW_X0 + _TOWN_NW_SPAN_X_M / 2.0, _TOWN_NW_Y0 - _IND_NW_HEIGHT_M / 2.0),
    size=(_TOWN_NW_SPAN_X_M, _IND_NW_HEIGHT_M),
    feather_m=20.0,
    drain_grade=0.0,
    level=True,
    tags={'landuse': 'farmyard', 'building': 'industrial'},
)

# PLSS Section Roads (tertiary roads dividing the interior into ~1500 m sections)
_PLSS_SEC_X = (2760.0, 4850.0, 6025.0)
_PLSS_SEC_Y = (1900.0, 3050.0, 4400.0, 5700.0)
_ALL_ROAD_X = (1000.0, 2760.0, 4850.0, 6025.0, 7200.0)
_ALL_ROAD_Y = (800.0, 1900.0, 3050.0, 4400.0, 5700.0, 7000.0)

add_corridor(
    cid='road_sec_x2760',
    name='Camino Sección X=2760',
    kind='tertiary',
    axis=[(2760.0, 800.0), (2760.0, 7000.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección X=2760'},
)
add_corridor(
    cid='road_sec_x4850',
    name='Camino Sección X=4850',
    kind='tertiary',
    axis=[(4850.0, 800.0), (4850.0, 7000.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección X=4850'},
)
add_corridor(
    cid='road_sec_x6025',
    name='Camino Sección X=6025',
    kind='tertiary',
    axis=[(6025.0, 3050.0), (6025.0, 7000.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección X=6025'},
)
add_corridor(
    cid='road_sec_y1900',
    name='Camino Sección Y=1900',
    kind='tertiary',
    axis=[(1000.0, 1900.0), (4850.0, 1900.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección Y=1900'},
)
add_corridor(
    cid='road_sec_y3050',
    name='Camino Sección Y=3050',
    kind='tertiary',
    axis=[(1000.0, 3050.0), (7200.0, 3050.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección Y=3050'},
)
add_corridor(
    cid='road_sec_y4400',
    name='Camino Sección Y=4400',
    kind='tertiary',
    axis=[(1000.0, 4400.0), (7200.0, 4400.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección Y=4400'},
)
add_corridor(
    cid='road_sec_y5700',
    name='Camino Sección Y=5700',
    kind='tertiary',
    axis=[(1000.0, 5700.0), (7200.0, 5700.0)],
    half_width_m=3.0,
    feather_m=6.0,
    grade_max=0.06,
    tags={'highway': 'tertiary', 'name': 'Camino Sección Y=5700'},
)

# 10 square farmyards of 15 ha (~387.30 x 387.30 m) at random positions along the primary roads and dirt tracks
_FY15_SIDE_M = math.sqrt(15.0 * 10000.0)
_FY15_RNG = random.Random(202505)
_FY15_ROADS = [
    ('y800',  800.0,  'h'),
    ('x7200', 7200.0, 'v'),
    ('x1000', 1000.0, 'v'),
    ('y7000', 7000.0, 'h'),
]


def _fy15_valid_box(x0, y0, x1, y1, margin=80.0):
    """True if candidate box [x0,x1]x[y0,y1] stays clear of boundaries, cross-roads, and existing features."""
    if x0 < 100.0 or y0 < 100.0 or x1 > PLAYABLE_M - 100.0 or y1 > PLAYABLE_M - 100.0:
        return False
    # Must not be crossed in its interior by any main or section road
    for rx in _ALL_ROAD_X:
        if x0 - 25.0 < rx < x1 + 25.0 and not (abs(x0 - rx) < 1e-6 or abs(x1 - rx) < 1e-6):
            return False
    for ry in _ALL_ROAD_Y:
        if y0 - 25.0 < ry < y1 + 25.0 and not (abs(y0 - ry) < 1e-6 or abs(y1 - ry) < 1e-6):
            return False
    cand_padded = rect_ring(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    for p in PADS:
        if rings_overlap(cand_padded, p['ring']):
            return False
    for a in AREAS:
        if rings_overlap(cand_padded, a['ring']):
            return False
    for w in WATER:
        if w.get('ring') and rings_overlap(cand_padded, w['ring']):
            return False
    return True


_fy15_count = 0
while _fy15_count < 10:
    # Cycle through the 4 roads for balanced coverage, then pick random side and position
    _rname, _rcoord, _rorient = _FY15_ROADS[_fy15_count % len(_FY15_ROADS)]
    _side_sign = _FY15_RNG.choice([-1, 1])
    _pos = _FY15_RNG.uniform(150.0, PLAYABLE_M - 150.0 - _FY15_SIDE_M)
    if _rorient == 'h':
        _x0, _x1 = _pos, _pos + _FY15_SIDE_M
        _y0, _y1 = (_rcoord - _FY15_SIDE_M, _rcoord) if _side_sign < 0 else (_rcoord, _rcoord + _FY15_SIDE_M)
    else:
        _y0, _y1 = _pos, _pos + _FY15_SIDE_M
        _x0, _x1 = (_rcoord - _FY15_SIDE_M, _rcoord) if _side_sign < 0 else (_rcoord, _rcoord + _FY15_SIDE_M)

    if _fy15_valid_box(_x0, _y0, _x1, _y1, margin=120.0):
        _fy15_count += 1
        add_pad(
            pid=f'pad_farmyard_{_fy15_count}',
            name=f'Granja {_fy15_count}',
            kind='farmyard',
            centre=(0.5 * (_x0 + _x1), 0.5 * (_y0 + _y1)),
            size=(_FY15_SIDE_M, _FY15_SIDE_M),
            feather_m=20.0,
            drain_grade=0.0,
            level=True,
            tags={'landuse': 'farmyard'},
        )

# Broadleaved shelterbelts (rompevientos) in long stretches along primary roads and dirt tracks
_BELT_WIDTH_M = 48.0
_BELT_SETBACK_M = 8.0
_BELT_RNG = random.Random(202506)


def _belt_free_runs(rcoord, orient, side_sign, step=20.0, clear_margin=25.0):
    """Find contiguous free intervals [s0, s1] along a corridor side where a shelterbelt can sit."""
    s_vals = []
    s = 120.0
    while s <= PLAYABLE_M - 120.0:
        if orient == 'h':
            x0, x1 = s, s + step
            if side_sign < 0:
                y0, y1 = rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M, rcoord - _BELT_SETBACK_M
            else:
                y0, y1 = rcoord + _BELT_SETBACK_M, rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M
        else:
            y0, y1 = s, s + step
            if side_sign < 0:
                x0, x1 = rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M, rcoord - _BELT_SETBACK_M
            else:
                x0, x1 = rcoord + _BELT_SETBACK_M, rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M

        ok = True
        # Keep clear of perpendicular main and section roads
        if orient == 'h':
            for rx in _ALL_ROAD_X:
                if x0 - 30.0 <= rx <= x1 + 30.0:
                    ok = False
                    break
        else:
            for ry in _ALL_ROAD_Y:
                if y0 - 30.0 <= ry <= y1 + 30.0:
                    ok = False
                    break

        if ok:
            if orient == 'h':
                box = rect_ring(x0 - clear_margin, y0 - 4.0, x1 + clear_margin, y1 + 4.0)
            else:
                box = rect_ring(x0 - 4.0, y0 - clear_margin, x1 + 4.0, y1 + clear_margin)
            for p in PADS:
                if rings_overlap(box, p['ring']):
                    ok = False
                    break
        if ok:
            for a in AREAS:
                if rings_overlap(box, a['ring']):
                    ok = False
                    break
        if ok:
            for w in WATER:
                if w.get('ring') and rings_overlap(box, w['ring']):
                    ok = False
                    break

        s_vals.append((s, ok))
        s += step

    runs = []
    cur_start = None
    last_s = None
    for s_pos, is_ok in s_vals:
        if is_ok:
            if cur_start is None:
                cur_start = s_pos
            last_s = s_pos + step
        else:
            if cur_start is not None and (last_s - cur_start) >= 500.0:
                runs.append((cur_start, last_s))
            cur_start = None
    if cur_start is not None and (last_s - cur_start) >= 500.0:
        runs.append((cur_start, last_s))
    return runs


_belt_idx = 0
for _rname, _rcoord, _rorient in _FY15_ROADS:
    for _side_sign in (-1, 1):
        for _run_s0, _run_s1 in _belt_free_runs(_rcoord, _rorient, _side_sign):
            _cursor = _run_s0 + _BELT_RNG.uniform(20.0, 70.0)
            while _cursor + 500.0 <= _run_s1:
                _len = min(_BELT_RNG.uniform(650.0, 1450.0), _run_s1 - _cursor)
                if _len < 480.0:
                    break
                _s0, _s1 = _cursor, _cursor + _len
                if _rorient == 'h':
                    _bx0, _bx1 = _s0, _s1
                    if _side_sign < 0:
                        _by0, _by1 = _rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M, _rcoord - _BELT_SETBACK_M
                    else:
                        _by0, _by1 = _rcoord + _BELT_SETBACK_M, _rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M
                else:
                    _by0, _by1 = _s0, _s1
                    if _side_sign < 0:
                        _bx0, _bx1 = _rcoord - _BELT_SETBACK_M - _BELT_WIDTH_M, _rcoord - _BELT_SETBACK_M
                    else:
                        _bx0, _bx1 = _rcoord + _BELT_SETBACK_M, _rcoord + _BELT_SETBACK_M + _BELT_WIDTH_M

                _belt_idx += 1
                _belt_rec = add_wood(
                    wid=f'belt_{_rname}_{_belt_idx}',
                    name=f'Rompevientos {_belt_idx}',
                    ring=rect_ring(_bx0, _by0, _bx1, _by1),
                    leaf_type='broadleaved',
                )
                SHELTERBELTS.append(_belt_rec)
                _cursor = _s1 + _BELT_RNG.uniform(160.0, 320.0)

# 50 m wide broadleaved perimeter border all around the playable map (0..50 m from edge)
_BORDER_SPECS = [
    # North border (y = 0 .. 50), leaving gaps at x=1000 and x=7200
    ('border_wood_n1', 'Borde Boscoso Norte 1', 0.0, 0.0, 990.0, EDGE_CLEAR_M),
    ('border_wood_n2', 'Borde Boscoso Norte 2', 1010.0, 0.0, 7190.0, EDGE_CLEAR_M),
    ('border_wood_n3', 'Borde Boscoso Norte 3', 7210.0, 0.0, PLAYABLE_M, EDGE_CLEAR_M),
    # South border (y = 8142 .. 8192), leaving gaps at x=1000 and x=7200
    ('border_wood_s1', 'Borde Boscoso Sur 1', 0.0, PLAYABLE_M - EDGE_CLEAR_M, 990.0, PLAYABLE_M),
    ('border_wood_s2', 'Borde Boscoso Sur 2', 1010.0, PLAYABLE_M - EDGE_CLEAR_M, 7190.0, PLAYABLE_M),
    ('border_wood_s3', 'Borde Boscoso Sur 3', 7210.0, PLAYABLE_M - EDGE_CLEAR_M, PLAYABLE_M, PLAYABLE_M),
    # East border (x = 8142 .. 8192), leaving gaps at y=800 and y=7000
    ('border_wood_e1', 'Borde Boscoso Este 1', PLAYABLE_M - EDGE_CLEAR_M, EDGE_CLEAR_M, PLAYABLE_M, 790.0),
    ('border_wood_e2', 'Borde Boscoso Este 2', PLAYABLE_M - EDGE_CLEAR_M, 810.0, PLAYABLE_M, 6990.0),
    ('border_wood_e3', 'Borde Boscoso Este 3', PLAYABLE_M - EDGE_CLEAR_M, 7010.0, PLAYABLE_M, PLAYABLE_M - EDGE_CLEAR_M),
    # West border (x = 0 .. 50), leaving gaps at y=800, lake_west (3180..5820), and y=7000
    ('border_wood_w1', 'Borde Boscoso Oeste 1', 0.0, EDGE_CLEAR_M, EDGE_CLEAR_M, 790.0),
    ('border_wood_w2', 'Borde Boscoso Oeste 2', 0.0, 810.0, EDGE_CLEAR_M, 3180.0),
    ('border_wood_w3', 'Borde Boscoso Oeste 3', 0.0, 5820.0, EDGE_CLEAR_M, 6990.0),
    ('border_wood_w4', 'Borde Boscoso Oeste 4', 0.0, 7010.0, EDGE_CLEAR_M, PLAYABLE_M - EDGE_CLEAR_M),
]

for _bid, _bname, _bx0, _by0, _bx1, _by1 in _BORDER_SPECS:
    add_wood(
        wid=_bid,
        name=_bname,
        ring=rect_ring(_bx0, _by0, _bx1, _by1),
        leaf_type='broadleaved',
        is_border=True,
    )

# PLSS Agricultural Subdivision (landuse=farmland, <= 200 parcels) across the rest of the playable map
_PLSS_RNG = random.Random(202507)
_PAD_OBSTACLES = []
for _p in PADS:
    _pxs = [_pt[0] for _pt in _p['ring']]
    _pys = [_pt[1] for _pt in _p['ring']]
    _PAD_OBSTACLES.append((min(_pxs) - 10.0, min(_pys) - 10.0, max(_pxs) + 10.0, max(_pys) + 10.0))

_BELT_OBSTACLES = []
for _sb in SHELTERBELTS:
    _sxs = [_pt[0] for _pt in _sb['ring']]
    _sys = [_pt[1] for _pt in _sb['ring']]
    _BELT_OBSTACLES.append((min(_sxs) - 6.0, min(_sys) - 6.0, max(_sxs) + 6.0, max(_sys) + 6.0))


def _retract_from_shelterbelts(x0, y0, x1, y1):
    """Retract parcel edges cleanly behind any overlapping roadside shelterbelt without fragmenting the parcel."""
    for bx0, by0, bx1, by1 in _BELT_OBSTACLES:
        if x1 <= bx0 or x0 >= bx1 or y1 <= by0 or y0 >= by1:
            continue
        bw, bh = bx1 - bx0, by1 - by0
        if bw >= bh:
            # Horizontal shelterbelt along top or bottom edge of the parcel
            if abs(by0 - y0) < abs(y1 - by1):
                y0 = max(y0, by1)
            else:
                y1 = min(y1, by0)
        else:
            # Vertical shelterbelt along left or right edge of the parcel
            if abs(bx0 - x0) < abs(x1 - bx1):
                x0 = max(x0, bx1)
            else:
                x1 = min(x1, bx0)
    return x0, y0, x1, y1


def _subtract_pad_obstacles(boxes, min_dim=110.0, split_gap=8.0):
    """Carve parcels cleanly around farmyard/town/industrial pads without creating small slivers."""
    current = list(boxes)
    half_g = 0.5 * split_gap
    for ox0, oy0, ox1, oy1 in _PAD_OBSTACLES:
        nxt = []
        for x0, y0, x1, y1 in current:
            if x1 <= ox0 or x0 >= ox1 or y1 <= oy0 or y0 >= oy1:
                nxt.append((x0, y0, x1, y1))
                continue
            pieces = []
            # Top strip
            ty1 = min(y1, oy0) - half_g
            if ty1 - y0 >= min_dim:
                pieces.append((x0, y0, x1, ty1))
            # Bottom strip
            by0 = max(y0, oy1) + half_g
            if y1 - by0 >= min_dim:
                pieces.append((x0, by0, x1, y1))
            # Middle vertical span
            my0 = max(y0, oy0 + half_g)
            my1 = min(y1, oy1 - half_g)
            if my1 - my0 >= min_dim:
                if ox0 - x0 >= min_dim:
                    pieces.append((x0, my0, ox0, my1))
                if x1 - ox1 >= min_dim:
                    pieces.append((ox1, my0, x1, my1))
            # Keep at most the 2 largest pieces so a pad never shatters a parcel into many fragments
            pieces.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
            nxt.extend(pieces[:2])
        current = nxt
    return current


def _harmonic_q(x, y, cx, cy, a, b, rot_deg, harmonics, margin_m=12.0):
    """Normalized radius q relative to a harmonic ellipse buffered by margin_m."""
    dx, dy = x - cx, y - cy
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    u = dx * cos_r + dy * sin_r
    v = -dx * sin_r + dy * cos_r
    th = math.atan2(v / (b + margin_m), u / (a + margin_m))
    m = 1.0
    for amp, k, ph in harmonics:
        m += amp * math.sin(k * th + ph)
    rx = (a + margin_m) * m
    ry = (b + margin_m) * m
    return math.hypot(u / rx, v / ry), th, cos_r, sin_r


def _point_at_harmonic_q(q_target, th, cx, cy, a, b, rot_deg, harmonics, margin_m=12.0):
    """Point at normalized radius q_target and local angle th on the buffered harmonic ellipse."""
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    m = 1.0
    for amp, k, ph in harmonics:
        m += amp * math.sin(k * th + ph)
    u = q_target * (a + margin_m) * m * math.cos(th)
    v = q_target * (b + margin_m) * m * math.sin(th)
    return (cx + u * cos_r - v * sin_r, cy + u * sin_r + v * cos_r)


_CURVED_OBSTACLES = [
    (6000.0, 2000.0, 930.0, 930.0, 15.0, HILL_SPIRAL_HARMONICS, 12.0),
    (
        LAKE_WEST_SHAPE['cx'],
        LAKE_WEST_SHAPE['cy'],
        LAKE_WEST_SHAPE['a'],
        LAKE_WEST_SHAPE['b'],
        LAKE_WEST_SHAPE['rot_deg'],
        LAKE_WEST_SHAPE['harmonics'],
        12.0,
    ),
]


def _box_touches_curve(x0, y0, x1, y1):
    for cx, cy, a, b, rot_deg, harm, marg in _CURVED_OBSTACLES:
        for sx in (x0, 0.5 * (x0 + x1), x1):
            for sy in (y0, 0.5 * (y0 + y1), y1):
                q, _, _, _ = _harmonic_q(sx, sy, cx, cy, a, b, rot_deg, harm, marg)
                if q < 1.05:
                    return True
    return False


def _plss_subdivide_section(x0, y0, x1, y1, gap=10.0):
    """Subdivide a PLSS section into a balanced mix of Quarter (~64 ha), Half-Quarter (~32 ha), and Quarter-Quarter (~16 ha) boxes (<= 200 total)."""
    w, h = x1 - x0, y1 - y0
    # Step 1: Split into Quarter Sections if larger than ~1050 m
    qx = [(x0, x1)]
    if w > 1050.0:
        mx = 0.5 * (x0 + x1)
        qx = [(x0, mx - 0.5 * gap), (mx + 0.5 * gap, x1)]
    qy = [(y0, y1)]
    if h > 1050.0:
        my = 0.5 * (y0 + y1)
        qy = [(y0, my - 0.5 * gap), (my + 0.5 * gap, y1)]

    quarters = [(ax0, ay0, ax1, ay1) for ax0, ax1 in qx for ay0, ay1 in qy]
    parcels = []
    for qx0, qy0, qx1, qy1 in quarters:
        qw, qh = qx1 - qx0, qy1 - qy0
        touches_curve = _box_touches_curve(qx0, qy0, qx1, qy1)
        roll = _PLSS_RNG.random()
        if (touches_curve and qw >= 420.0 and qh >= 420.0) or (roll < 0.025 and qw >= 580.0 and qh >= 580.0):
            # Subdivide into 2x2 Quarter-Quarter sections (~16 ha)
            csx = [
                (qx0, 0.5 * (qx0 + qx1) - 0.5 * gap),
                (0.5 * (qx0 + qx1) + 0.5 * gap, qx1),
            ]
            csy = [
                (qy0, 0.5 * (qy0 + qy1) - 0.5 * gap),
                (0.5 * (qy0 + qy1) + 0.5 * gap, qy1),
            ]
            for cx0, cx1 in csx:
                for cy0, cy1 in csy:
                    if touches_curve:
                        # Subdivide straddling curved boxes one more time so none encloses obstacle center
                        qs = [
                            min(
                                _harmonic_q(sx, sy, ocx, ocy, oa, ob, orot, oharm, omarg)[0]
                                for ocx, ocy, oa, ob, orot, oharm, omarg in _CURVED_OBSTACLES
                            )
                            for sx in (cx0, 0.5 * (cx0 + cx1), cx1)
                            for sy in (cy0, 0.5 * (cy0 + cy1), cy1)
                        ]
                        if min(qs) < 1.01 and max(qs) > 0.99 and (cx1 - cx0) >= 240.0 and (cy1 - cy0) >= 240.0:
                            mx2, my2 = 0.5 * (cx0 + cx1), 0.5 * (cy0 + cy1)
                            for sx0, sx1 in ((cx0, mx2 - 0.5 * gap), (mx2 + 0.5 * gap, cx1)):
                                for sy0, sy1 in ((cy0, my2 - 0.5 * gap), (my2 + 0.5 * gap, cy1)):
                                    parcels.append((sx0, sy0, sx1, sy1))
                            continue
                    parcels.append((cx0, cy0, cx1, cy1))
        elif roll < 0.125 and (qw >= 520.0 or qh >= 520.0):
            # Split into 2 Half-Quarter sections (~32 ha)
            if qw >= qh and qw >= 520.0:
                mx = 0.5 * (qx0 + qx1)
                parcels.append((qx0, qy0, mx - 0.5 * gap, qy1))
                parcels.append((mx + 0.5 * gap, qy0, qx1, qy1))
            else:
                my = 0.5 * (qy0 + qy1)
                parcels.append((qx0, qy0, qx1, my - 0.5 * gap))
                parcels.append((qx0, my + 0.5 * gap, qx1, qy1))
        else:
            # Keep as a full Quarter section (~64 ha)
            parcels.append((qx0, qy0, qx1, qy1))
    return parcels


def _trim_box_to_curved_obstacles(x0, y0, x1, y1):
    """Trim box [x0,y0,x1,y1] smoothly along the buffered contour of curved obstacles."""
    for cx, cy, a, b, rot_deg, harm, marg in _CURVED_OBSTACLES:
        if x0 - 20.0 <= cx <= x1 + 20.0 and y0 - 20.0 <= cy <= y1 + 20.0:
            return None
        corner_qs = [
            _harmonic_q(sx, sy, cx, cy, a, b, rot_deg, harm, marg)[0]
            for sx in (x0, x1)
            for sy in (y0, y1)
        ]
        if max(corner_qs) < 1.02:
            return None

    needs_curve = False
    for cx, cy, a, b, rot_deg, harm, marg in _CURVED_OBSTACLES:
        sample_qs = [
            _harmonic_q(sx, sy, cx, cy, a, b, rot_deg, harm, marg)[0]
            for sx in (x0, 0.5 * (x0 + x1), x1)
            for sy in (y0, 0.5 * (y0 + y1), y1)
        ]
        if min(sample_qs) < 1.01:
            needs_curve = True
            break

    base_ring = rect_ring(x0, y0, x1, y1)
    if not needs_curve:
        return base_ring

    dense = densify(base_ring, 16.0)[:-1]
    out = []
    for px, py in dense:
        for cx, cy, a, b, rot_deg, harm, marg in _CURVED_OBSTACLES:
            q, th, _, _ = _harmonic_q(px, py, cx, cy, a, b, rot_deg, harm, marg)
            if q < 1.0:
                px, py = _point_at_harmonic_q(1.0, th, cx, cy, a, b, rot_deg, harm, marg)
        px = max(EDGE_CLEAR_M + 4.0, min(PLAYABLE_M - EDGE_CLEAR_M - 4.0, px))
        py = max(EDGE_CLEAR_M + 4.0, min(PLAYABLE_M - EDGE_CLEAR_M - 4.0, py))
        if not out or math.dist(out[-1], (px, py)) >= 6.0:
            out.append((px, py))
    if len(out) >= 2 and math.dist(out[0], out[-1]) < 6.0:
        out.pop()
    if len(out) < 3:
        return None
    ring = close_ring(out)
    if not ring_is_simple(ring) or ring_area_ha(ring) < 2.0:
        return None
    for w in WATER:
        if w.get('ring') and rings_overlap(ring, w['ring']):
            return None
    for a in AREAS:
        if a['id'] == 'wood_hill_spiral' and rings_overlap(ring, a['ring']):
            return None
    return ring


# Macro PLSS sections bounded by the 50 m border and the road network
_X_GRID = [EDGE_CLEAR_M, 1000.0, 2760.0, 4850.0, 6025.0, 7200.0, PLAYABLE_M - EDGE_CLEAR_M]
_Y_GRID = [EDGE_CLEAR_M, 800.0, 1900.0, 3050.0, 4400.0, 5700.0, 7000.0, PLAYABLE_M - EDGE_CLEAR_M]

_field_idx = 0
for _iy in range(len(_Y_GRID) - 1):
    for _ix in range(len(_X_GRID) - 1):
        _sx0, _sx1 = _X_GRID[_ix] + 10.0, _X_GRID[_ix + 1] - 10.0
        _sy0, _sy1 = _Y_GRID[_iy] + 10.0, _Y_GRID[_iy + 1] - 10.0
        for _px0, _py0, _px1, _py1 in _plss_subdivide_section(_sx0, _sy0, _sx1, _sy1, gap=10.0):
            for _cx0, _cy0, _cx1, _cy1 in _subtract_pad_obstacles([(_px0, _py0, _px1, _py1)], min_dim=110.0):
                _cx0, _cy0, _cx1, _cy1 = _retract_from_shelterbelts(_cx0, _cy0, _cx1, _cy1)
                if (_cx1 - _cx0) < 90.0 or (_cy1 - _cy0) < 90.0 or (_cx1 - _cx0) * (_cy1 - _cy0) / 10000.0 < 2.5:
                    continue
                _fring = _trim_box_to_curved_obstacles(_cx0, _cy0, _cx1, _cy1)
                if _fring is not None and ring_area_ha(_fring) >= 2.5:
                    _field_idx += 1
                    add_field(
                        fid=f'field_plss_{_field_idx}',
                        name=f'Parcela {_field_idx}',
                        ring=_fring,
                        tags={'landuse': 'farmland'},
                    )


# ==================================================================================
# 7. Registry Accessors
# ==================================================================================
def corridors():
    return list(CORRIDORS)


def water():
    return list(WATER)


def water_axes():
    """(polyline, name) for every watercourse that has a centreline."""
    return [(w['axis'], w['name']) for w in WATER if w.get('axis')]


def pads():
    return list(PADS)


def areas():
    return list(AREAS)


def hills():
    return list(HILLS)


def load_roughness(path=None):
    """Read terrain_stats.json and return roughness lookup function, or None."""
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
        c = max(0, min(n - 1, c))
        r = max(0, min(n - 1, r))
        return rough[r * n + c]
    return lookup


# ==================================================================================
# 8. Validation & Integrity Checks
# ==================================================================================
def validate():
    """Check geometric invariants before generating DEM or OSM."""
    bad = []

    # Rim bounds check
    if RIM_RAMP_M <= 0.0:
        bad.append(f"rim: apron ({RIM_APRON_M:.0f} m) and shoulder "
                   f"({RIM_BACK_M:.0f} m) leave no ramp room")

    # IDs must be unique
    all_records = CORRIDORS + WATER + PADS + AREAS + HILLS
    ids = [r['id'] for r in all_records]
    seen = set()
    for i in ids:
        if i in seen:
            bad.append(f"{i}: duplicate record id")
        seen.add(i)

    # Corridors check
    for c in CORRIDORS:
        if c['kind'] not in HIGHWAY_CLASS and c['kind'] != 'rail':
            bad.append(f"{c['id']}: unknown corridor class {c['kind']!r}")
        if len(c['axis']) < 2:
            bad.append(f"{c['id']}: alignment needs >= 2 points")
        for x, y in c['axis']:
            if not (-OFFSET_M - EXTEND_M <= x <= PLAYABLE_M + OFFSET_M + EXTEND_M
                    and -OFFSET_M - EXTEND_M <= y <= PLAYABLE_M + OFFSET_M + EXTEND_M):
                bad.append(f"{c['id']}: point ({x:.1f}, {y:.1f}) outside canvas bounds")
                break

    # Pads check
    for p in PADS:
        ring = p.get('ring', [])
        if len(ring) < 4 or math.dist(ring[0], ring[-1]) > 1e-6:
            bad.append(f"{p['id']}: pad ring does not close on first point")

    # Water check
    for w in WATER:
        if not w.get('tags'):
            bad.append(f"{w['id']}: untagged water body")
        elif not any(k in w['tags'] and (v is None or w['tags'][k] == v) for k, v in RENDERED_TAGS):
            bad.append(f"{w['id']}: tagged {w['tags']} - no renderer draws that")
        if w.get('ring'):
            r = w['ring']
            if len(r) < 4 or math.dist(r[0], r[-1]) > 1e-6:
                bad.append(f"{w['id']}: ring does not close on first point")
            for x, y in r:
                if not (-0.5 <= x <= PLAYABLE_M + 0.5 and -0.5 <= y <= PLAYABLE_M + 0.5):
                    bad.append(f"{w['id']}: point ({x:.1f}, {y:.1f}) outside playable bounds")
                    break

    # Areas check
    for a in AREAS:
        if not a.get('tags'):
            bad.append(f"{a['id']}: untagged area")
        elif not any(k in a['tags'] and (v is None or a['tags'][k] == v) for k, v in RENDERED_TAGS):
            bad.append(f"{a['id']}: tagged {a['tags']} - no renderer draws that")
        r = a.get('ring', [])
        if len(r) < 4 or math.dist(r[0], r[-1]) > 1e-6:
            bad.append(f"{a['id']}: ring does not close on first point")
        for x, y in r:
            if not (-0.5 <= x <= PLAYABLE_M + 0.5 and -0.5 <= y <= PLAYABLE_M + 0.5):
                bad.append(f"{a['id']}: point ({x:.1f}, {y:.1f}) outside playable bounds")
                break

    return bad


def summary():
    """One-line description of the layout status."""
    if not (CORRIDORS or WATER or PADS or AREAS or HILLS):
        return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, clean layout (no features), "
                f"flat canvas at base datum {BASE_ELEV_M:.1f} m")
    woods = [a for a in AREAS if a.get('kind') == 'wood']
    water_str = f", {len(WATER)} water body" if len(WATER) == 1 else (f", {len(WATER)} water bodies" if len(WATER) > 1 else "")
    hill_str = f", {len(HILLS)} hill" if len(HILLS) == 1 else (f", {len(HILLS)} hills" if len(HILLS) > 1 else "")
    return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, "
            f"{len(CORRIDORS)} roads, {len(PADS)} pads, {len(FIELDS)} fields, "
            f"{len(woods)} woods{water_str}{hill_str}, "
            f"base datum {BASE_ELEV_M:.1f} m")


if __name__ == '__main__':
    print("=== map_layout self-check ===")
    print("  ", summary())
    sw = global_to_local(*local_to_global(0.0, PLAYABLE_M))
    ne = global_to_local(*local_to_global(PLAYABLE_M, 0.0))
    print(f"   centre {LAT_CENTER:.4f}, {LON_CENTER:.4f}; projection round-trips "
          f"corners to ({sw[0]:.3f}, {sw[1]:.3f}) and ({ne[0]:.3f}, {ne[1]:.3f})")
    print(f"   canvas metres run {-OFFSET_M:.0f} .. {PLAYABLE_M + OFFSET_M:.0f}, "
          f"alignments out to {EDGE_MIN:.0f} .. {EDGE_MAX:.0f}")
    problems = validate()
    if problems:
        print("\n   PROBLEMS:")
        for p in problems:
            print("    -", p)
    else:
        print("\n   layout is clean and sound")
