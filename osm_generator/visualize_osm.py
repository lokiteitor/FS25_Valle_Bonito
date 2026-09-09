#!/usr/bin/env python3
"""Renders map.osm to a PNG for eyeballing the layout without opening an editor."""
import os
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D

import map_extent as ms


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    osm_path = os.path.join(script_dir, "map.osm")
    output_png = os.path.join(script_dir, "map_osm_visual.png")

    if not os.path.exists(osm_path):
        print(f"Error: {osm_path} not found. Run generate_osm.py first.")
        return

    root = ET.parse(osm_path).getroot()
    nodes = {int(n.get('id')): ms.global_to_local(float(n.get('lat')),
                                                  float(n.get('lon')))
             for n in root.findall('node')}

    ways = []
    for way in root.findall('way'):
        ways.append({
            'coords': [nodes[int(nd.get('ref'))] for nd in way.findall('nd')
                       if int(nd.get('ref')) in nodes],
            'tags': {t.get('k'): t.get('v') for t in way.findall('tag')},
        })

    fig, ax = plt.subplots(figsize=(11, 11), dpi=150)
    bg, grid_c = '#0F172A', '#334155'
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    pad = ms.PLAYABLE_M * 0.02
    ax.set_xlim(-pad, ms.PLAYABLE_M + pad)
    ax.set_ylim(ms.PLAYABLE_M + pad, -pad)     # y grows southwards
    ax.set_xlabel("X (East-West) [metres]", fontsize=11, fontweight='bold', color='white')
    ax.set_ylabel("Y (North-South) [metres]", fontsize=11, fontweight='bold', color='white')
    ax.tick_params(colors='white')
    ax.grid(True, color=grid_c, linestyle='--', linewidth=0.5, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_color(grid_c)

    ax.add_patch(patches.Rectangle((0, 0), ms.PLAYABLE_M, ms.PLAYABLE_M, fill=False,
                                   edgecolor='#6366F1', linewidth=2.0, linestyle='--'))

    counts = {'farmland': 0, 'wood': 0, 'belt': 0, 'farmyard': 0, 'industry': 0,
              'water': 0}
    for way in ways:
        coords = way['coords']
        if len(coords) < 2:
            continue
        xs, ys = zip(*coords)
        tags = way['tags']
        name = tags.get('name', '')

        # natural=wood is checked before landuse: woods carry both tags and the wood
        # reading is the meaningful one.
        if tags.get('natural') == 'wood':
            # Conifer against broadleaf, the same way an industrial apron is drawn apart
            # from a farmyard: they are one tag to the renderer's own vocabulary, and
            # telling them apart here is the only place `leaf_type` shows.
            needle = tags.get('leaf_type') == 'needleleaved'
            ax.add_patch(patches.Polygon(
                coords, closed=True, facecolor='#14532D' if needle else '#15803D',
                edgecolor='#34D399' if needle else '#22C55E', alpha=0.55, linewidth=0.8))
            counts['wood' if needle else 'belt'] += 1
        elif tags.get('natural') == 'water':
            ax.add_patch(patches.Polygon(coords, closed=True, facecolor='#0284C7',
                                         edgecolor='#38BDF8', alpha=0.85, linewidth=1.0))
            counts['water'] += 1
        elif tags.get('landuse') == 'farmyard':
            # An industrial apron is drawn apart from a yard: they are the same tag to
            # the renderers, and telling them apart here is the only place it shows.
            industrial = tags.get('building') == 'industrial'
            colour = '#6366F1' if industrial else '#DB2777'
            ax.add_patch(patches.Polygon(coords, closed=True, facecolor=colour,
                                         edgecolor=colour, alpha=0.55, linewidth=1.2))
            # Only a ring wide enough to hold the label gets one. A town block is
            # 100 m on an eight-kilometre plot - sixteen characters of bold white text
            # across seventeen pixels - and sixty-four of them turned the four towns
            # into a smear that hid the street grid underneath.
            if max(xs) - min(xs) >= 300.0:
                ax.text(sum(xs) / len(xs), sum(ys) / len(ys), name.split(' (')[0][:18],
                        color='white', fontsize=6, fontweight='bold', ha='center',
                        va='center', zorder=8)
            counts['industry' if industrial else 'farmyard'] += 1
        elif tags.get('landuse') == 'farmland':
            ax.add_patch(patches.Polygon(coords, closed=True, facecolor='#A3E635',
                                         edgecolor='#65A30D', alpha=0.25, linewidth=0.6))
            counts['farmland'] += 1
        elif tags.get('highway') == 'primary':
            ax.plot(xs, ys, color='#F97316', linewidth=3.0, solid_capstyle='round',
                    zorder=6)
        elif tags.get('highway') == 'secondary':
            ax.plot(xs, ys, color='#FCD34D', linewidth=1.8, solid_capstyle='round',
                    zorder=6)
        elif tags.get('highway') == 'tertiary':
            ax.plot(xs, ys, color='#CBD5E1', linewidth=0.9, alpha=0.85, zorder=5)
        elif 'railway' in tags:
            ax.plot(xs, ys, color='#475569', linewidth=3.0, zorder=5)
            ax.plot(xs, ys, color='#FFFFFF', linewidth=0.9, linestyle='--', zorder=5)

    # Bridges on top of everything, so the crossings are visible.
    for way in ways:
        if way['tags'].get('bridge') == 'yes' and len(way['coords']) >= 2:
            xs, ys = zip(*way['coords'])
            ax.plot(xs, ys, color='#F43F5E', linewidth=4.0, solid_capstyle='butt',
                    zorder=7)

    # Nodes shared by more than one way, i.e. the junctions.
    usage = {}
    for way in ways:
        for pt in way['coords']:
            key = (round(pt[0], 2), round(pt[1], 2))
            usage[key] = usage.get(key, 0) + 1
    shared = [k for k, v in usage.items() if v > 1]
    if shared:
        ax.scatter(*zip(*shared), color='#EF4444', s=6, zorder=9)

    legend = [
        Line2D([], [], color='#F97316', lw=3, label='Main road'),
        Line2D([], [], color='#FCD34D', lw=2, label='Village / link road'),
        Line2D([], [], color='#CBD5E1', lw=1, label='Farm lane'),
        Line2D([], [], color='#F43F5E', lw=3, label='Bridge'),
        patches.Patch(facecolor='#A3E635', alpha=0.4, label=f"Farmland ({counts['farmland']})"),
        patches.Patch(facecolor='#14532D', alpha=0.6,
                      label=f"Wood, needleleaf ({counts['wood']})"),
        patches.Patch(facecolor='#15803D', alpha=0.6,
                      label=f"Wood, broadleaf ({counts['belt']})"),
        patches.Patch(facecolor='#DB2777', alpha=0.6, label=f"Farmyard ({counts['farmyard']})"),
        patches.Patch(facecolor='#6366F1', alpha=0.6,
                      label=f"Industrial ({counts['industry']})"),
        patches.Patch(facecolor='#0284C7', alpha=0.8, label='River'),
    ]
    ax.legend(handles=legend, loc='upper right', facecolor='#111827',
              labelcolor='white', fontsize=8, framealpha=0.9)

    ax.set_title(f"OSM layout - {ms.PLAYABLE_M/1000:.1f} x {ms.PLAYABLE_M/1000:.1f} km "
                 f"playable area\nCentre: {ms.LAT_CENTER:.4f}, {ms.LON_CENTER:.4f}",
                 color='white', fontsize=13, fontweight='bold', pad=14)
    ax.set_aspect('equal')

    plt.savefig(output_png, bbox_inches='tight', facecolor=fig.get_facecolor())
    plt.close()
    print(f"[+] Saved visualization to '{output_png}'  "
          f"({counts['farmland']} fields, {counts['wood']} woods, "
          f"{counts['belt']} broadleaf, "
          f"{counts['farmyard']} farmyards, {counts['industry']} industrial)")


if __name__ == '__main__':
    main()
