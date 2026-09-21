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
BASE_ELEV_M = 46.4

# ==================================================================================
# 2. Non-Playable Border (Royal, Clay County, Iowa - Des Moines Lobe Till Plain)
# ==================================================================================
# What the player sees past the boundary: the gently rolling glacial till plain and
# low recessional moraines of northwest Iowa (Clay County / Royal).
# Broad undulating moraine ridges and gentle swells characteristic of the Des Moines Lobe.
RIM_APRON_M = 100.0           # flat transition apron beyond boundary
RIM_BACK_M = 150.0            # shoulder inside canvas edge
RIM_CREST_M = 72.0            # crest of highest rolling moraine ridges (~25.6 m above datum)
RIM_SADDLE_M = 60.0           # saddles between rolling moraines (~13.6 m above datum)
RIM_MOUTH_M = 52.0            # open horizon drainage sills (~5.6 m above datum)
RIM_MOUTH_VAR_M = 3.0         # gentle roll variance
RIM_RIDGE_LOBES = 9           # broader, gentler moraine rises around perimeter
RIM_RIDGE_BEAT = 15           # secondary harmonic for natural variation
RIM_SPUR_LAM_M = 1800.0       # broad moraine spurs and rolling swells
RIM_SPUR_AMP = 0.15           # gentle amplitude typical of glacial till
RIM_ROUGH_LAM_M = 800.0       # secondary swell-and-swale wavelength
RIM_ROUGH_AMP = 0.05
RIM_WEST_SMOOTH = 0.80        # consistent gentle rolling terrain all around

# Des Moines Lobe glacial moraine characteristics
MORAINE_GRAIN_DEG = -35.0     # moraine trend NW-SE
MORAINE_STRETCH = 2.6         # elongation along glacial ice margin
MORAINE_AMP_M = 4.5           # amplitude of moraine ridges
MORAINE_LAM_M = 2800.0        # broad moraine wavelength
SWELL_AMP_M = 2.0             # swell-and-swale undulations
SWELL_LAM_M = 1000.0

RIM_HEIGHT_M = RIM_CREST_M - BASE_ELEV_M
RIM_RAMP_M = OFFSET_M - RIM_APRON_M - RIM_BACK_M
RIM_PERIM_M = 4.0 * PLAYABLE_M
RIM_RIDGE_LAM_M = RIM_PERIM_M / RIM_RIDGE_LOBES
RIM_MAX_FLANK_DEG = 6.0       # gentle agricultural slopes

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
    'track': 'tertiary',
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


def ellipse_ring(cx, cy, a, b, rot_deg=0.0, n=48):
    """Sample an ellipse into a closed polygon ring."""
    rot = math.radians(rot_deg)
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    pts = []
    for i in range(n):
        th = 2.0 * math.pi * i / n
        ex = a * math.cos(th)
        ey = b * math.sin(th)
        pts.append((cx + ex * cos_r - ey * sin_r,
                    cy + ex * sin_r + ey * cos_r))
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


def add_water(wid, name, kind='lake', ring=None, axis=None, water_half_w=20.0, tags=None):
    """Register a water body (lake/pond with ring, or river with axis)."""
    t = dict(tags or {'natural': 'water', 'water': kind})
    record = {
        'id': wid,
        'name': name,
        'kind': kind,
        'tags': t
    }
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
        'tags': dict(tags or {'landuse': 'farmyard'})
    }
    PADS.append(record)
    return record


def add_area(aid, name, kind, ring, tags=None):
    """Register a generic enclosed area (forest, farmyard, etc.)."""
    record = {
        'id': aid,
        'name': name,
        'kind': kind,
        'ring': close_ring([(float(x), float(y)) for x, y in ring]),
        'tags': dict(tags or {})
    }
    AREAS.append(record)
    return record


def add_field(fid, name, ring, tags=None):
    """Register an agricultural field."""
    t = dict(tags or {'landuse': 'farmland'})
    record = add_area(fid, name, 'farmland', ring, tags=t)
    FIELDS.append(record)
    return record


def add_wood(wid, name, ring, leaf_type='mixed', tags=None):
    """Register a forest / wood lot."""
    t = dict(tags or wood_tags(leaf_type))
    return add_area(wid, name, 'wood', ring, tags=t)


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
    all_records = CORRIDORS + WATER + PADS + AREAS
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
    if not (CORRIDORS or WATER or PADS or AREAS):
        return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, clean layout (no features), "
                f"base datum {BASE_ELEV_M:.1f} m, Iowa moraine rim to {RIM_CREST_M:.0f} m")
    woods = [a for a in AREAS if a.get('kind') == 'wood']
    water_str = f", {len(WATER)} water body" if len(WATER) == 1 else (f", {len(WATER)} water bodies" if len(WATER) > 1 else "")
    return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, "
            f"{len(CORRIDORS)} roads, {len(PADS)} pads, {len(FIELDS)} fields, "
            f"{len(woods)} woods{water_str}, "
            f"base datum {BASE_ELEV_M:.1f} m, Iowa moraine rim to {RIM_CREST_M:.0f} m")


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
