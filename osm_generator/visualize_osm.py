#!/usr/bin/env python3
import os
import math
import xml.etree.ElementTree as ET
import matplotlib
matplotlib.use('Agg') # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# Map center parameters (matching generator)
lat_center = 43.145692357357156
lon_center = -95.1450786604236

def global_to_local(lat, lon):
    delta_y = (lat - lat_center) * 111111.0
    delta_x = (lon - lon_center) * (111111.0 * math.cos(math.radians(lat_center)))
    x = delta_x + 4096.0
    y = 4096.0 - delta_y
    return x, y

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    osm_path = os.path.join(script_dir, "map.osm")
    output_png = os.path.join(script_dir, "map_osm_visual.png")

    if not os.path.exists(osm_path):
        print(f"Error: {osm_path} not found.")
        return

    # Parse OSM XML
    tree = ET.parse(osm_path)
    root = tree.getroot()

    # Parse Nodes
    nodes = {}
    for node in root.findall('node'):
        nid = int(node.get('id'))
        lat = float(node.get('lat'))
        lon = float(node.get('lon'))
        x, y = global_to_local(lat, lon)
        nodes[nid] = (x, y)

    # Parse Ways
    ways = []
    for way in root.findall('way'):
        wid = int(way.get('id'))
        nd_refs = [int(nd.get('ref')) for nd in way.findall('nd')]
        
        tags = {}
        for tag in way.findall('tag'):
            tags[tag.get('k')] = tag.get('v')
            
        ways.append({
            'id': wid,
            'node_refs': nd_refs,
            'tags': tags
        })

    # Plotting setup
    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    
    # Dark Theme Colors
    bg_color = '#0F172A'       # Slate 900
    grid_color = '#334155'     # Slate 700
    border_color = '#E2E8F0'   # Slate 200
    
    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # Set axes limits (Playable area is 0 to 8192 meters)
    ax.set_xlim(-100, 8392)
    ax.set_ylim(8392, -100) # Inverted Y-axis to match 0 at North/Top

    # Labels and grid
    ax.set_xlabel("X (East-West) [meters]", fontsize=12, fontweight='bold', color='white')
    ax.set_ylabel("Y (North-South) [meters]", fontsize=12, fontweight='bold', color='white')
    ax.tick_params(colors='white')
    ax.grid(True, which='both', color=grid_color, linestyle='--', linewidth=0.5, alpha=0.5)
    
    for spine in ax.spines.values():
        spine.set_color(grid_color)

    # Draw Playable Area Border
    rect_playable = patches.Rectangle((0, 0), 8192, 8192, 
                                      fill=False, edgecolor='#6366F1', linewidth=2.5, linestyle='--', 
                                      label='Playable Area (8.2 km)')
    ax.add_patch(rect_playable)

    # Legend elements mapping to customize labels
    has_farmyard = False
    has_water = False
    has_primary = False
    has_secondary = False
    has_rail = False

    # Draw Ways
    for way in ways:
        coords = [nodes[ref] for ref in way['node_refs'] if ref in nodes]
        if not coords:
            continue
            
        x_coords, y_coords = zip(*coords)
        tags = way['tags']
        name = tags.get('name', '')

        # Area features (Polygons)
        # NOTE: woods are tagged natural=wood AND landuse=farmyard, so they must be
        # matched before the farmyard branch or they render as a farmyard.
        if 'natural' in tags and tags['natural'] == 'wood':
            # Forest (wood)
            poly = patches.Polygon(coords, closed=True, facecolor='#15803D', edgecolor='#16A34A', alpha=0.45, linewidth=1.5)
            ax.add_patch(poly)

        elif 'landuse' in tags and tags['landuse'] == 'farmyard':
            # Town, Yard 7, Town Farmyard
            if name.startswith('Yard'):
                color = '#D97706' # Amber 600 - Yard 7 and the parcels converted to yard
                has_farmyard = True
            elif name == 'Town Farmyard':
                color = '#059669' # Emerald 600
            elif name.startswith('Open Ground'):
                color = '#78716C' # Stone 500 - leftover open ground, neither field nor forest
            else: # Town
                color = '#4F46E5' # Indigo 600
                
            poly = patches.Polygon(coords, closed=True, facecolor=color, edgecolor=color, alpha=0.35, linewidth=1.5)
            ax.add_patch(poly)

        elif 'natural' in tags and tags['natural'] == 'water':
            # Reservoir or Canal
            poly = patches.Polygon(coords, closed=True, facecolor='#0284C7', edgecolor='#0EA5E9', alpha=0.6, linewidth=1.5)
            ax.add_patch(poly)

        elif 'landuse' in tags and tags['landuse'] == 'farmland':
            # Farmland Field
            poly = patches.Polygon(coords, closed=True, facecolor='#EAB308', edgecolor='#CA8A04', alpha=0.18, linewidth=0.8)
            ax.add_patch(poly)

        # Line features (Roads / Rail)
        elif 'highway' in tags and tags['highway'] == 'primary':
            ax.plot(x_coords, y_coords, color='#F97316', linewidth=4.0, solid_capstyle='round')
        elif 'highway' in tags and tags['highway'] == 'secondary':
            has_secondary = True
            ax.plot(x_coords, y_coords, color='#94A3B8', linewidth=1.8, linestyle='-')
        elif 'highway' in tags and tags['highway'] == 'tertiary':
            ax.plot(x_coords, y_coords, color='#A16207', linewidth=1.0, linestyle='-', alpha=0.7)
        elif 'railway' in tags and tags['railway'] == 'rail':
            # Railway visual (two lines overlayed to look like train tracks)
            ax.plot(x_coords, y_coords, color='#475569', linewidth=3.5)
            ax.plot(x_coords, y_coords, color='#FFFFFF', linewidth=1.0, linestyle='--')

    # Draw nodes for intersections (specifically connection nodes on Primary road)
    # Filter nodes that are shared by more than one way
    node_usage = {}
    for way in ways:
        for ref in way['node_refs']:
            node_usage[ref] = node_usage.get(ref, 0) + 1
            
    shared_nodes = [nodes[ref] for ref, count in node_usage.items() if count > 1 and ref in nodes]
    if shared_nodes:
        x_sh, y_sh = zip(*shared_nodes)
        ax.scatter(x_sh, y_sh, color='#EF4444', s=25, zorder=5)

    # Set Title
    ax.set_title(f"OSM Layout Visualization - Playable 8.2km Area\nCenter: {lat_center:.6f}, {lon_center:.6f}", 
                 color='white', fontsize=14, fontweight='bold', pad=15)

    # Save
    plt.savefig(output_png, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"[+] Saved visualization successfully to '{output_png}'.")

if __name__ == '__main__':
    main()
