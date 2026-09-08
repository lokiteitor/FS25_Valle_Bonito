#!/usr/bin/env python3
"""The layout of the map: where everything is, in playable metres.

This is the one source of truth shared by the two halves of the pipeline. The DEM
generator sculpts terrain around the geometry defined here; the OSM generator writes the
same geometry out as vectors. If the two disagree - a river carved where no river is
drawn, a farm pad flattened where no farmyard exists - the map is broken in a way that is
invisible in either output on its own, so both read their geometry from this module and
neither is allowed to invent its own.

**The map is empty.** The Iowa layout that used to live here - the river, the lake, the
creek, the Public Land Survey road grid, the branch line, three villages, seven
farmsteads, the potholes, the shelterbelts and 118 parcels - has been cleared out. What
is left is the technical base: the projection, the size of the canvas, the geometric
primitives every feature is built out of, and four empty registries to put the new world
in. The DEM that comes out of an empty layout is flat at `BASE_ELEV_M`, and `map.osm` is
a `<bounds>` element with nothing inside it.

To build the new map, fill the registries (`CORRIDORS`, `WATER`, `PADS`, `AREAS`) and add
the rules that have to hold about them to `validate()`. Both generators refuse to run
while `validate()` complains, which is what keeps a placement mistake from reaching the
editor. The record each registry holds is documented above it.

Coordinates are playable metres: x east, y south from the north edge, so the centre of
the playable area is (4096, 4096). The DEM canvas is larger than the playable area, so
canvas coordinates run from -2048 to 10240 in the same frame. Those numbers, and the
projection that ties them to lat/lon, are unchanged from the Iowa map: a heightmap and an
OSM built from this module still line up with each other and still import into Giants
Editor the way they always did.

Standard library only. The scripts in `osm_generator/` run without numpy, and the DEM
generator needs the same numbers, so nothing here may depend on anything else. Keep the
alignments free of floating-point randomness too: a river meander that comes out of a
closed formula gives both halves of the pipeline the identical polyline, and one that
comes out of an RNG does not.
"""
import json
import math
import os
import random

# --- where the map is -------------------------------------------------------------
# The projection anchor. Unchanged: moving it moves every node in map.osm relative to
# the heightmap, which no check downstream would catch.
LAT_CENTER = 43.0600
LON_CENTER = -95.2800

# --- how big it is ----------------------------------------------------------------
PLAYABLE_M = 8192.0
HALF_M = PLAYABLE_M / 2.0
CANVAS_M = 12288.0
OFFSET_M = (CANVAS_M - PLAYABLE_M) / 2.0      # 2048 m of margin on every side

# How far past the canvas the road, rail and river alignments run. Terrain features that
# stop at the canvas edge leave a valley dying in mid-air or a road ending at a cliff.
EXTEND_M = 300.0
EDGE_MIN = -OFFSET_M - EXTEND_M               # -2348
EDGE_MAX = PLAYABLE_M + OFFSET_M + EXTEND_M   # 10540

# A clean strip inside the playable boundary. Nothing the parcelling or the planting
# places may stand in it: no parcel, no shelterbelt, no yard. Only the things that have
# to leave the map cross it - the roads, the railway and the water. Without it a field is
# cut off square by the boundary and reads as half a field, and the ground the player
# sees at the edge is the ground the rim rises out of.
EDGE_CLEAR_M = 100.0

# --- the datum --------------------------------------------------------------------
# The elevation the whole canvas sits at before anything is sculpted into it. An empty
# layout comes out of the DEM generator flat at exactly this, everywhere - playable area
# and border alike - which is the blank sheet the new map gets built on.
#
# 20 m leaves 20 m of cut under the ground before the heightmap hits zero and clips, and
# the 16-bit centimetre encoding tops out at 620 m, so there is room either way.
BASE_ELEV_M = 20.0

# --- the non-playable rim ----------------------------------------------------------
# What the player sees past the boundary. The apron is left as the landscape made it, so
# the ground does not change character at the edge of play; mountains would start beyond
# it and close the horizon off. Zero while the map is flat: a rim built on a flat plate
# is a wall around a table, and the brief is a base at BASE_ELEV_M everywhere. Raise
# RIM_HEIGHT_M when there is terrain for it to grow out of.
RIM_APRON_M = 500.0           # flat apron between the boundary and the first slope
RIM_HEIGHT_M = 0.0            # highest summit, above the ground at the boundary

# --- how the two relate -----------------------------------------------------------
# Equirectangular about the centre. 111111.0 m per degree is the constant the rest of the
# pipeline was built with (1e7 m from the equator to the pole, over 90 degrees).
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))

SEED = 20250902

# --- the OSM vocabulary -----------------------------------------------------------
# Exactly what `osm_generator/visualize_osm.py` and `visualizer/create_3d_viewer.py` know
# how to draw. A way tagged with anything else is dropped by both renderers without a
# word, so it is worse than useless: it costs nodes and shows nothing. Both the generator
# and the checker read this list, so the two cannot drift apart.
RENDERED_TAGS = (('natural', 'water'), ('water', None), ('natural', 'wood'),
                 ('landuse', 'forest'), ('landuse', 'farmyard'),
                 ('landuse', 'farmland'), ('railway', None), ('highway', None))

# The road classes the renderers colour, keyed by the `kind` a corridor record carries.
HIGHWAY_CLASS = {'primary': 'primary', 'section': 'secondary',
                 'track': 'tertiary', 'street': 'tertiary'}


# ==================================================================================
# projection
# ==================================================================================
def local_to_global(x, y):
    """Playable metres -> (lat, lon). y grows southwards, so it subtracts."""
    return (LAT_CENTER - (y - HALF_M) / M_PER_DEG,
            LON_CENTER + (x - HALF_M) / M_PER_DEG_LON)


def global_to_local(lat, lon):
    """(lat, lon) -> playable metres. The inverse of local_to_global."""
    return (HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON,
            HALF_M - (lat - LAT_CENTER) * M_PER_DEG)


def bounds():
    """The four values of the OSM `<bounds>` element, as (minlat, minlon, maxlat,
    maxlon). The south-west corner is local (0, PLAYABLE_M); the north-east is
    (PLAYABLE_M, 0)."""
    minlat, minlon = local_to_global(0.0, PLAYABLE_M)
    maxlat, maxlon = local_to_global(PLAYABLE_M, 0.0)
    return minlat, minlon, maxlat, maxlon


def to_canvas(x, y):
    """Playable metres -> canvas metres (the DEM's frame, origin at its NW corner)."""
    return x + OFFSET_M, y + OFFSET_M


def from_canvas(xc, yc):
    return xc - OFFSET_M, yc - OFFSET_M


# ==================================================================================
# geometry helpers
# ==================================================================================
def polyline_length(pts):
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def ring_area_ha(ring):
    """Shoelace area in hectares. The ring may be given open or closed."""
    pts = ring[:-1] if len(ring) > 2 and math.dist(ring[0], ring[-1]) < 1e-9 else ring
    if len(pts) < 3:
        return 0.0
    twice = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return abs(twice) / 2.0 / 10000.0


def ring_perimeter(ring):
    pts = ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]
    return polyline_length(pts)


def close_ring(ring):
    return ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]


def rect_ring(x0, y0, x1, y1):
    """Axis-aligned rectangle as a closed ring, counter-clockwise in screen terms."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def clip_ring_to_rect(ring, x0, y0, x1, y1):
    """Sutherland-Hodgman against an axis-aligned rectangle.

    Clamping a ring's coordinates instead folds whatever hangs over the edge onto the
    edge itself, which is how a strip of gallery timber ended up with a run of nodes
    lying across a river channel. Clipping only ever puts a new vertex on the ring's
    own boundary, and cuts a straight edge where the ring leaves the rectangle.

    Returns a closed ring, or [] if nothing of it is left inside.
    """
    poly = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)
    for keep, cut in ((lambda p: p[0] >= x0, lambda a, b: _cut_x(a, b, x0)),
                      (lambda p: p[0] <= x1, lambda a, b: _cut_x(a, b, x1)),
                      (lambda p: p[1] >= y0, lambda a, b: _cut_y(a, b, y0)),
                      (lambda p: p[1] <= y1, lambda a, b: _cut_y(a, b, y1))):
        out = []
        for i, b in enumerate(poly):
            a = poly[i - 1]
            if keep(b):
                if not keep(a):
                    out.append(cut(a, b))
                out.append(b)
            elif keep(a):
                out.append(cut(a, b))
        poly = out
        if not poly:
            return []
    return poly + [poly[0]]


def _cut_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return (x, a[1] + t * (b[1] - a[1]))


def _cut_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return (a[0] + t * (b[0] - a[0]), y)


def ellipse_ring(cx, cy, a, b, rot_deg=0.0, n=48):
    c, s = math.cos(math.radians(rot_deg)), math.sin(math.radians(rot_deg))
    out = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        u, v = a * math.cos(t), b * math.sin(t)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return out


def point_in_ring(pt, ring):
    """Even-odd test. The ring may be open or closed."""
    x, y = pt
    pts = ring[:-1] if math.dist(ring[0], ring[-1]) < 1e-9 else ring
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xx = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < xx:
                inside = not inside
    return inside


def seg_point_dist(p, a, b):
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    if ll < 1e-12:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_polyline(p, pts):
    return min(seg_point_dist(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def catmull_rom(pts, per_seg=16):
    """Centripetal-ish Catmull-Rom through the control points, endpoints duplicated."""
    p = [pts[0]] + list(pts) + [pts[-1]]
    out = []
    for i in range(len(pts) - 1):
        p0, p1, p2, p3 = p[i], p[i + 1], p[i + 2], p[i + 3]
        for j in range(per_seg):
            t = j / per_seg
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t
                       + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in (0, 1)))
    out.append(tuple(pts[-1]))
    return out


def densify(pts, step):
    """Resample a polyline to roughly `step` between vertices, keeping the ends."""
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        d = step - carry
        while d < seg:
            t = d / seg
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            d += step
        carry = seg - (d - step)
    out.append(pts[-1])
    return out


def offset_polyline(pts, dist):
    """Offset a polyline sideways by `dist` (positive = left of travel).

    Only safe while `dist` stays under the radius of curvature: offset a meander by more
    than it bends and the ring folds through itself, and an even-odd fill then punches
    holes in the tightest bends. Reserves along water are stamped by distance to the
    centreline instead, never as offset polygons.
    """
    out = []
    n = len(pts)
    for i in range(n):
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ll = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ll, dx / ll
        out.append((pts[i][0] + dist * nx, pts[i][1] + dist * ny))
    return out


def buffer_ring(pts, half_w):
    """Closed ring around a polyline: left side out, right side back."""
    left = offset_polyline(pts, half_w)
    right = offset_polyline(pts, -half_w)
    return close_ring(left + right[::-1])


def clip_polyline(pts, x0, y0, x1, y1):
    """Keep the vertices inside the box, splitting into runs. Coarse (vertex level),
    which is all that is needed at 40 m sampling."""
    runs, cur = [], []
    for p in pts:
        if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _smoothstep(t):
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def playable_sdf(x, y):
    """Signed distance to the playable boundary: negative inside, positive out in the
    border. The clean strip is everything between -EDGE_CLEAR_M and 0."""
    dx = max(-x, x - PLAYABLE_M)
    dy = max(-y, y - PLAYABLE_M)
    if dx <= 0.0 and dy <= 0.0:
        return max(dx, dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def rng():
    """The layout's random stream. Seeded, so both halves of the pipeline draw the same
    numbers - and key any jitter to position rather than to iteration order, or adding
    one feature shifts every feature after it."""
    return random.Random(SEED)


# ==================================================================================
# the registries
# ==================================================================================
# Everything the world is made of, in four lists. They are empty: this is a blank map.
# Both generators read them and neither defines geometry of its own, so a feature added
# here appears in the terrain and in the vectors together, and a feature added to only
# one half of the pipeline is the bug this arrangement exists to prevent.

# Roads and railway. One record per alignment:
#     id             short stable key, used in messages and by the DEM's build order
#     name           what goes in the OSM `name` tag
#     kind           'primary' | 'section' | 'track' | 'street' | 'rail'
#                    - the class decides the OSM highway tag (HIGHWAY_CLASS), how wide
#                      the graded platform is and which corridor wins where two cross
#     axis           [(x, y), ...] centreline in playable metres, running from EDGE_MIN
#                    to EDGE_MAX if it leaves the map: an alignment that stops at the
#                    canvas edge ends in a cliff
#     half_width_m   half the running surface
#     feather_m      nominal width of the fill either side; the DEM widens it to
#                    max(feather_m, 1.5 * |dz| / tan(4 deg)) wherever the cut is deep,
#                    because in a smoothstep the steepest gradient is 1.5 * rise / run
#                    and a constant feather cuts a step under a deep platform
#     grade_max      ruling grade, as a fraction (rail is ~0.015, a section road ~0.06)
#     bridge_spans   [(s0, s1), ...] arc lengths along the axis carried on a deck, so
#                    the DEM leaves the channel alone there and the OSM tags bridge=yes
CORRIDORS = []

# Rivers, creeks and lakes. One record per body:
#     id, name       as above
#     kind           'river' | 'lake'
#     axis           centreline, for a watercourse
#     ring           closed shore, for a standing body
#     water_half_w   half-width of the drawn water surface; must equal the half-width at
#                    which the carved trough reaches the waterline, or the map paints
#                    water over dry bank
#     water_depth_m  water surface down to the bed. The profile the DEM carries along
#                    the channel is the **water surface**; the bed is cut this far under
#                    it, and the whole pipeline has to agree where that waterline is -
#                    the slope limiter, the platforms, the texture and the datum all
#                    take it as exempt ground.
WATER = []

# Levelled platforms: village and farm yards, industrial aprons, anything that wants
# flat ground under it. One record per pad:
#     id, name, kind
#     centre         (x, y)
#     size           (w, h) in metres
#     ring           closed outline, normally rect_ring around centre
#     feather_m      nominal edge; widened with the cut the same way a corridor's is
#     drain_grade    residual fall across the pad, so a yard drains instead of terracing
# Build order is load-bearing: pads are graded before corridors, or a road platform
# overwrites the pad and leaves a step at its edge.
PADS = []

# Tagged rings the OSM draws and the terrain mostly ignores: fields, farmyard polygons,
# woods and shelterbelts. One record per ring:
#     id, name
#     ring           closed outline in playable metres
#     tags           OSM tags; every one of them has to match RENDERED_TAGS or both
#                    renderers drop the way without a word
AREAS = []


def corridors():
    return list(CORRIDORS)


def water():
    return list(WATER)


def water_axes():
    """(polyline, name) for every watercourse that has a centreline. What the DEM's
    exempt-ground mask and the OSM's clearance checks both walk."""
    return [(w['axis'], w['name']) for w in WATER if w.get('axis')]


def pads():
    return list(PADS)


def areas():
    return list(AREAS)


def load_roughness(path=None):
    """Read `terrain_stats.json` and return a roughness lookup, or None if it is not
    there yet.

    The parcelling wants smaller fields on broken ground, and this is how it finds out
    where the broken ground is without re-deriving the terrain in a second
    implementation that would drift. The DEM has to run first; the OSM degrades
    gracefully if it has not. On a flat map every cell reads 0.
    """
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
        c = 0 if c < 0 else (n - 1 if c >= n else c)
        r = 0 if r < 0 else (n - 1 if r >= n else r)
        return rough[r * n + c]
    return lookup


# ==================================================================================
# self-check
# ==================================================================================
def validate():
    """Everything that has to be true about the layout before anything is built on it.
    Returns a list of complaints; empty means the layout is sound. Both generators
    refuse to run while it complains.

    Add a rule here when you find a placement mistake, rather than only moving the
    coordinates: on the map this replaces, three of the seven farmsteads turned out to
    be misplaced once the road-clearance rule existed, and only one of them had been
    visible.

    The checks below are the ones that hold on any map, empty or not. The ones that
    belong to particular features go in beside those features as they are added.
    """
    bad = []

    ids = [r['id'] for r in CORRIDORS + WATER + PADS + AREAS]
    for i in sorted(set(ids)):
        if ids.count(i) > 1:
            bad.append(f"{i}: used by {ids.count(i)} records - ids must be unique")

    for c in CORRIDORS:
        if c['kind'] not in HIGHWAY_CLASS and c['kind'] != 'rail':
            bad.append(f"{c['id']}: unknown corridor class {c['kind']!r}")
        if len(c['axis']) < 2:
            bad.append(f"{c['id']}: an alignment needs at least two points")

    for w in WATER:
        if not w.get('axis') and not w.get('ring'):
            bad.append(f"{w['id']}: water with neither a centreline nor a shore")

    m = EDGE_CLEAR_M
    for p in PADS:
        x0 = p['centre'][0] - p['size'][0] / 2
        x1 = p['centre'][0] + p['size'][0] / 2
        y0 = p['centre'][1] - p['size'][1] / 2
        y1 = p['centre'][1] + p['size'][1] / 2
        if x0 < m or y0 < m or x1 > PLAYABLE_M - m or y1 > PLAYABLE_M - m:
            bad.append(f"{p['id']}: stands in the {m:.0f} m clean strip along the "
                       "boundary")

    for a in AREAS:
        if not a['tags']:
            bad.append(f"{a['id']}: untagged - both renderers would drop it")
        elif not any(k in a['tags'] and (v is None or a['tags'][k] == v)
                     for k, v in RENDERED_TAGS):
            bad.append(f"{a['id']}: tagged {a['tags']} - neither renderer draws that")
        if len(a['ring']) < 4 or math.dist(a['ring'][0], a['ring'][-1]) > 1e-9:
            bad.append(f"{a['id']}: ring does not close on its first point")
        if max(playable_sdf(x, y) for x, y in a['ring']) > -m:
            bad.append(f"{a['id']}: reaches into the {m:.0f} m clean strip along the "
                       "boundary")
    return bad


def summary():
    """One-line description of the layout, for the generators to print."""
    if not (CORRIDORS or WATER or PADS or AREAS):
        return (f"{PLAYABLE_M:.0f} m playable on a {CANVAS_M:.0f} m canvas, empty, "
                f"flat at {BASE_ELEV_M:.0f} m")
    return (f"{PLAYABLE_M:.0f} m playable, {len(CORRIDORS)} corridors, "
            f"{len(WATER)} water bodies, {len(PADS)} pads, {len(AREAS)} areas")


if __name__ == '__main__':
    print("=== map_layout self-check ===")
    print("  ", summary())
    sw = global_to_local(*local_to_global(0.0, PLAYABLE_M))
    ne = global_to_local(*local_to_global(PLAYABLE_M, 0.0))
    print(f"   centre {LAT_CENTER:.4f}, {LON_CENTER:.4f}; the projection round-trips "
          f"the corners to ({sw[0]:.3f}, {sw[1]:.3f}) and ({ne[0]:.3f}, {ne[1]:.3f})")
    print(f"   canvas metres run {-OFFSET_M:.0f} .. {PLAYABLE_M + OFFSET_M:.0f}, "
          f"alignments out to {EDGE_MIN:.0f} .. {EDGE_MAX:.0f}")
    problems = validate()
    if problems:
        print("\n   PROBLEMS")
        for p in problems:
            print("    -", p)
    else:
        print("\n   layout is sound")
