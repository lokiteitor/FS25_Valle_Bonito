#!/usr/bin/env python3
"""The layout of the map: where everything is, in playable metres.

This is the one source of truth shared by the two halves of the pipeline. The DEM
generator sculpts terrain around the geometry defined here; the OSM generator writes the
same geometry out as vectors. If the two disagree - a river carved where no river is
drawn, a farm pad flattened where no farmyard exists - the map is broken in a way that is
invisible in either output on its own, so both read their geometry from this module and
neither is allowed to invent its own.

The landscape is northwest Iowa: Clay County, around Royal, on the western edge of the
Des Moines Lobe. Gently rolling till plain, closed prairie-pothole depressions, one river
in a broad shallow valley with a glacial lake beside it, and the Public Land Survey grid
laid over all of it - section roads every mile, dead straight, meeting at right angles.

Coordinates are playable metres: x east, y south from the north edge, so the centre of
the playable area is (4096, 4096). The DEM canvas is larger than the playable area, so
canvas coordinates run from -2048 to 10240 in the same frame.

Standard library only. The scripts in `osm_generator/` run without numpy, and the DEM
generator needs the same numbers, so nothing here may depend on anything else. There is
no floating-point randomness in the alignments either: the river meanders come from a
closed formula, so both halves of the pipeline get the identical polyline.
"""
import json
import math
import os
import random

# --- where the map is -------------------------------------------------------------
# Royal, Clay County, Iowa. 431 m at the town, 411 m at Lost Island Lake a few miles
# northeast: about 30 m of relief over the whole neighbourhood, which is what the terrain
# here reproduces.
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

# --- how the two relate -----------------------------------------------------------
# Equirectangular about the centre. 111111.0 m per degree is the constant the rest of the
# pipeline was built with (1e7 m from the equator to the pole, over 90 degrees).
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))

SEED = 20250902

# --- the Public Land Survey grid --------------------------------------------------
# One mile between section roads. Five lines fit on each axis of an 8192 m square, and
# the middle one lands exactly on the centre of the map.
SECTION_M = 1609.344
GRID = tuple(HALF_M + k * SECTION_M for k in (-2, -1, 0, 1, 2))

MAIN_ROAD_Y = HALF_M          # 420th Street, the primary, dead straight east-west
RAILWAY_X = HALF_M            # the branch line, dead straight north-south
# The railway sits on the middle north-south section line, so the road that would run
# there is offset by the width of the right of way and runs alongside it, the way a
# section road parallels a track in Iowa. Without this the two platforms fight over the
# same ground and the 1.5% rail ruling grade cannot be held.
RAIL_ROW_M = 40.0
RAIL_PARALLEL_X = RAILWAY_X + RAIL_ROW_M

# Iowa names its rural grid by distance: streets run east-west and count up going south,
# avenues run north-south and count up going east.
STREET_NAMES = ("400th Street", "410th Street", "420th Street",
                "430th Street", "440th Street")
AVENUE_NAMES = ("250th Avenue", "260th Avenue", "270th Avenue",
                "280th Avenue", "290th Avenue")

# --- the river --------------------------------------------------------------------
# Cross-section of the valley, all half-widths from the centreline. Every step is a
# smoothstep, whose maximum gradient is 1.5 * rise / run - which is what sets the bank
# width: 2.20 m over 30 m is 11%, or 6.3 degrees, inside the 8 degree ceiling. A 18 m
# bank would be 11.7 degrees and out of spec.
RIVER = dict(
    bed_half_w=18.0,          # flat bed, water sits here
    bank_w=30.0, bank_h=2.20,  # inner bank up to the floodplain
    floodplain_w=260.0, floodplain_cross=0.0040,
    wall_w=420.0, wall_h=11.0,  # the valley side proper
    ext_grade=0.020,          # linear continuation, so the carve always closes
    incision_m=14.4,          # total, thalweg to valley rim
    grade_nom=0.75e-3, grade_min=0.30e-3, grade_max=2.50e-3,
    softmin_k=3.0,
    water_half_w=22.0,        # what gets drawn as water in the OSM
    wood_half_w=95.0,         # gallery timber along the banks
    # Out to the edge of the floodplain (18 + 30 + 260) plus 40 m of headland: no field
    # may be laid inside the basin the terrain cuts for the river.
    riparian_half_w=348.0,
)

# Control points of the river's general course, west edge to south edge, keeping to the
# southwest so the main road never has to cross it and the northern two thirds of the map
# stay open for extensive farming.
RIVER_TREND = [
    (EDGE_MIN, 5250.0), (-1200.0, 5320.0), (0.0, 5420.0), (1000.0, 5600.0),
    (2000.0, 5850.0), (3000.0, 6150.0), (4000.0, 6500.0), (4800.0, 6950.0),
    (5300.0, 7450.0), (5550.0, 7950.0), (5680.0, PLAYABLE_M), (5760.0, 8900.0),
    (5820.0, EDGE_MAX),
]
# Meanders: two harmonics on the normal, no randomness, so the OSM and the DEM get the
# same polyline down to the last bit.
RIVER_MEANDER = ((210.0, 950.0, 0.0), (90.0, 430.0, 1.1))   # amplitude, period, phase

# --- the lake ---------------------------------------------------------------------
# A moderate glacial lake, the size and depth of Lost Island Lake scaled to the map, and
# sitting **on the river** rather than beside it: the main stem runs in at the head and
# out at the foot, which is what makes it a lake and not a pond. Its centre is not a
# constant - it is the point of the river axis at LAKE_AT_S, so the water cannot drift
# off the channel when the alignment changes, and the long axis follows the flow.
# 40 m deep, which is far deeper than anything else on the map and needs a profile in
# two stages: a shelf you can walk into, then the drop. All of the steep part is under
# water, where no machine goes; the visible shore stays under 4 degrees.
LAKE = dict(semi_a=560.0, semi_b=340.0, max_depth=40.0, flat_r=0.35,
            shelf_r=0.86, shelf_depth=2.5,
            apron_r=2.00, apron_grade=0.008, softmin_k=2.5)
# Arc length along the river axis. It is measured from where the alignment starts, which
# is 2.3 km outside the canvas, so this is not the same as a distance across the map: it
# puts the lake at roughly (1790, 5860), in the middle of the floodplain.
LAKE_AT_S = 6600.0
# A perfect ellipse reads as a reservoir, not as a kettle lake, and its apron shows up in
# the terrain as concentric rings. Two harmonics on the radius break that up. The DEM
# applies the same modulation, so shore and basin agree exactly.
LAKE_SHORE = ((0.085, 3, 1.0), (0.050, 5, 2.2))     # amplitude, lobes, phase
LAKE_MARGIN_R = 1.22          # no field inside this much of the shore radius

# --- the tributary ------------------------------------------------------------------
# A second, smaller stream off the northern uplands, joining the lake at its head. One
# river through a lake is drainage; two watercourses feeding it is a catchment, and it
# gives the northwest quarter a reason for its low ground.
CREEK = dict(bed_half_w=7.0, bank_w=14.0, bank_h=1.2, floodplain_w=70.0,
             floodplain_cross=0.0040, wall_w=140.0, wall_h=3.6, ext_grade=0.020,
             incision_m=4.8, grade_min=0.60e-3, grade_max=6.0e-3, softmin_k=2.0,
             water_half_w=9.0, wood_half_w=55.0, riparian_half_w=125.0)
CREEK_TREND = [(3080.0, 2320.0), (2900.0, 3020.0), (2620.0, 3640.0),
               (2330.0, 4260.0), (2140.0, 4940.0)]
# Kept gentle on purpose: a creek that wanders faster than its trend runs doubles back
# across a north-south road and needs three culverts where one would do.
CREEK_MEANDER = ((52.0, 780.0, 0.4), (20.0, 330.0, 2.0))

# --- corridors --------------------------------------------------------------------
# half_width: the flat running surface. feather: nominal transition, widened where the
# cut or fill is deep (see the DEM generator). max_grade: longitudinal ruling grade.
CORRIDOR_CLASS = {
    'rail':      dict(half_width_m=9.0,  feather_m=90.0, max_grade=0.015,
                      max_cross=math.tan(math.radians(5.5))),
    'primary':   dict(half_width_m=11.0, feather_m=75.0, max_grade=0.040,
                      max_cross=math.tan(math.radians(5.0))),
    'section':   dict(half_width_m=7.0,  feather_m=45.0, max_grade=0.060,
                      max_cross=math.tan(math.radians(6.0))),
    'track':     dict(half_width_m=4.0,  feather_m=30.0, max_grade=0.080,
                      max_cross=math.tan(math.radians(7.0))),
    'street':    dict(half_width_m=5.0,  feather_m=30.0, max_grade=0.060,
                      max_cross=math.tan(math.radians(6.0))),
}

# Which corridors are allowed to bridge the river. Everything else that would meet the
# water is cut back short of the floodplain instead: in real grid country plenty of
# section roads simply stop at the river.
BRIDGED = ('rail_main', 'road_ew_440th', 'road_ns_250th')
BRIDGE_ABUTMENT_M = 45.0      # deck extends this far past the channel each side
ROAD_STUB_GAP_M = 250.0       # how far short of the centreline a cut road stops

# --- settlements ------------------------------------------------------------------
# Three small rural communities on the primary. The middle one is at the level crossing,
# which is why the town is there at all. Farm yards are deliberately larger than village
# pads: a modern Iowa operation needs more flat ground than a hamlet does.
VILLAGE_SPEC = [
    ('Village Hollandale', GRID[0], MAIN_ROAD_Y, 420.0, 300.0),
    ('Village Royal',      GRID[2], MAIN_ROAD_Y, 520.0, 340.0),
    ('Village Ashgrove',   GRID[4], MAIN_ROAD_Y, 400.0, 290.0),
]

# Seven holdings spread over the whole map, not clustered around the towns. Each is a
# rectangle of workable flat ground big enough for sheds, silos, yards and machinery.
FARM_SPEC = [
    ('Bergman Farms (main)',    3420.0, 1960.0, 620.0, 430.0),
    ('Nordstrom Dairy (cows)',  6320.0, 1520.0, 560.0, 400.0),
    ('Kleinsasser (pigs)',      1660.0, 1640.0, 540.0, 380.0),
    ('Prairie Gold (chickens)', 6500.0, 3260.0, 520.0, 380.0),
    ('Vanden Berg (sheep)',     1720.0, 4820.0, 520.0, 380.0),
    ('Sundance Stables (horses)', 5020.0, 5320.0, 540.0, 390.0),
    ('Ockenga Farms (mixed)',   6820.0, 6030.0, 580.0, 410.0),
]
VILLAGE_FEATHER_M = 60.0
FARM_FEATHER_M = 90.0
PAD_DRAIN_GRADE = 0.005       # residual fall, so a yard drains and does not terrace
PAD_RIVER_CLEAR_M = 380.0     # no pad may sit inside the floodplain
# No section road, primary or railway may run through a farmyard. A village straddles the
# highway by design - that is its main street - but a yard with a public road across the
# middle of it is a yard split in two.
PAD_ROAD_CLEAR_M = 70.0

# --- prairie potholes -------------------------------------------------------------
POTHOLE_N = 95
POTHOLE_R = (55.0, 175.0)
POTHOLE_MIN_GAP_M = 260.0
POTHOLE_CLEAR_M = 120.0       # clearance from corridors and pads

# --- field parcelling -------------------------------------------------------------
ROW_CLEAR_M = 14.0            # field edge to road centreline
FIELD_MAX_HA = 100.0          # hard cap, never exceeded
FIELD_MAX_COUNT = 200         # hard cap on how many parcels the map carries
FIELD_MIN_HA = 3.0
FIELD_MIN_WIDTH_M = 70.0      # narrowest a parcel may get
FIELD_MARGIN_M = 2.0          # inset, so neighbours show a 4 m boundary
FIELD_ASPECT_MAX = 5.5
GRID_CELL_M = 32.0            # occupancy raster for clipping against water and pads

# --- windbreaks ---------------------------------------------------------------------
# Tree rows. Two sides of every yard - north and west, the sides the winter wind comes
# from - and a scattering of field-edge belts along the section grid.
WINDBREAK_W_M = 24.0
WINDBREAK_YARD_GAP_M = 34.0
WINDBREAK_EDGE_INSET_M = 26.0
WATER_CLEAR_M = 30.0          # a belt stands this far back from any watercourse


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
    """Offset a polyline sideways by `dist` (positive = left of travel). Good enough
    for a river bank: the curvature here is far gentler than the offset."""
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


# ==================================================================================
# water
# ==================================================================================
_river_cache = None


def river_axis():
    """The river centreline, west edge to south edge, densified to 40 m and running past
    the canvas at both ends. Deterministic: the meanders are a closed formula."""
    global _river_cache
    if _river_cache is not None:
        return _river_cache

    spine = densify(catmull_rom(RIVER_TREND, per_seg=14), 40.0)
    out = []
    s = 0.0
    for i, (x, y) in enumerate(spine):
        if i:
            s += math.dist(spine[i - 1], spine[i])
        if 0 < i < len(spine) - 1:
            dx = spine[i + 1][0] - spine[i - 1][0]
            dy = spine[i + 1][1] - spine[i - 1][1]
            ll = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / ll, dx / ll
            a = sum(amp * math.sin(2.0 * math.pi * s / per + ph)
                    for amp, per, ph in RIVER_MEANDER)
            out.append((x + a * nx, y + a * ny))
        else:
            out.append((x, y))
    _river_cache = out
    return out


def lake_shore_scale(theta):
    """Radial modulation of the lake shore at parametric angle theta."""
    return 1.0 + sum(a * math.sin(k * theta + ph) for a, k, ph in LAKE_SHORE)


def lake_centre():
    """Where the lake sits: a point of the river axis, not a constant of its own."""
    return _lake_frame()[0]


def lake_rot_deg():
    """The lake's long axis follows the river through it."""
    return _lake_frame()[1]


_lake_frame_cache = None


def _lake_frame():
    global _lake_frame_cache
    if _lake_frame_cache is None:
        axis = river_axis()
        s = polyline_arclen_list(axis)
        i = min(range(len(s)), key=lambda k: abs(s[k] - LAKE_AT_S))
        a = axis[max(0, i - 2)]
        b = axis[min(len(axis) - 1, i + 2)]
        _lake_frame_cache = (axis[i],
                             math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))
    return _lake_frame_cache


def polyline_arclen_list(pts):
    out = [0.0]
    for i in range(len(pts) - 1):
        out.append(out[-1] + math.dist(pts[i], pts[i + 1]))
    return out


def lake_ring(n=72):
    cx, cy = lake_centre()
    a, b = LAKE['semi_a'], LAKE['semi_b']
    rot = lake_rot_deg()
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    out = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        m = lake_shore_scale(t)
        u, v = a * m * math.cos(t), b * m * math.sin(t)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return out


_creek_cache = None


def creek_axis():
    """The tributary, north uplands to the head of the lake. Same closed-form meanders
    as the river, so it too is identical on both sides of the pipeline."""
    global _creek_cache
    if _creek_cache is not None:
        return _creek_cache
    trend = list(CREEK_TREND)
    # the last control point is the lake shore itself, so the two always meet
    cx, cy = lake_centre()
    ux = trend[-1][0] - cx
    uy = trend[-1][1] - cy
    ll = math.hypot(ux, uy) or 1.0
    r = _lake_radius_towards(ux / ll, uy / ll)
    trend.append((cx + ux / ll * r * 0.85, cy + uy / ll * r * 0.85))

    spine = densify(catmull_rom(trend, per_seg=12), 30.0)
    out = []
    s = 0.0
    for i, (x, y) in enumerate(spine):
        if i:
            s += math.dist(spine[i - 1], spine[i])
        if 0 < i < len(spine) - 1:
            dx = spine[i + 1][0] - spine[i - 1][0]
            dy = spine[i + 1][1] - spine[i - 1][1]
            n = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / n, dx / n
            a = sum(amp * math.sin(2.0 * math.pi * s / per + ph)
                    for amp, per, ph in CREEK_MEANDER)
            out.append((x + a * nx, y + a * ny))
        else:
            out.append((x, y))
    _creek_cache = out
    return out


def water_axes():
    """Every watercourse a corridor might have to get across."""
    return ((river_axis(), 'river'), (creek_axis(), 'creek'))


def _lake_radius_towards(ux, uy):
    """Distance from the lake centre to its shore along the unit vector (ux, uy)."""
    rot = lake_rot_deg()
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    u = ux * c + uy * s
    v = -ux * s + uy * c
    a, b = LAKE['semi_a'], LAKE['semi_b']
    q = math.hypot(u / a, v / b) or 1.0
    return lake_shore_scale(math.atan2(v / b, u / a)) / q


def river_water_ring():
    """The channel as a polygon, upstream reach and downstream reach.

    The channel stops at the lake shore: the lake is its own polygon, and two overlapping
    water areas fight over the same ground in the editor.
    """
    out = []
    for axis, half in ((river_axis(), RIVER['water_half_w']),
                       (creek_axis(), CREEK['water_half_w'])):
        trimmed = [p for p in axis if not _in_lake(p, 0.98)]
        for run in _split_runs(trimmed, axis):
            for piece in clip_polyline(run, -60.0, -60.0,
                                       PLAYABLE_M + 60.0, PLAYABLE_M + 60.0):
                if polyline_length(piece) > 120.0:
                    out.append(buffer_ring(piece, half))
    return out


def _in_lake(pt, r_max=1.0):
    cx, cy = lake_centre()
    rot = lake_rot_deg()
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    u = (pt[0] - cx) * c + (pt[1] - cy) * s
    v = -(pt[0] - cx) * s + (pt[1] - cy) * c
    a, b = LAKE['semi_a'], LAKE['semi_b']
    q = math.hypot(u / a, v / b)
    return q <= r_max * lake_shore_scale(math.atan2(v / b, u / a))


def _split_runs(kept, axis):
    """Group the surviving vertices back into contiguous runs."""
    runs, cur = [], []
    idx = {id(p): i for i, p in enumerate(axis)}
    last = None
    for p in kept:
        i = idx[id(p)]
        if last is not None and i != last + 1:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
        cur.append(p)
        last = i
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def river_woods(chunk_m=700.0, keep_every=2):
    """Gallery timber, on the banks and only on the banks.

    Buffering the centreline puts the trees in the river: the band is centred on the
    channel, so the inner 22 m of it is open water. These are two strips instead, each
    starting outside the water's edge and running out to the timber line, and the sides
    alternate along the reach - a prairie river carries trees in stretches, first one
    bank and then the other, not as a continuous double hedge.
    """
    inner = RIVER['water_half_w'] + 10.0
    left = _bank_strips(river_axis(), inner, RIVER['wood_half_w'], chunk_m, side=+1)
    right = _bank_strips(river_axis(), inner, RIVER['wood_half_w'], chunk_m, side=-1)
    out = []
    for i, ring in enumerate(left):
        if i % keep_every == 0:
            out.append(ring)
    for i, ring in enumerate(right):
        if i % keep_every == 1:
            out.append(ring)
    return out


def _bank_strips(axis, inner, outer, chunk_m, side=+1):
    """Chunked strips alongside a watercourse, from `inner` to `outer` off the axis.

    Vertices inside the lake are dropped first: the lake is its own polygon, and a strip
    of timber laid across open water is worse than no timber at all.
    """
    kept = [p for p in axis if not _in_lake(p, 1.06)]
    out = []
    for run in _split_runs(kept, axis):
        for piece in clip_polyline(run, -40.0, -40.0,
                                   PLAYABLE_M + 40.0, PLAYABLE_M + 40.0):
            acc, cur = 0.0, [piece[0]]
            chunks = []
            for i in range(1, len(piece)):
                cur.append(piece[i])
                acc += math.dist(piece[i - 1], piece[i])
                if acc >= chunk_m:
                    chunks.append(cur)
                    cur, acc = [piece[i]], 0.0
            if len(cur) >= 2:
                chunks.append(cur)
            for ch in chunks:
                if polyline_length(ch) < 90.0:
                    continue
                a = offset_polyline(ch, side * inner)
                b = offset_polyline(ch, side * outer)
                out.append(close_ring(a + b[::-1]))
    return out


# ==================================================================================
# corridors
# ==================================================================================
def _straight(x0, y0, x1, y1, step=40.0):
    return densify([(x0, y0), (x1, y1)], step)


def _axis_water_hits(axis, water):
    """Arc lengths along `axis` where it meets a watercourse, plus the crossing point."""
    hits = []
    s = 0.0
    for i in range(len(axis) - 1):
        a, b = axis[i], axis[i + 1]
        seg = math.dist(a, b)
        for j in range(len(water) - 1):
            p = _seg_intersect(a, b, water[j], water[j + 1])
            if p is not None:
                hits.append((s + math.dist(a, p), p))
        s += seg
    hits.sort()
    # collapse duplicates from a meander doubling back within a few metres
    out = []
    for h in hits:
        if not out or h[0] - out[-1][0] > 120.0:
            out.append(h)
    return out


def _axis_river_hits(axis):
    """Every watercourse crossing on an alignment, so nothing gets graded through water
    by accident."""
    out = []
    for water, _kind in water_axes():
        out.extend(_axis_water_hits(axis, water))
    return sorted(out)


def _seg_intersect(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (a[0] + t * r[0], a[1] + t * r[1])
    return None


def _sub_polyline(axis, s0, s1):
    """The piece of `axis` between arc lengths s0 and s1."""
    out = []
    s = 0.0
    for i in range(len(axis) - 1):
        a, b = axis[i], axis[i + 1]
        seg = math.dist(a, b)
        if seg < 1e-9:
            continue
        for (lo, hi) in ((s, s + seg),):
            if hi < s0 or lo > s1:
                continue
            t0 = max(0.0, (s0 - lo) / seg)
            t1 = min(1.0, (s1 - lo) / seg)
            p0 = (a[0] + t0 * (b[0] - a[0]), a[1] + t0 * (b[1] - a[1]))
            p1 = (a[0] + t1 * (b[0] - a[0]), a[1] + t1 * (b[1] - a[1]))
            if not out:
                out.append(p0)
            out.append(p1)
        s += seg
    return out if len(out) >= 2 else []


def _resolve_water(cid, kind, name, axis, ref=None):
    """Split or bridge a corridor where it meets the river.

    Every corridor that meets the water has to say what it does about it. A bridge gets a
    span the DEM leaves untouched; anything else is cut back short of the floodplain, so
    it ends at the river the way plenty of section roads do. The alternative - grading
    straight through - builds a fifteen metre earth dam across the channel.
    """
    river_hits = _axis_water_hits(axis, river_axis())
    creek_hits = _axis_water_hits(axis, creek_axis())
    total = polyline_length(axis)
    base = dict(kind=kind, name=name, ref=ref, **CORRIDOR_CLASS[kind])

    # The creek is small enough to culvert, so every road crosses it; the alternative is
    # a four metre earth dam on the tributary, which is worse than a pipe.
    spans = [(max(0.0, s - (CREEK['water_half_w'] + 25.0)),
              min(total, s + (CREEK['water_half_w'] + 25.0))) for s, _ in creek_hits]

    if not river_hits:
        return [dict(id=cid, axis=axis, bridge_spans=spans, **base)]

    if cid in BRIDGED:
        half = RIVER['water_half_w'] + BRIDGE_ABUTMENT_M
        spans += [(max(0.0, s - half), min(total, s + half)) for s, _ in river_hits]
        return [dict(id=cid, axis=axis, bridge_spans=sorted(spans), **base)]

    cuts = [0.0]
    for s, _ in river_hits:
        cuts.extend([s - ROAD_STUB_GAP_M, s + ROAD_STUB_GAP_M])
    cuts.append(total)
    out = []
    for k in range(0, len(cuts) - 1, 2):
        piece = _sub_polyline(axis, cuts[k], cuts[k + 1])
        if len(piece) >= 2 and polyline_length(piece) > 300.0:
            suffix = '' if len(cuts) <= 3 else f"_{k // 2}"
            lo = cuts[k]
            keep = [(max(0.0, s0 - lo), s1 - lo) for s0, s1 in spans
                    if cuts[k] < 0.5 * (s0 + s1) < cuts[k + 1]]
            out.append(dict(id=cid + suffix, axis=piece, bridge_spans=keep, **base))
    return out


_corridor_cache = None


def corridors():
    """Every graded alignment on the map, in the order the DEM must build them: the
    tightest ruling grade first, because everything later pins itself to it."""
    global _corridor_cache
    if _corridor_cache is not None:
        return _corridor_cache

    out = []
    # 1. the railway: straight north-south through the middle
    out += _resolve_water('rail_main', 'rail', 'C&NW Sheldon Subdivision',
                          _straight(RAILWAY_X, EDGE_MIN, RAILWAY_X, EDGE_MAX))
    # 2. the primary: straight east-west through the middle
    out += _resolve_water('road_ew_420th', 'primary', STREET_NAMES[2],
                          _straight(EDGE_MIN, MAIN_ROAD_Y, EDGE_MAX, MAIN_ROAD_Y),
                          ref='B40')
    # 3. the rest of the section grid
    for k, y in enumerate(GRID):
        if abs(y - MAIN_ROAD_Y) < 1.0:
            continue
        cid = f"road_ew_{STREET_NAMES[k].split()[0]}"
        out += _resolve_water(cid, 'section', STREET_NAMES[k],
                              _straight(EDGE_MIN, y, EDGE_MAX, y))
    for k, x in enumerate(GRID):
        xx = RAIL_PARALLEL_X if abs(x - RAILWAY_X) < 1.0 else x
        cid = f"road_ns_{AVENUE_NAMES[k].split()[0]}"
        out += _resolve_water(cid, 'section', AVENUE_NAMES[k],
                              _straight(xx, EDGE_MIN, xx, EDGE_MAX))
    # 4. farm accesses: a straight spur from the nearest section road to each yard
    for pad in farm_pads():
        spur = _farm_spur(pad)
        if spur:
            out.append(dict(id=f"track_{pad['id']}", kind='track',
                            name=f"{pad['name']} lane", ref=None, axis=spur,
                            bridge_spans=[], **CORRIDOR_CLASS['track']))
    # 5. village streets
    for pad in village_pads():
        for i, st in enumerate(_village_streets(pad)):
            out.append(dict(id=f"street_{pad['id']}_{i}", kind='street',
                            name=st['name'], ref=None, axis=st['axis'],
                            bridge_spans=[], **CORRIDOR_CLASS['street']))
    _corridor_cache = out
    return out


def _farm_spur(pad):
    """Straight lane from a yard to whichever grid line is nearest, at right angles."""
    cx, cy = pad['centre']
    hw, hh = pad['size'][0] / 2.0, pad['size'][1] / 2.0
    best = None
    for x in list(GRID) + [RAIL_PARALLEL_X]:
        d = abs(cx - x)
        if best is None or d < best[0]:
            best = (d, 'ns', x)
    for y in GRID:
        d = abs(cy - y)
        if d < best[0]:
            best = (d, 'ew', y)
    d, orient, v = best
    if orient == 'ns':
        edge = cx + hw if v > cx else cx - hw
        return _straight(edge, cy, v, cy, step=25.0)
    edge = cy + hh if v > cy else cy - hh
    return _straight(cx, edge, cx, v, step=25.0)


def _village_streets(pad):
    """A small orthogonal street grid inside a village: one cross street either side of
    the highway, and a back lane parallel to it."""
    cx, cy = pad['centre']
    w, h = pad['size']
    out = []
    for k, dx in enumerate((-w * 0.28, w * 0.28)):
        out.append(dict(name=('Elm Street' if k == 0 else 'Church Street'),
                        axis=_straight(cx + dx, cy - h * 0.42,
                                       cx + dx, cy + h * 0.42, step=20.0)))
    out.append(dict(name='Depot Street',
                    axis=_straight(cx - w * 0.40, cy + h * 0.30,
                                   cx + w * 0.40, cy + h * 0.30, step=20.0)))
    return out


def crossings():
    """Where the railway meets a road. These are the points the DEM levels first, so the
    two profiles meet at the same height instead of arguing about it."""
    out = []
    rail = [c for c in corridors() if c['kind'] == 'rail']
    for c in corridors():
        if c['kind'] in ('rail', 'track', 'street'):
            continue
        for r in rail:
            for i in range(len(c['axis']) - 1):
                for j in range(len(r['axis']) - 1):
                    p = _seg_intersect(c['axis'][i], c['axis'][i + 1],
                                       r['axis'][j], r['axis'][j + 1])
                    if p is not None:
                        out.append(dict(id=f"x_{c['id']}_{r['id']}", xy=p,
                                        corridor_ids=(c['id'], r['id']),
                                        pad_radius_m=(260.0 if c['kind'] == 'primary'
                                                      else 120.0)))
    return out


# ==================================================================================
# settlements
# ==================================================================================
def village_pads():
    out = []
    for i, (name, cx, cy, w, h) in enumerate(VILLAGE_SPEC):
        out.append(dict(id=f"village{i}", kind='village', name=name,
                        centre=(cx, cy), size=(w, h),
                        ring=rect_ring(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                        feather_m=VILLAGE_FEATHER_M, drain_grade=PAD_DRAIN_GRADE,
                        max_drop_m=0.8))
    return out


def farm_pads():
    out = []
    for i, (name, cx, cy, w, h) in enumerate(FARM_SPEC):
        out.append(dict(id=f"farm{i}", kind='farm', name=name,
                        centre=(cx, cy), size=(w, h),
                        ring=rect_ring(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                        feather_m=FARM_FEATHER_M, drain_grade=PAD_DRAIN_GRADE,
                        max_drop_m=1.2))
    return out


def pads():
    return village_pads() + farm_pads()


# ==================================================================================
# prairie potholes
# ==================================================================================
_pothole_cache = None


def potholes():
    """Shallow closed depressions - the signature of the Des Moines Lobe.

    They are 0.45 to 1.7 m deep over 55 to 175 m, which works out at under one degree of
    slope: still perfectly farmable, and the reason the map does not read as generic
    rolling noise. They are not a drainage defect and nothing downstream should treat
    them as one.
    """
    global _pothole_cache
    if _pothole_cache is not None:
        return _pothole_cache

    rng = random.Random(SEED + 7)
    river = river_axis()
    lake = lake_centre()
    blockers = [(p['centre'], p['size']) for p in pads()]
    axes = [c['axis'] for c in corridors() if c['kind'] in ('rail', 'primary', 'section')]

    out = []
    tries = 0
    while len(out) < POTHOLE_N and tries < POTHOLE_N * 400:
        tries += 1
        x = rng.uniform(EDGE_MIN + 200.0, EDGE_MAX - 200.0)
        y = rng.uniform(EDGE_MIN + 200.0, EDGE_MAX - 200.0)
        r = rng.uniform(*POTHOLE_R)
        if dist_to_polyline((x, y), river) < 500.0:
            continue
        if math.hypot(x - lake[0], y - lake[1]) < LAKE['semi_a'] * 2.4:
            continue
        if any(abs(x - c[0]) < s[0] / 2 + POTHOLE_CLEAR_M and
               abs(y - c[1]) < s[1] / 2 + POTHOLE_CLEAR_M for c, s in blockers):
            continue
        if any(dist_to_polyline((x, y), a) < POTHOLE_CLEAR_M for a in axes):
            continue
        if any(math.hypot(x - p['centre'][0], y - p['centre'][1]) < POTHOLE_MIN_GAP_M
               for p in out):
            continue
        depth = 0.45 + 0.0105 * (r - POTHOLE_R[0])
        out.append(dict(centre=(x, y), radius=r, depth=depth))
    _pothole_cache = out
    return out


def lake_margin(n=72):
    """A shore margin round the lake. The basin is 40 m deep; the ground for a good way
    back off it belongs to the water, not to a crop."""
    cx, cy = lake_centre()
    a, b = LAKE['semi_a'], LAKE['semi_b']
    rot = lake_rot_deg()
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    out = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        m = lake_shore_scale(t) * LAKE_MARGIN_R
        u, v = a * m * math.cos(t), b * m * math.sin(t)
        out.append((cx + u * c - v * s, cy + u * s + v * c))
    out.append(out[0])
    return [out]


def wet_reserves(n=8):
    """The deepest potholes nearest the floodplain.

    These are not emitted as features - `natural=wetland` is outside the vocabulary the
    renderers understand, and a way they cannot draw is a way that silently disappears.
    They are ground the parcelling keeps out of cultivation, which is what they amount to
    on the map anyway: too wet to crop, left as slough grass.
    """
    river = river_axis()
    inside = [p for p in potholes()
              if 0.0 <= p['centre'][0] <= PLAYABLE_M and 0.0 <= p['centre'][1] <= PLAYABLE_M]
    inside.sort(key=lambda p: dist_to_polyline(p['centre'], river) - 8.0 * p['depth'])
    out = []
    for p in inside[:n]:
        out.append(ellipse_ring(p['centre'][0], p['centre'][1],
                                p['radius'] * 0.72, p['radius'] * 0.55,
                                (p['centre'][0] * 7.0) % 180.0, n=20))
    return out


# ==================================================================================
# windbreaks
# ==================================================================================
def windbreaks():
    """Every tree row on the map, as separate rectangles.

    Three jobs, and the job is what decides where each one goes:

    * **Farmstead groves.** Around the buildings, where they cut the heating and cooling
      bill. One way per side rather than a ring: they were planted a side at a time, and
      the lane needs a gap to come in through.
    * **Living snow fences.** Upwind of a road, so the drift piles up in the trees
      instead of across the carriageway. The winter wind here comes out of the
      northwest, so a fence for an east-west road stands on its **north** side and one
      for a north-south road on its **west** side - which puts them on the south and east
      edges of the block, not the north and west ones. On the wrong side it is just a
      hedge. Set back about 40 m from the centreline, roughly ten times mature height.
    * **Field hedgerows.** Along field boundaries inside the block. They are laid before
      the parcelling, so the occupancy grid splits the block around them and the parcels
      come out either side of the trees - a hedge between two fields, rather than a strip
      of trees dropped on top of one.
    """
    out = []
    b = WINDBREAK_W_M
    g = WINDBREAK_YARD_GAP_M

    for p in farm_pads():
        cx, cy = p['centre']
        w, h = p['size']
        x0, y0 = cx - w / 2 - g, cy - h / 2 - g
        x1, y1 = cx + w / 2 + g, cy + h / 2 + g
        short = p['name'].split(' (')[0]
        lane = _farm_lane_side(p)
        sides = {
            'n': rect_ring(x0, y0 - b, x1, y0),
            's': rect_ring(x0, y1, x1, y1 + b),
            'w': rect_ring(x0 - b, y0, x0, y1),
            'e': rect_ring(x1, y0, x1 + b, y1),
        }
        for side, ring in sides.items():
            if side == lane:
                continue                      # the lane comes in here
            # the north and west sides are the ones that earn their keep on a fuel bill
            name = f"{short} grove" if side in ('n', 'w') else f"{short} shelterbelt"
            out.append(dict(name=name, ring=ring))

    for p in village_pads():
        cx, cy = p['centre']
        w, h = p['size']
        town = p['name'].split()[-1]
        out.append(dict(name=f"{town} north grove",
                        ring=rect_ring(cx - w / 2, cy - h / 2 - g - b,
                                       cx + w / 2, cy - h / 2 - g)))
        out.append(dict(name=f"{town} west grove",
                        ring=rect_ring(cx - w / 2 - g - b, cy - h / 2 - g,
                                       cx - w / 2 - g, cy + h / 2)))

    for bi, (x0, y0, x1, y1) in enumerate(field_blocks()):
        hsh = (hash((bi, SEED)) >> 3) & 0xFFFFFF
        e = WINDBREAK_EDGE_INSET_M

        # snow fence standing north of the road along the block's south edge
        if hsh % 100 < 55:
            frac = 0.50 + 0.35 * ((hsh >> 8) % 100) / 100.0
            ln = (x1 - x0) * frac
            sx = x0 + ((hsh >> 4) % 60) / 100.0 * (x1 - x0 - ln)
            ring = rect_ring(sx, y1 - e - b, sx + ln, y1 - e)
            if ring_area_ha(ring) > 0.4:
                out.append(dict(name='Living snow fence', ring=ring))

        # ...and west of the road along its east edge
        if (hsh >> 6) % 100 < 45:
            frac = 0.45 + 0.35 * ((hsh >> 10) % 100) / 100.0
            ln = (y1 - y0) * frac
            sy = y0 + ((hsh >> 7) % 60) / 100.0 * (y1 - y0 - ln)
            ring = rect_ring(x1 - e - b, sy, x1 - e, sy + ln)
            if ring_area_ha(ring) > 0.4:
                out.append(dict(name='Living snow fence', ring=ring))

        # a hedgerow across the middle, which becomes a boundary between two fields
        if (hsh >> 12) % 100 < 55 and min(x1 - x0, y1 - y0) > 700.0:
            t = 0.34 + 0.32 * (((hsh >> 16) % 100) / 100.0)
            span = 0.55 + 0.35 * (((hsh >> 18) % 100) / 100.0)
            if (hsh >> 11) & 1:
                yy = y0 + (y1 - y0) * t
                ln = (x1 - x0) * span
                sx = x0 + (x1 - x0 - ln) * 0.5
                ring = rect_ring(sx, yy, sx + ln, yy + b)
            else:
                xx = x0 + (x1 - x0) * t
                ln = (y1 - y0) * span
                sy = y0 + (y1 - y0 - ln) * 0.5
                ring = rect_ring(xx, sy, xx + b, sy + ln)
            out.append(dict(name='Field hedgerow', ring=ring))
    return [w for w in (_clear_of_obstacles(w) for w in out) if w]


def _clear_of_obstacles(wb):
    """Pull a belt back out of anything it has no business crossing.

    A snow fence that runs into the creek is a row of trees standing in the water, and one
    that runs through a farmyard is a row of trees through the machine shed. Rather than
    drop the whole 600 m of it because one end clips a meander or a yard, keep the
    longest stretch that stands on clear ground.
    """
    x0 = min(p[0] for p in wb['ring'])
    x1 = max(p[0] for p in wb['ring'])
    y0 = min(p[1] for p in wb['ring'])
    y1 = max(p[1] for p in wb['ring'])
    along_x = (x1 - x0) >= (y1 - y0)
    lo, hi = (x0, x1) if along_x else (y0, y1)
    cx, cy = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    half = 0.5 * ((y1 - y0) if along_x else (x1 - x0))
    clear_m = WATER_CLEAR_M + half

    step = 10.0
    n = max(2, int((hi - lo) / step) + 1)
    ok = []
    for i in range(n):
        t = lo + (hi - lo) * i / (n - 1)
        p = (t, cy) if along_x else (cx, t)
        ok.append(dist_to_polyline(p, river_axis()) > clear_m
                  and dist_to_polyline(p, creek_axis()) > clear_m
                  and not _in_lake(p, 1.02)
                  and not _in_any_pad(p, half + 6.0))
    if all(ok):
        return wb

    best = run = None
    for i, good in enumerate(ok + [False]):
        if good and run is None:
            run = i
        elif not good and run is not None:
            if best is None or i - run > best[1] - best[0]:
                best = (run, i)
            run = None
    if best is None:
        return None
    a = lo + (hi - lo) * best[0] / (n - 1)
    b = lo + (hi - lo) * (best[1] - 1) / (n - 1)
    if b - a < 120.0:
        return None
    ring = rect_ring(a, y0, b, y1) if along_x else rect_ring(x0, a, x1, b)
    return dict(name=wb['name'], ring=ring)


def _in_any_pad(pt, margin=0.0):
    """Is the point on, or up against, a village or farm platform?"""
    for p in pads():
        cx, cy = p['centre']
        w, h = p['size']
        if (abs(pt[0] - cx) <= w / 2 + margin
                and abs(pt[1] - cy) <= h / 2 + margin):
            return True
    return False


def _farm_lane_side(pad):
    """Which side of the yard the access lane arrives on."""
    spur = _farm_spur(pad)
    if not spur:
        return None
    cx, cy = pad['centre']
    ex, ey = spur[0]
    if abs(ex - cx) > abs(ey - cy):
        return 'e' if ex > cx else 'w'
    return 's' if ey > cy else 'n'


# ==================================================================================
# fields
# ==================================================================================
class _Occupancy:
    """Coarse raster of everything a field may not overlap: water, wet ground, timber,
    yards and road right of way. 32 m cells - the step it leaves along a river bank is
    the grass headland every Iowa field has there anyway."""

    def __init__(self):
        self.n = int(PLAYABLE_M / GRID_CELL_M)
        self.g = bytearray(self.n * self.n)
        for rings in (river_water_ring(), [lake_ring()], lake_margin(),
                      river_woods(), wet_reserves()):
            for ring in rings:
                self._fill_ring_strict(ring)
        # The bottom land is stamped by distance to the centreline, not filled as an
        # offset polygon. Offsetting a polyline by more than its radius of curvature
        # folds the ring back through itself, and an even-odd fill then punches holes in
        # exactly the tightest meanders - which is where fields were turning up 120 m
        # from the water.
        self._fill_corridor(river_axis(), RIVER['riparian_half_w'])
        self._fill_corridor(creek_axis(), CREEK['riparian_half_w'])
        for wb in windbreaks():
            self._fill_ring_strict(wb['ring'])
        for p in pads():
            cx, cy = p['centre']
            w, h = p['size']
            m = 25.0
            self._fill_rect(cx - w / 2 - m, cy - h / 2 - m,
                            cx + w / 2 + m, cy + h / 2 + m)
        for c in corridors():
            if c['kind'] in ('track', 'street'):
                self._fill_polyline(c['axis'], c['half_width_m'] + 12.0)

    def _idx(self, x, y):
        return (int(y // GRID_CELL_M), int(x // GRID_CELL_M))

    def _set(self, r, c):
        if 0 <= r < self.n and 0 <= c < self.n:
            self.g[r * self.n + c] = 1

    def get(self, r, c):
        if 0 <= r < self.n and 0 <= c < self.n:
            return self.g[r * self.n + c]
        return 1

    def _fill_rect(self, x0, y0, x1, y1):
        r0, c0 = self._idx(max(0.0, x0), max(0.0, y0))
        r1, c1 = self._idx(min(PLAYABLE_M - 1, x1), min(PLAYABLE_M - 1, y1))
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                self._set(r, c)

    def _fill_ring_strict(self, ring):
        """Mark every cell the polygon touches, not only those it covers.

        A scanline fill decides a cell by its centre, so anything narrower than the 32 m
        cell can fall between two centres and mark nothing at all - which is how a 24 m
        shelterbelt ends up invisible to the parcelling and a field gets laid straight
        over it. Walking the boundary as well closes that.
        """
        self._fill_ring(ring)
        pts = ring if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring) + [ring[0]]
        for p in densify(pts, GRID_CELL_M * 0.4):
            r, c = self._idx(p[0], p[1])
            self._set(r, c)

    def _fill_corridor(self, pts, radius):
        """Union of discs along a polyline: the true buffer, at any curvature.

        A cell is judged by its centre, so a parcel edge could otherwise sit half a cell
        diagonal inside the reserve. The radius is grown by that much, which turns "no
        field within R of the water" from nearly true into true.
        """
        radius = radius + GRID_CELL_M * math.sqrt(2.0) / 2.0
        rr = int(math.ceil(radius / GRID_CELL_M))
        seen = set()
        for x, y in pts:
            r0, c0 = self._idx(x, y)
            if (r0, c0) in seen:
                continue
            seen.add((r0, c0))
            for r in range(r0 - rr, r0 + rr + 1):
                for c in range(c0 - rr, c0 + rr + 1):
                    if 0 <= r < self.n and 0 <= c < self.n and not self.g[r * self.n + c]:
                        cx = (c + 0.5) * GRID_CELL_M
                        cy = (r + 0.5) * GRID_CELL_M
                        if math.hypot(cx - x, cy - y) <= radius:
                            self.g[r * self.n + c] = 1

    def _fill_polyline(self, pts, half):
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            self._fill_rect(x0 - half, y0 - half, x1 + half, y1 + half)

    def _fill_ring(self, ring):
        """Scanline fill, one row of cells at a time."""
        pts = ring[:-1] if math.dist(ring[0], ring[-1]) < 1e-9 else list(ring)
        ys = [p[1] for p in pts]
        r0 = max(0, int(min(ys) // GRID_CELL_M))
        r1 = min(self.n - 1, int(max(ys) // GRID_CELL_M))
        m = len(pts)
        for r in range(r0, r1 + 1):
            y = (r + 0.5) * GRID_CELL_M
            xs = []
            for i in range(m):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % m]
                if (y0 > y) != (y1 > y):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                c0 = max(0, int(xs[k] // GRID_CELL_M))
                c1 = min(self.n - 1, int(xs[k + 1] // GRID_CELL_M))
                for c in range(c0, c1 + 1):
                    self._set(r, c)

    def blocked_fraction(self, x0, y0, x1, y1):
        r0, c0 = self._idx(max(0.0, x0), max(0.0, y0))
        r1, c1 = self._idx(min(PLAYABLE_M - 1, x1), min(PLAYABLE_M - 1, y1))
        tot = bad = 0
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                tot += 1
                bad += self.get(r, c)
        return (bad / tot) if tot else 1.0


def _grain_ha(x, y, rough, scale=1.0):
    """Target field size at a point.

    Small near the towns and the yards, small on the bottoms, small where the ground is
    broken - and large out on the open, even sections, which is where Midwestern
    extensive farming actually happens. The hard 100 ha cap is not enforced here but in
    the split rule, which is the only place it can be guaranteed.
    """
    g = 34.0
    dv = min(math.hypot(x - p['centre'][0], y - p['centre'][1]) for p in village_pads())
    df = min(math.hypot(x - p['centre'][0], y - p['centre'][1]) for p in farm_pads())
    dw = min(dist_to_polyline((x, y), river_axis()),
             math.hypot(x - lake_centre()[0], y - lake_centre()[1]) - LAKE['semi_a'])
    if dv < 700.0:
        g = min(g, 5.0 + 0.030 * max(0.0, dv - 150.0))
    if df < 650.0:
        g = min(g, 8.0 + 0.026 * max(0.0, df - 150.0))
    if dw < 500.0:
        g = min(g, 7.0 + 0.028 * max(0.0, dw - 150.0))
    if dv > 1200.0 and df > 1200.0 and dw > 900.0:
        openness = min(1.0, (min(dv, df, dw) - 900.0) / 900.0)
        g = 38.0 + 62.0 * openness
    g *= 1.0 - 0.55 * rough(x, y)
    # jitter keyed to position, not to iteration order, so the result does not depend on
    # how the blocks happen to be walked
    h = (hash((int(x // 64), int(y // 64), SEED)) % 1000) / 1000.0
    g *= 0.82 + 0.40 * h
    # `scale` is solved for in fields(): it is what holds the parcel count under
    # FIELD_MAX_COUNT without flattening the size mix, since it lifts every class
    # together rather than deleting the small fields.
    return max(4.0, min(96.0, g * scale))


def _split_block(rect, rough, rng, prefer_ns, scale=1.0):
    """Recursive orthogonal guillotine.

    The stop rule is the whole ballgame: `ha <= min(target*1.25, FIELD_MAX_HA)`. The
    obvious `ha <= target*1.35` lets a block settle at 128 ha, over the cap the brief
    sets, because the tolerance multiplies the target instead of bounding it.
    """
    out, stack = [], [rect]
    guard = 0
    while stack and guard < 4000:
        guard += 1
        x0, y0, x1, y1 = stack.pop()
        w, h = x1 - x0, y1 - y0
        ha = w * h / 1e4
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        tgt = _grain_ha(cx, cy, rough, scale)
        if ha <= min(tgt * 1.25, FIELD_MAX_HA):
            out.append((x0, y0, x1, y1))
            continue
        long_is_x = w >= h
        if rng.random() < 0.20:
            long_is_x = not long_is_x
        if ha > FIELD_MAX_HA * 1.9:
            long_is_x = w >= h          # never fight the cap with a bad axis
        f = rng.uniform(0.38, 0.62)
        if long_is_x:
            c = x0 + w * f
            parts = ((x0, y0, c, y1), (c, y0, x1, y1))
        else:
            c = y0 + h * f
            parts = ((x0, y0, x1, c), (x0, c, x1, y1))
        for p in parts:
            pw, ph = p[2] - p[0], p[3] - p[1]
            if min(pw, ph) < FIELD_MIN_WIDTH_M or pw * ph / 1e4 < FIELD_MIN_HA:
                if pw * ph / 1e4 >= FIELD_MIN_HA * 0.6:
                    out.append(p)
            else:
                stack.append(p)
    return out


def _free_rects(occ, block, max_rects=40):
    """Carve the free part of a block into maximal rectangles.

    The obvious way round - guillotine the whole block, then throw away or shave back
    whatever landed on a yard, a wood or the river - loses every scrap it cuts, and those
    scraps add up to the dead ground that shows between the parcels. Finding the largest
    free rectangle first and repeating means the leftovers get farmed too, and only the
    32 m the raster cannot resolve is left as headland.
    """
    x0, y0, x1, y1 = block
    g = GRID_CELL_M
    c0, c1 = int(math.ceil(x0 / g)), int(math.floor(x1 / g)) - 1
    r0, r1 = int(math.ceil(y0 / g)), int(math.floor(y1 / g)) - 1
    if c1 < c0 or r1 < r0:
        return []
    free = [[0 if occ.get(r, c) else 1 for c in range(c0, c1 + 1)]
            for r in range(r0, r1 + 1)]

    out = []
    for _ in range(max_rects):
        best = _max_rect(free)
        if best is None:
            break
        ra, ca, rb, cb = best
        # Extend to the block boundary where the rectangle already reaches the edge cell,
        # so the strip between the last whole cell and the road does not go to waste -
        # but only where the extension is itself clear. Extending blindly puts part of
        # the parcel on an obstacle, and the trim that follows then eats the whole field
        # rather than the strip.
        mx0, mx1 = (c0 + ca) * g, (c0 + cb + 1) * g
        my0, my1 = (r0 + ra) * g, (r0 + rb + 1) * g
        if ca == 0 and occ.blocked_fraction(x0, my0, mx0, my1) <= 0.0:
            mx0 = x0
        if cb == c1 - c0 and occ.blocked_fraction(mx1, my0, x1, my1) <= 0.0:
            mx1 = x1
        if ra == 0 and occ.blocked_fraction(mx0, y0, mx1, my0) <= 0.0:
            my0 = y0
        if rb == r1 - r0 and occ.blocked_fraction(mx0, my1, mx1, y1) <= 0.0:
            my1 = y1
        if (mx1 - mx0) < FIELD_MIN_WIDTH_M or (my1 - my0) < FIELD_MIN_WIDTH_M \
                or (mx1 - mx0) * (my1 - my0) / 1e4 < FIELD_MIN_HA:
            break
        out.append((mx0, my0, mx1, my1))
        for r in range(ra, rb + 1):
            for c in range(ca, cb + 1):
                free[r][c] = 0
    return out


def _max_rect(free):
    """Largest all-free axis-aligned rectangle, by the usual histogram sweep."""
    if not free or not free[0]:
        return None
    n = len(free[0])
    heights = [0] * n
    best = (0, None)
    for r, row in enumerate(free):
        for c in range(n):
            heights[c] = heights[c] + 1 if row[c] else 0
        stack = []
        for c in range(n + 1):
            h = heights[c] if c < n else 0
            start = c
            while stack and stack[-1][1] >= h:
                sc, sh = stack.pop()
                area = sh * (c - sc)
                if area > best[0]:
                    best = (area, (r - sh + 1, sc, r, c - 1))
                start = sc
            stack.append((start, h))
    return best[1]


def _enforce_aspect(rects):
    """Halve anything longer than the aspect guard allows instead of discarding it.

    A leaf 6:1 across is not a field, but throwing it away throws away the land with it;
    cut in two it is two ordinary parcels.
    """
    out = []
    stack = list(rects)
    while stack:
        x0, y0, x1, y1 = stack.pop()
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        if max(w / h, h / w) <= FIELD_ASPECT_MAX or min(w, h) < 2 * FIELD_MIN_WIDTH_M:
            out.append((x0, y0, x1, y1))
            continue
        if w >= h:
            c = 0.5 * (x0 + x1)
            stack += [(x0, y0, c, y1), (c, y0, x1, y1)]
        else:
            c = 0.5 * (y0 + y1)
            stack += [(x0, y0, x1, c), (x0, c, x1, y1)]
    return out


def _trim_to_free(rect, occ):
    """Shrink a rectangle off water, wet ground and yards, one 32 m strip at a time."""
    x0, y0, x1, y1 = rect
    for _ in range(24):
        if occ.blocked_fraction(x0, y0, x1, y1) <= 0.0:
            return (x0, y0, x1, y1)
        if (x1 - x0) < FIELD_MIN_WIDTH_M or (y1 - y0) < FIELD_MIN_WIDTH_M:
            return None
        sides = {
            'w': occ.blocked_fraction(x0, y0, min(x1, x0 + GRID_CELL_M), y1),
            'e': occ.blocked_fraction(max(x0, x1 - GRID_CELL_M), y0, x1, y1),
            'n': occ.blocked_fraction(x0, y0, x1, min(y1, y0 + GRID_CELL_M)),
            's': occ.blocked_fraction(x0, max(y0, y1 - GRID_CELL_M), x1, y1),
        }
        side = max(sides, key=lambda k: sides[k])
        if sides[side] <= 0.0:
            return None                 # blocked in the middle: not recoverable
        if side == 'w':
            x0 += GRID_CELL_M
        elif side == 'e':
            x1 -= GRID_CELL_M
        elif side == 'n':
            y0 += GRID_CELL_M
        else:
            y1 -= GRID_CELL_M
    return None


def _jog_ring(rect, rng):
    """Push the middle third of one side in or out, so the parcels stop looking stamped
    out. Still rectilinear - this is mechanised farmland, not a bocage."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    d = rng.choice((-1.0, 1.0)) * rng.uniform(32.0, 96.0)
    side = rng.choice(('n', 's', 'w', 'e'))
    if side in ('n', 's') and w > 340.0 and abs(d) < h * 0.35:
        a, b = x0 + w / 3.0, x1 - w / 3.0
        if side == 'n':
            yy = y0 + d
            return [(x0, y0), (a, y0), (a, yy), (b, yy), (b, y0), (x1, y0),
                    (x1, y1), (x0, y1), (x0, y0)]
        yy = y1 + d
        return [(x0, y0), (x1, y0), (x1, y1), (b, y1), (b, yy), (a, yy),
                (a, y1), (x0, y1), (x0, y0)]
    if side in ('w', 'e') and h > 340.0 and abs(d) < w * 0.35:
        a, b = y0 + h / 3.0, y1 - h / 3.0
        if side == 'w':
            xx = x0 + d
            return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, b), (xx, b),
                    (xx, a), (x0, a), (x0, y0)]
        xx = x1 + d
        return [(x0, y0), (x1, y0), (x1, a), (xx, a), (xx, b), (x1, b),
                (x1, y1), (x0, y1), (x0, y0)]
    return rect_ring(x0, y0, x1, y1)


def field_blocks():
    """The section blocks, inset off the road right of way."""
    xs = [0.0] + [x for x in GRID] + [PLAYABLE_M]
    ys = [0.0] + [y for y in GRID] + [PLAYABLE_M]
    out = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            x0 = xs[i] + (ROW_CLEAR_M if i else 0.0)
            x1 = xs[i + 1] - (ROW_CLEAR_M if i + 1 < len(xs) - 1 else 0.0)
            y0 = ys[j] + (ROW_CLEAR_M if j else 0.0)
            y1 = ys[j + 1] - (ROW_CLEAR_M if j + 1 < len(ys) - 1 else 0.0)
            if x1 - x0 > FIELD_MIN_WIDTH_M and y1 - y0 > FIELD_MIN_WIDTH_M:
                out.append((x0, y0, x1, y1))
    return out


def fields(roughness=None):
    """Every cropped parcel on the map, as closed rings with an area in hectares.

    `roughness(x, y)` returns 0 for dead flat and 1 for the roughest ground the map has;
    the DEM writes one out as `terrain_stats.json`. Without it the parcelling still runs,
    it just loses the "smaller fields on broken ground" rule.

    The map carries at most FIELD_MAX_COUNT parcels. That is enforced by coarsening the
    whole grain field until the count fits, not by deleting fields afterwards: dropping
    the overflow would quietly eat the small parcels near the towns, which are exactly
    the ones the size mix needs.
    """
    rough = roughness or (lambda x, y: 0.0)
    occ = _Occupancy()
    scale = 1.0
    for _ in range(16):
        out = _fields_once(occ, rough, scale)
        if len(out) <= FIELD_MAX_COUNT:
            return out
        # overshoot in proportion to how far over we are, so this converges in a couple
        # of passes instead of creeping
        scale *= max(1.06, (len(out) / FIELD_MAX_COUNT) ** 0.55)
    return sorted(out, key=lambda f: -f['ha'])[:FIELD_MAX_COUNT]


def _fields_once(occ, rough, scale):
    rng = random.Random(SEED + 11)
    out = []
    for bi, block in enumerate(field_blocks()):
        # alternate the first cut per block, which alternates the run direction between
        # neighbouring sections - what an aerial photo of Iowa actually looks like
        prefer_ns = bool(hash((bi, SEED)) & 1)
        rects = []
        for free in _free_rects(occ, block):
            rects.extend(_split_block(free, rough, rng, prefer_ns, scale))
        # No occupancy test here: every rectangle came out of _free_rects, which only
        # ever returns clear ground. Re-testing costs a strip of every parcel whose edge
        # falls mid-cell, because the cell it shares with the obstacle next door reads as
        # blocked - about 400 ha of perfectly good farmland, trimmed away for nothing.
        for rect in _enforce_aspect(rects):
            x0, y0, x1, y1 = rect
            m = FIELD_MARGIN_M
            x0, y0, x1, y1 = x0 + m, y0 + m, x1 - m, y1 - m
            w, h = x1 - x0, y1 - y0
            if w < FIELD_MIN_WIDTH_M or h < FIELD_MIN_WIDTH_M:
                continue
            if w * h / 1e4 < FIELD_MIN_HA:
                continue
            ring = rect_ring(x0, y0, x1, y1)
            if rng.random() < 0.12:
                cand = _jog_ring((x0, y0, x1, y1), rng)
                xs = [p[0] for p in cand]
                ys = [p[1] for p in cand]
                # the jog is the one place a parcel reaches outside its free rectangle,
                # so this is the one place the occupancy still has to be consulted
                if (min(xs) >= 0.0 and max(xs) <= PLAYABLE_M and min(ys) >= 0.0
                        and max(ys) <= PLAYABLE_M
                        and occ.blocked_fraction(min(xs), min(ys),
                                                 max(xs), max(ys)) <= 0.0):
                    ring = cand
            ha = ring_area_ha(ring)
            if ha > FIELD_MAX_HA or ha < FIELD_MIN_HA:
                continue
            out.append(dict(ring=ring, ha=ha))
    return out


def load_roughness(path=None):
    """Read `terrain_stats.json` and return a roughness lookup, or None if it is not
    there yet. The DEM has to run first; the OSM degrades gracefully if it has not."""
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
    Returns a list of complaints; empty means the layout is sound."""
    bad = []
    river = river_axis()

    inside = [p for p in river if 0 <= p[0] <= PLAYABLE_M and 0 <= p[1] <= PLAYABLE_M]
    if not inside:
        bad.append("river: never enters the playable area")
    else:
        north = min(p[1] for p in inside)
        if north - MAIN_ROAD_Y < 600.0:
            bad.append(f"river: comes within {north - MAIN_ROAD_Y:.0f} m of the primary "
                       "(wanted 600+), it would cut the farmland in two")
        if polyline_length(inside) < 6500.0:
            bad.append(f"river: only {polyline_length(inside):.0f} m inside the playable "
                       "area (wanted 6500+)")

    for c in corridors():
        hits = _axis_river_hits(c['axis'])
        if hits and not c['bridge_spans']:
            bad.append(f"{c['id']}: crosses the river with no bridge span - grading it "
                       "would dam the channel")
        if c['axis'][0][0] > EDGE_MIN + 1.0 and c['axis'][0][0] < -OFFSET_M + 1.0:
            bad.append(f"{c['id']}: starts inside the canvas edge")

    for p in pads():
        for water, name in water_axes():
            d = dist_to_polyline(p['centre'], water)
            if d < PAD_RIVER_CLEAR_M:
                bad.append(f"{p['id']}: {d:.0f} m from the {name} centreline "
                           f"(wanted {PAD_RIVER_CLEAR_M:.0f}+)")
        x0 = p['centre'][0] - p['size'][0] / 2
        x1 = p['centre'][0] + p['size'][0] / 2
        y0 = p['centre'][1] - p['size'][1] / 2
        y1 = p['centre'][1] + p['size'][1] / 2
        if x0 < 60 or y0 < 60 or x1 > PLAYABLE_M - 60 or y1 > PLAYABLE_M - 60:
            bad.append(f"{p['id']}: too close to the map edge")

    for p in farm_pads():
        cx, cy = p['centre']
        w, h = p['size']
        m = PAD_ROAD_CLEAR_M
        for c in corridors():
            if c['kind'] in ('track', 'street'):
                continue                       # the yard's own lane arrives here
            for (ax, ay) in c['axis']:
                if (abs(ax - cx) <= w / 2 + m and abs(ay - cy) <= h / 2 + m):
                    bad.append(f"{p['id']} ({p['name']}): {c['name']} runs through the "
                               f"yard - a public road across the middle of a farmstead")
                    break
            else:
                continue
            break

    vmax = max(ring_area_ha(p['ring']) for p in village_pads())
    fmin = min(ring_area_ha(p['ring']) for p in farm_pads())
    if fmin <= vmax:
        bad.append(f"farm yards must be larger than village pads "
                   f"(smallest farm {fmin:.1f} ha, largest village {vmax:.1f} ha)")

    for a in pads():
        for b in pads():
            if a['id'] >= b['id']:
                continue
            if (abs(a['centre'][0] - b['centre'][0]) < (a['size'][0] + b['size'][0]) / 2 + 120
                    and abs(a['centre'][1] - b['centre'][1])
                    < (a['size'][1] + b['size'][1]) / 2 + 120):
                bad.append(f"{a['id']} and {b['id']} overlap or crowd each other")

    lake_c = lake_centre()
    if dist_to_polyline(lake_c, river) > 1400.0:
        bad.append("lake: too far from the river to connect naturally")
    return bad


def summary():
    """One-line description of the layout, for the generators to print."""
    return (f"{PLAYABLE_M:.0f} m playable, {len(corridors())} corridors, "
            f"{len(village_pads())} villages, {len(farm_pads())} farms, "
            f"{len(potholes())} potholes")


if __name__ == '__main__':
    print("=== map_layout self-check ===")
    print("  ", summary())
    r = river_axis()
    ins = [p for p in r if 0 <= p[0] <= PLAYABLE_M and 0 <= p[1] <= PLAYABLE_M]
    print(f"   river {polyline_length(r):.0f} m total, {polyline_length(ins):.0f} m "
          f"inside, closest approach to the primary "
          f"{min(p[1] for p in ins) - MAIN_ROAD_Y:.0f} m")
    print(f"   lake {ring_area_ha(lake_ring()):.1f} ha")
    for c in corridors():
        if c['bridge_spans']:
            print(f"   bridge on {c['id']}: {len(c['bridge_spans'])} span(s)")
    problems = validate()
    if problems:
        print("\n   PROBLEMS")
        for p in problems:
            print("    -", p)
    else:
        print("\n   layout is sound")
