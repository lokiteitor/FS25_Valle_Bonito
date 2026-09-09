#!/usr/bin/env python3
"""Inventory of what is actually in map.osm, and the invariants it has to hold.

Two jobs. The inventory counts and measures every feature by kind - areas by hectares,
roads by kilometres and class - so a change of shape somewhere in `map_layout` shows up
as a number moving rather than as a picture that looks about right. The invariants are
the things that are wrong in a way no picture would show:

  * every node inside the playable area, and every way pointing at nodes that exist
  * every area ring closing on its own first node id - the 3D viewer compares the first
    and last coordinate exactly, and an area that does not close is drawn as a line
  * every way carrying a tag both renderers actually draw. The vocabulary is closed
    (`map_layout.RENDERED_TAGS`); a way tagged with anything else disappears from both
    renderers without a word, which is worse than not emitting it
  * nothing planted inside the clean strip along the boundary
  * the layout and the file agreeing on how much is on the map - the OSM half going
    quiet while `map_layout` still carries features is exactly the failure the
    shared-geometry rule exists to catch

Exits non-zero if an invariant is broken, so it can gate the pipeline.
"""
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import map_extent as ms

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import map_layout as ml                                             # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"   {'ok  ' if ok else 'FAIL'}  {name}{('   ' + detail) if detail else ''}")
    if not ok:
        _failures.append(name)
    return ok


def area_key(tags):
    """Which bucket a tagged ring belongs in. `natural=wood` is tested before landuse:
    a shelterbelt carries both tags and the wood reading is the meaningful one."""
    if tags.get('natural') == 'wood' or tags.get('landuse') == 'forest':
        return 'wood'
    if tags.get('natural') == 'water':
        return 'water'
    if tags.get('landuse') in ('farmland', 'farmyard'):
        return tags['landuse']
    return None


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    osm_path = os.path.join(script_dir, "map.osm")
    if not os.path.exists(osm_path):
        print(f"Error: {osm_path} not found. Run generate_osm.py first.")
        return 2

    root = ET.parse(osm_path).getroot()
    nodes = {int(n.get('id')): ms.global_to_local(float(n.get('lat')),
                                                  float(n.get('lon')))
             for n in root.findall('node')}
    ways = root.findall('way')

    groups = {'farmland': [], 'wood': [], 'farmyard': [], 'water': []}
    rings = {'farmland': [], 'wood': [], 'farmyard': [], 'water': []}
    roads = Counter()
    road_km = Counter()
    bridges = 0
    dangling = 0

    for way in ways:
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        refs = [int(nd.get('ref')) for nd in way.findall('nd')]
        dangling += sum(1 for r in refs if r not in nodes)
        coords = [nodes[r] for r in refs if r in nodes]
        if len(coords) < 2:
            continue
        name = tags.get('name', '(unnamed)')

        if 'highway' in tags or 'railway' in tags:
            kind = tags.get('highway') or f"rail:{tags['railway']}"
            roads[kind] += 1
            road_km[kind] += ms.polyline_length(coords) / 1000.0
            if tags.get('bridge') == 'yes':
                bridges += 1
            continue

        key = area_key(tags)
        if key is None:
            continue
        groups[key].append((name, ms.ring_area_ha(coords), len(coords)))
        rings[key].append(coords)

    print(f"=== {os.path.basename(osm_path)}: {len(nodes)} nodes, {len(ways)} ways ===")
    print(f"layout: {ml.summary()}")
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

    if roads:
        print(f"\nroads: {sum(roads.values())} ways, "
              f"{sum(road_km.values()):.1f} km, {bridges} bridge(s)")
        for kind in sorted(roads, key=lambda k: -road_km[k]):
            print(f"   {kind:<12} {roads[kind]:3d} ways  {road_km[kind]:6.2f} km")
    else:
        print("\nroads: none")

    # Nodes shared by more than one way are the junctions; a road network with none
    # would mean every way is topologically isolated.
    usage = Counter()
    for way in ways:
        for nd in way.findall('nd'):
            usage[int(nd.get('ref'))] += 1
    shared = sum(1 for v in usage.values() if v > 1)
    print(f"\nshared nodes (junctions): {shared}")

    # ------------------------------------------------------------------ invariants
    print("\ninvariants:")
    b = root.find('bounds')
    if check("the file declares its bounds", b is not None):
        sw = ms.global_to_local(float(b.get('minlat')), float(b.get('minlon')))
        ne = ms.global_to_local(float(b.get('maxlat')), float(b.get('maxlon')))
        w = abs(ne[0] - sw[0])
        h = abs(sw[1] - ne[1])
        check(f"the bounds round-trip to {ms.PLAYABLE_M:.0f} m square",
              abs(w - ms.PLAYABLE_M) < 0.5 and abs(h - ms.PLAYABLE_M) < 0.5,
              f"{w:.3f} x {h:.3f} m")

    check("every way points at nodes that exist", dangling == 0,
          f"{dangling} dangling reference(s)")
    orphans = [nid for nid in nodes if usage[nid] == 0]
    check("no orphan nodes", not orphans, f"{len(orphans)} node(s) in no way")

    out_of_bounds = [(x, y) for x, y in nodes.values()
                     if not (-0.5 <= x <= ms.PLAYABLE_M + 0.5
                             and -0.5 <= y <= ms.PLAYABLE_M + 0.5)]
    check("every node inside the playable area", not out_of_bounds,
          f"{len(out_of_bounds)} outside")

    unclosed = 0
    unrendered = []
    for way in ways:
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        refs = [nd.get('ref') for nd in way.findall('nd')]
        is_area = any(k in tags for k in ('landuse', 'natural')) and 'highway' not in tags
        if is_area and (len(refs) < 4 or refs[0] != refs[-1]):
            unclosed += 1
        if not any(k in tags and (v is None or tags[k] == v)
                   for k, v in ml.RENDERED_TAGS):
            unrendered.append(tags)
    check("every area closes on its first node", unclosed == 0, f"{unclosed} open")
    check("every way carries a tag both renderers draw", not unrendered,
          f"{len(unrendered)} invisible")

    # Nothing the planting places may stand in the clean strip along the boundary: a
    # ring cut off square by the map edge reads as half of itself, and that strip is the
    # ground the rim rises out of. Water is exempt - it has to leave
    # the map - and so are the roads, for the same reason.
    in_strip = []
    closest = -1e9
    planted = rings['farmland'] + rings['wood'] + rings['farmyard']
    for r in planted:
        d = max(ml.playable_sdf(x, y) for x, y in r)
        closest = max(closest, d)
        # half a metre of slack, the same the boundary check allows: the coordinates
        # made the round trip through lat/lon and come back a hair off
        if d > -ml.EDGE_CLEAR_M + 0.5:
            in_strip.append(d)
    check(f"nothing inside the {ml.EDGE_CLEAR_M:.0f} m strip along the boundary",
          not in_strip,
          f"{len(in_strip)} way(s) in it" if in_strip
          else (f"closest way is {-closest:.0f} m in from the boundary" if planted
                else "nothing planted yet"))

    # Both halves of the pipeline describe the same world or neither does. A layout that
    # carries features while the file is empty means the OSM side stopped emitting them,
    # and nothing else here would notice.
    layout_features = (len(ml.water()) + len(ml.pads()) + len(ml.areas())
                       + len(ml.corridors()))
    check("the file and the layout agree on whether the map is empty",
          (layout_features == 0) == (len(ways) == 0),
          f"{layout_features} feature(s) in map_layout, {len(ways)} way(s) in the file")

    problems = ml.validate()
    check("map_layout validates", not problems,
          "; ".join(problems) if problems else "no complaints")

    print(f"\n{len(_failures)} invariant(s) broken" if _failures
          else "\nall invariants hold")
    return 1 if _failures else 0


if __name__ == '__main__':
    sys.exit(main() or 0)
