#!/usr/bin/env python3
"""Inventory of what is actually in map.osm, by feature type.

A sanity pass over the generated file: counts, areas and the biggest features of each
kind, the road network by class, and the invariants the brief sets - at most 200 fields,
none of them over 100 ha, nothing outside the playable area, every ring closed and every
way carrying a tag the renderers actually draw.

Exits non-zero if an invariant is broken, so it can gate the pipeline.
"""
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import map_extent as ms

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402

MAX_FIELDS = 200
MAX_FIELD_HA = 100.0
MIN_FIELD_HA = 3.0

# Exactly what visualize_osm.py and visualizer/create_3d_viewer.py know how to draw. A
# way matching none of these is dropped by both without a word.
RENDERED = (('natural', 'water'), ('water', None), ('natural', 'wood'),
            ('landuse', 'forest'), ('landuse', 'farmyard'), ('landuse', 'farmland'),
            ('railway', None), ('highway', None))

_failures = []


def check(name, ok, detail=""):
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    osm_path = os.path.join(script_dir, "map.osm")
    if not os.path.exists(osm_path):
        print(f"Error: {osm_path} not found. Run generate_osm.py first.")
        return

    root = ET.parse(osm_path).getroot()
    nodes = {int(n.get('id')): ms.global_to_local(float(n.get('lat')),
                                                  float(n.get('lon')))
             for n in root.findall('node')}

    groups = {'farmland': [], 'wood': [], 'farmyard': [], 'water': []}
    roads = Counter()
    road_km = Counter()
    bridges = 0

    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        coords = [nodes[int(nd.get('ref'))] for nd in way.findall('nd')
                  if int(nd.get('ref')) in nodes]
        if len(coords) < 2:
            continue
        name = tags.get('name', '(unnamed)')

        if 'highway' in tags:
            kind = tags['highway']
            roads[kind] += 1
            road_km[kind] += ms.polyline_length(coords) / 1000.0
            if tags.get('bridge') == 'yes':
                bridges += 1
            continue

        # natural=wood is checked before landuse: woods carry both tags.
        if tags.get('natural') == 'wood':
            key = 'wood'
        elif tags.get('natural') == 'water':
            key = 'water'
        elif tags.get('landuse') in ('farmland', 'farmyard'):
            key = tags['landuse']
        else:
            continue
        groups[key].append((name, ms.ring_area_ha(coords), len(coords)))

    print(f"=== {os.path.basename(osm_path)}: {len(nodes)} nodes, "
          f"{len(root.findall('way'))} ways ===")
    playable_ha = ms.PLAYABLE_M ** 2 / 10000.0
    for key in ('farmland', 'wood', 'farmyard', 'water'):
        items = groups[key]
        if not items:
            print(f"\n{key}: none")
            continue
        total = sum(a for _, a, _ in items)
        areas = sorted(a for _, a, _ in items)
        print(f"\n{key}: {len(items)} ways, {total:.0f} ha "
              f"({total / playable_ha * 100:.1f}% of the playable area)")
        print(f"   size ha: min {areas[0]:.1f}  median {areas[len(areas)//2]:.1f}  "
              f"max {areas[-1]:.1f}")
        for name, area, npts in sorted(items, key=lambda z: -z[1])[:5]:
            print(f"   {area:7.1f} ha  {npts:4d} nodes  {name}")

    print(f"\nroads: {sum(roads.values())} ways, "
          f"{sum(road_km.values()):.1f} km, {bridges} bridge(s)")
    for kind in sorted(roads, key=lambda k: -road_km[k]):
        print(f"   {kind:<12} {roads[kind]:3d} ways  {road_km[kind]:6.2f} km")

    # Nodes shared by more than one way are the junctions; a road network with none
    # would mean every way is topologically isolated.
    usage = Counter()
    for way in root.findall('way'):
        for nd in way.findall('nd'):
            usage[int(nd.get('ref'))] += 1
    shared = sum(1 for v in usage.values() if v > 1)
    print(f"\nshared nodes (junctions): {shared}")

    # ------------------------------------------------------------------ invariants
    print("\ninvariants:")
    fields = groups['farmland']
    areas = [a for _, a, _ in fields]
    check(f"at most {MAX_FIELDS} fields", len(fields) <= MAX_FIELDS, f"{len(fields)}")
    check(f"no field over {MAX_FIELD_HA:.0f} ha", not areas or max(areas) <= MAX_FIELD_HA,
          f"largest {max(areas):.1f} ha" if areas else "no fields")
    check(f"no field under {MIN_FIELD_HA:.0f} ha", not areas or min(areas) >= MIN_FIELD_HA,
          f"smallest {min(areas):.1f} ha" if areas else "no fields")

    edges = [0, 3, 8, 12, 22, 35, 65, 100, 1e9]
    labels = ["0-3", "3-8", "8-12", "12-22", "22-35", "35-65", "65-100", "100+"]
    hist = [sum(1 for a in areas if lo <= a < hi)
            for lo, hi in zip(edges[:-1], edges[1:])]
    print("         field sizes   " + "  ".join(
        f"{lab} {n}" for lab, n in zip(labels, hist)))

    out_of_bounds = [(x, y) for x, y in nodes.values()
                     if not (-0.5 <= x <= ms.PLAYABLE_M + 0.5
                             and -0.5 <= y <= ms.PLAYABLE_M + 0.5)]
    check("every node inside the playable area", not out_of_bounds,
          f"{len(out_of_bounds)} outside")

    unclosed = 0
    unrendered = []
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        refs = [nd.get('ref') for nd in way.findall('nd')]
        is_area = any(k in tags for k in ('landuse', 'natural')) and 'highway' not in tags
        if is_area and (len(refs) < 4 or refs[0] != refs[-1]):
            unclosed += 1
        if not any(k in tags and (v is None or tags[k] == v) for k, v in RENDERED):
            unrendered.append(tags)
    check("every area closes on its first node", unclosed == 0, f"{unclosed} open")
    check("every way carries a tag both renderers draw", not unrendered,
          f"{len(unrendered)} invisible")

    # Nothing cropped inside the basin, and no timber standing in the water. Both are
    # easy to break from the far side of the pipeline - widen a meander, move the lake -
    # and neither shows up in any count.
    river = ml.river_axis()
    creek = ml.creek_axis()
    rings = {'farmland': [], 'wood': []}
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        coords = [nodes[int(nd.get('ref'))] for nd in way.findall('nd')
                  if int(nd.get('ref')) in nodes]
        if tags.get('natural') == 'wood':
            rings['wood'].append(coords)
        elif tags.get('landuse') == 'farmland':
            rings['farmland'].append(coords)

    def clearance(coords, axis):
        return min((ml.dist_to_polyline(p, axis) for p in coords), default=1e9)

    in_basin = [c for c in rings['farmland']
                if clearance(c, river) < ml.RIVER['riparian_half_w']
                or clearance(c, creek) < ml.CREEK['riparian_half_w']
                or any(ml._in_lake(p, ml.LAKE_MARGIN_R) for p in c)]
    check("no field inside the river basin", not in_basin,
          f"{len(in_basin)} of {len(rings['farmland'])}; nearest field is "
          f"{min((clearance(c, river) for c in rings['farmland']), default=0):.0f} m "
          "from the centreline")

    in_water = [c for c in rings['wood']
                if clearance(c, river) < ml.RIVER['water_half_w']
                or clearance(c, creek) < ml.CREEK['water_half_w']
                or any(ml._in_lake(p) for p in c)]
    check("no timber standing in the water", not in_water,
          f"{len(in_water)} of {len(rings['wood'])}")

    # A shelterbelt is 24 m across and the occupancy raster is 32 m, so a belt can fall
    # between two cell centres, mark nothing, and have a field laid straight over it.
    # Sampling the belt against the parcels is the only way to see that from here.
    yards = []
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        if tags.get('landuse') == 'farmyard' and tags.get('natural') != 'wood':
            yards.append([nodes[int(nd.get('ref'))] for nd in way.findall('nd')
                          if int(nd.get('ref')) in nodes])

    def bbox(r):
        xs = [p[0] for p in r]
        ys = [p[1] for p in r]
        return min(xs), min(ys), max(xs), max(ys)

    over_field = over_yard = 0
    for w in rings['wood']:
        x0, y0, x1, y1 = bbox(w)
        pts = [(x0 + (x1 - x0) * i / 16.0, y0 + (y1 - y0) * j / 8.0)
               for i in range(1, 16) for j in range(1, 8)]
        for target, flag in ((rings['farmland'], 'f'), (yards, 'y')):
            for r in target:
                b = bbox(r)
                if b[2] < x0 or b[0] > x1 or b[3] < y0 or b[1] > y1:
                    continue
                if any(ml.point_in_ring(p, r) for p in pts):
                    if flag == 'f':
                        over_field += 1
                    else:
                        over_yard += 1
                    break
    check("no tree row laid over a field", over_field == 0,
          f"{over_field} of {len(rings['wood'])}")
    check("no tree row laid over a yard", over_yard == 0,
          f"{over_yard} of {len(rings['wood'])}")

    # The parcelling cuts its blocks on the corridor alignments, but 270th Avenue sits
    # off the section line by the width of the railway's right of way. While the blocks
    # were cut on the grid instead, the road ran 26 m inside the field beside it.
    over_road = 0
    for c in ml.corridors():
        ax = c['axis']
        vertical = abs(ax[0][0] - ax[-1][0]) < abs(ax[0][1] - ax[-1][1])
        fixed = ax[0][0] if vertical else ax[0][1]
        lo = min(p[1] for p in ax) if vertical else min(p[0] for p in ax)
        hi = max(p[1] for p in ax) if vertical else max(p[0] for p in ax)
        hw = c['half_width_m']
        a0, a1 = (fixed - hw, fixed + hw)
        for r in rings['farmland']:
            fx0, fy0, fx1, fy1 = bbox(r)
            if vertical:
                hit = min(fx1, a1) > max(fx0, a0) and min(fy1, hi) > max(fy0, lo)
            else:
                hit = min(fx1, hi) > max(fx0, lo) and min(fy1, a1) > max(fy0, a0)
            if hit:
                over_road += 1
    check("no road laid across a field", over_road == 0,
          f"{over_road} field/roadway overlaps")

    print(f"\n{len(_failures)} invariant(s) broken" if _failures
          else "\nall invariants hold")
    return 1 if _failures else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
