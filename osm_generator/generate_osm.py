#!/usr/bin/env python3
"""Write `map.osm`: the vector layout of the playable area.

Northwest Iowa farm country, laid out from `map_layout.py` at the root of the tree - the
same module the DEM generator sculpts its terrain from, so the river drawn here runs
along the valley that was carved there, and the farmyards sit on the platforms that were
levelled for them.

What goes in the file:

    highway=primary                               420th Street, the straight east-west
                                                  trunk through the middle of the map
    highway=secondary                             the Public Land Survey grid, one mile
                                                  apart, meeting at right angles
    highway=tertiary                              farm lanes and village streets
    railway=rail                                  the branch line, straight north-south,
                                                  crossing the primary at the centre
    landuse=farmland                              the fields
    landuse=farmyard                              three villages and seven farmsteads
    natural=wood + landuse=farmyard + leaf_type   river timber and farm shelterbelts
    natural=water                                 the river and the lake
    bridge=yes                                    the three river crossings

The vocabulary is deliberately closed: it is exactly what `visualize_osm.py` and
`visualizer/create_3d_viewer.py` already understand. A way tagged with anything else is
dropped by both without a word, so the floodplain pasture is left as unclaimed ground -
no field is placed there - rather than tagged with something neither renderer draws.
"""
import math
import os
import sys
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from collections import Counter

import map_extent as mx

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402

OUT_NAME = "map.osm"
STAMP = {'version': '1', 'timestamp': '2026-09-02T12:00:00Z',
         'changeset': '1', 'uid': '1', 'user': 'generator'}

HIGHWAY_CLASS = {'primary': 'primary', 'section': 'secondary',
                 'track': 'tertiary', 'street': 'tertiary'}


# ==================================================================================
# node and way pools
# ==================================================================================
class Osm:
    def __init__(self):
        self.nodes = {}
        self.node_coords = {}
        self.ways = []
        self._nid = 1
        self._wid = 1

    def node(self, x, y):
        """One node per coordinate. Two ways that name the same point must share it, or
        the road network is a pile of disconnected sticks."""
        key = (round(float(x), 3), round(float(y), 3))
        if key not in self.nodes:
            self.nodes[key] = self._nid
            self.node_coords[self._nid] = ml.local_to_global(*key)
            self._nid += 1
        return self.nodes[key]

    def way(self, coords, tags):
        pts = [(float(x), float(y)) for x, y in coords]
        w = {'id': self._wid, 'coords': pts, 'tags': tags,
             'node_refs': [self.node(x, y) for x, y in pts]}
        self.ways.append(w)
        self._wid += 1
        return w

    def area(self, ring, tags):
        """Closed way. The 3D viewer decides polygon versus line by comparing the first
        and last coordinate exactly, so the ring must close on the same node id."""
        pts = list(ring)
        if math.dist(pts[0], pts[-1]) > 1e-9:
            pts.append(pts[0])
        w = self.way(pts[:-1], tags)
        w['coords'].append(w['coords'][0])
        w['node_refs'].append(w['node_refs'][0])
        return w


def clip_to_playable(pts, margin=0.0):
    """Trim a polyline to the playable square, interpolating at the edge."""
    lo, hi = -margin, ml.PLAYABLE_M + margin

    def inside(p):
        return lo <= p[0] <= hi and lo <= p[1] <= hi

    out, cur = [], []
    for i, p in enumerate(pts):
        if inside(p):
            if not cur and i > 0:
                cur.append(_edge_point(pts[i - 1], p, lo, hi))
            cur.append(p)
        else:
            if cur:
                cur.append(_edge_point(cur[-1], p, lo, hi))
                out.append(cur)
                cur = []
    if cur:
        out.append(cur)
    return [r for r in out if len(r) >= 2]


def _edge_point(inside_pt, outside_pt, lo, hi):
    """Where the segment leaves the box."""
    best_t = 1.0
    for k in (0, 1):
        for bound in (lo, hi):
            a, b = inside_pt[k], outside_pt[k]
            if abs(b - a) < 1e-9:
                continue
            t = (bound - a) / (b - a)
            if 0.0 <= t < best_t:
                p = (inside_pt[0] + t * (outside_pt[0] - inside_pt[0]),
                     inside_pt[1] + t * (outside_pt[1] - inside_pt[1]))
                if lo - 1e-6 <= p[0] <= hi + 1e-6 and lo - 1e-6 <= p[1] <= hi + 1e-6:
                    best_t = t
    return (inside_pt[0] + best_t * (outside_pt[0] - inside_pt[0]),
            inside_pt[1] + best_t * (outside_pt[1] - inside_pt[1]))


def clamp_ring(ring):
    """Pull a ring back inside the playable square.

    The river and timber polygons are the centreline buffered outwards, so where the
    river leaves the map the buffer overhangs the edge by up to its own width. Clamping
    is what a map boundary does anyway; the alternative is nodes outside the declared
    bounds, which the 3D viewer would stretch the whole map to fit.
    """
    return [(min(max(x, 0.0), ml.PLAYABLE_M), min(max(y, 0.0), ml.PLAYABLE_M))
            for x, y in ring]


# ==================================================================================
# features
# ==================================================================================
def emit_water(osm):
    """The river, the creek and the lake the two of them feed.

    The channel polygons stop at the lake shore. Two overlapping water areas would fight
    over the same ground in the editor, and the lake is the one that should win there.
    """
    n = 0
    for ring in ml.river_water_ring():
        osm.area(clamp_ring(ring), {'natural': 'water', 'water': 'river',
                                    'name': 'Ocheyedan River'})
        n += 1
    osm.area(ml.lake_ring(), {'natural': 'water', 'water': 'lake',
                              'name': 'Silver Lake'})
    return n + 1


def emit_woods(osm):
    """River timber and the tree rows: yard groves, block edges and belts between fields.

    Each side of a farmstead grove is its own way rather than one ring around the yard -
    which is how they were actually planted, and leaves the lane a gap to come in
    through.
    """
    n = 0
    tags = {'natural': 'wood', 'landuse': 'farmyard', 'leaf_type': 'broadleaved'}
    for ring in ml.river_woods():
        osm.area(clamp_ring(ring), dict(tags, name='River timber'))
        n += 1
    for wb in ml.windbreaks():
        ring = clamp_ring(wb['ring'])
        if ml.ring_area_ha(ring) < 0.15:
            continue
        osm.area(ring, dict(tags, name=wb['name']))
        n += 1
    return n


def emit_pads(osm):
    for p in ml.village_pads():
        osm.area(p['ring'], {'landuse': 'farmyard', 'name': p['name']})
    for p in ml.farm_pads():
        osm.area(p['ring'], {'landuse': 'farmyard', 'name': p['name']})
    # The grain elevator by the tracks is what put the town there in the first place.
    v = ml.village_pads()[1]
    cx, cy = v['centre']
    w, h = v['size']
    x0 = cx + w / 2 + 40.0
    osm.area(ml.rect_ring(x0, cy - 70.0, x0 + 190.0, cy + 70.0),
             {'landuse': 'farmyard', 'building': 'industrial',
              'name': 'Royal Farmers Co-op Elevator'})
    return len(ml.pads()) + 1


def emit_fields(osm, rough):
    fs = ml.fields(rough)
    for i, f in enumerate(sorted(fs, key=lambda f: (-f['ha'], f['ring'][0])), 1):
        osm.area(f['ring'], {'landuse': 'farmland', 'name': f"Field {i:03d}"})
    return fs


def _corridor_vertices(c, others):
    """Every point another alignment meets this one, so the crossing is a shared node."""
    ax = c['axis']
    vertical = abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1])
    fixed = ax[0][0] if vertical else ax[0][1]
    lo = min(p[1] for p in ax) if vertical else min(p[0] for p in ax)
    hi = max(p[1] for p in ax) if vertical else max(p[0] for p in ax)
    out = []
    for o in others:
        if o['id'] == c['id']:
            continue
        oax = o['axis']
        ov = abs(oax[0][0] - oax[-1][0]) < abs(oax[0][1] - oax[-1][1])
        if ov == vertical:
            continue
        ofixed = oax[0][0] if ov else oax[0][1]
        olo = min(p[1] for p in oax) if ov else min(p[0] for p in oax)
        ohi = max(p[1] for p in oax) if ov else max(p[0] for p in oax)
        if vertical:
            if lo <= ofixed <= hi and olo <= fixed <= ohi:
                out.append((fixed, ofixed))
        else:
            if lo <= ofixed <= hi and olo <= fixed <= ohi:
                out.append((ofixed, fixed))
    return out


def emit_roads(osm):
    corr = ml.corridors()
    counts = Counter()
    bridges = 0
    for c in corr:
        vertical = abs(c['axis'][0][0] - c['axis'][-1][0]) < \
            abs(c['axis'][0][1] - c['axis'][-1][1])
        key = (lambda p: p[1]) if vertical else (lambda p: p[0])
        ends = [c['axis'][0], c['axis'][-1]]
        pts = sorted(set(ends + _corridor_vertices(c, corr)), key=key)

        # The abutments become vertices, so the span can be split off into a way of its
        # own and carry bridge=yes.
        dense = ml.densify(c['axis'], 5.0)
        spans = list(c['bridge_spans'])
        abutments = [_point_at(dense, s) for span in spans for s in span]
        pts = sorted(set(pts + abutments), key=key)

        base = {}
        if c['kind'] == 'rail':
            base = {'railway': 'rail', 'name': c['name']}
        else:
            base = {'highway': HIGHWAY_CLASS[c['kind']], 'name': c['name']}
            if c.get('ref'):
                base['ref'] = c['ref']

        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            seg = [a, b]
            runs = clip_to_playable(seg)
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            s_mid = _arc_at(dense, mid)
            on_bridge = any(s0 - 1.0 <= s_mid <= s1 + 1.0 for s0, s1 in spans)
            for run in runs:
                tags = dict(base)
                if on_bridge:
                    tags['bridge'] = 'yes'
                    tags['layer'] = '1'
                    bridges += 1
                osm.way(run, tags)
                counts[c['kind']] += 1
    return counts, bridges


def _point_at(dense, s):
    """The point at arc length s along a densified polyline."""
    t = 0.0
    for i in range(len(dense) - 1):
        seg = math.dist(dense[i], dense[i + 1])
        if t + seg >= s:
            f = (s - t) / seg if seg else 0.0
            return (dense[i][0] + f * (dense[i + 1][0] - dense[i][0]),
                    dense[i][1] + f * (dense[i + 1][1] - dense[i][1]))
        t += seg
    return dense[-1]


def _arc_at(dense, pt):
    best, acc, out = 1e18, 0.0, 0.0
    for i in range(len(dense) - 1):
        d = ml.seg_point_dist(pt, dense[i], dense[i + 1])
        if d < best:
            best, out = d, acc + math.dist(dense[i], pt)
        acc += math.dist(dense[i], dense[i + 1])
    return out


def connect_road_crossings(osm):
    """Split ways where their geometry crosses, so junctions share a node.

    The filter has to take the railway too. Left as `'highway' in tags`, the way it was
    in the old generator, none of the level crossings gets a shared node and the network
    falls into two disconnected components - which nothing downstream would complain
    about, it would just be wrong.
    """
    roads = [w for w in osm.ways if 'highway' in w['tags'] or 'railway' in w['tags']]
    added = 0
    for i, a in enumerate(roads):
        for b in roads[i + 1:]:
            for ia in range(len(a['coords']) - 1):
                for ib in range(len(b['coords']) - 1):
                    p = _seg_cross(a['coords'][ia], a['coords'][ia + 1],
                                   b['coords'][ib], b['coords'][ib + 1])
                    if p is None:
                        continue
                    for w in (a, b):
                        nid = osm.node(*p)
                        if nid not in w['node_refs']:
                            k = _insert_index(w['coords'], p)
                            w['coords'].insert(k, p)
                            w['node_refs'].insert(k, nid)
                            added += 1
    return added


def _seg_cross(a, b, c, d):
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    if 1e-9 < t < 1 - 1e-9 and 1e-9 < u < 1 - 1e-9:
        return (a[0] + t * r[0], a[1] + t * r[1])
    return None


def _insert_index(coords, p):
    for i in range(len(coords) - 1):
        if ml.seg_point_dist(p, coords[i], coords[i + 1]) < 1e-6:
            return i + 1
    return len(coords)


# ==================================================================================
# writing
# ==================================================================================
def write_osm(osm, path):
    minlat, minlon, maxlat, maxlon = ml.bounds()
    root = ET.Element('osm', version='0.6', generator='FS25 map pipeline')
    root.append(ET.Comment(
        f"\n       Playable area: {ml.PLAYABLE_M:.0f} x {ml.PLAYABLE_M:.0f} m, "
        f"centre {ml.LAT_CENTER:.4f}, {ml.LON_CENTER:.4f}\n"
        "       (Clay County, Iowa - Des Moines Lobe farmland around Royal).\n"
        "       Local coordinates are playable metres, x east, y south from the north\n"
        f"       edge, so the centre of the map is ({ml.HALF_M:.0f}, {ml.HALF_M:.0f}).\n"
        f"       Projection: equirectangular about the centre, {ml.M_PER_DEG:.1f} m per\n"
        f"       degree of latitude and {ml.M_PER_DEG:.1f} * cos(LAT_CENTER) m per\n"
        "       degree of longitude.\n"
        "       Geometry comes from map_layout.py, shared with the DEM generator.\n  "))
    ET.SubElement(root, 'bounds', {
        'minlat': f"{minlat:.10f}", 'minlon': f"{minlon:.10f}",
        'maxlat': f"{maxlat:.10f}", 'maxlon': f"{maxlon:.10f}"})

    for nid in sorted(osm.node_coords):
        lat, lon = osm.node_coords[nid]
        ET.SubElement(root, 'node', {'id': str(nid), 'lat': f"{lat:.10f}",
                                     'lon': f"{lon:.10f}", **STAMP})
    for w in osm.ways:
        elem = ET.SubElement(root, 'way', {'id': str(w['id']), **STAMP})
        for ref in w['node_refs']:
            ET.SubElement(elem, 'nd', ref=str(ref))
        for k, v in w['tags'].items():
            ET.SubElement(elem, 'tag', k=k, v=str(v))

    pretty = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(
        indent='  ', encoding='utf-8')
    with open(path, "wb") as fh:
        fh.write(pretty)


def main():
    print("=== Generating OSM data for the Iowa map ===")
    print(f"   centre {ml.LAT_CENTER:.4f}, {ml.LON_CENTER:.4f} - Clay County, Iowa")
    problems = ml.validate()
    if problems:
        print("!! layout problems:")
        for p in problems:
            print("   -", p)
        return 1

    rough = ml.load_roughness()
    if rough is None:
        print("   note: dem_generator/terrain_stats.json is missing, so the parcelling "
              "cannot size fields to the ground. Run the DEM generator first.")

    osm = Osm()
    print("1. Water...")
    nw = emit_water(osm)
    print("2. Timber...")
    nwd = emit_woods(osm)
    print("3. Villages and farmsteads...")
    npad = emit_pads(osm)
    print("4. Fields...")
    fs = emit_fields(osm, rough)
    print(f"   {len(fs)} fields, {sum(f['ha'] for f in fs):.0f} ha, "
          f"largest {max(f['ha'] for f in fs):.1f} ha")
    print("5. Roads and railway...")
    counts, bridges = emit_roads(osm)
    added = connect_road_crossings(osm)
    print(f"   {sum(counts.values())} ways, {bridges} on bridges, "
          f"{added} junction nodes stitched in")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), OUT_NAME)
    write_osm(osm, out)

    # Read the file back and measure it, rather than trusting the numbers just written:
    # a sign slip in the projection is invisible in the raw degrees and obvious here.
    root = ET.parse(out).getroot()
    b = root.find('bounds').attrib
    sw = mx.global_to_local(float(b['minlat']), float(b['minlon']))
    ne = mx.global_to_local(float(b['maxlat']), float(b['maxlon']))
    nodes = root.findall('node')
    local = [mx.global_to_local(float(n.get('lat')), float(n.get('lon'))) for n in nodes]
    outside = [p for p in local
               if not (-0.5 <= p[0] <= ml.PLAYABLE_M + 0.5
                       and -0.5 <= p[1] <= ml.PLAYABLE_M + 0.5)]
    print(f"   extent {abs(ne[0] - sw[0]):.3f} x {abs(sw[1] - ne[1]):.3f} m, "
          f"{len(nodes)} nodes, {len(root.findall('way'))} ways")
    if outside:
        print(f"!! {len(outside)} nodes outside the playable area, e.g. "
              f"{outside[0][0]:.1f},{outside[0][1]:.1f}")
        return 1
    print(f"[+] Wrote '{out}'.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
