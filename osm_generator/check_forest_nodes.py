import os
import xml.etree.ElementTree as ET
import numpy as np
import math

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
    
    tree = ET.parse(osm_path)
    root = tree.getroot()
    
    nodes = {}
    for node in root.findall('node'):
        nid = int(node.get('id'))
        lat = float(node.get('lat'))
        lon = float(node.get('lon'))
        x, y = global_to_local(lat, lon)
        nodes[nid] = (x, y)
        
    for way in root.findall('way'):
        tags = {tag.get('k'): tag.get('v') for tag in way.findall('tag')}
        name = tags.get('name', f"Way {way.get('id')}")
        if 'natural' in tags and tags['natural'] == 'wood':
            nd_refs = [int(nd.get('ref')) for nd in way.findall('nd')]
            coords = [nodes[ref] for ref in nd_refs if ref in nodes]
            print(f"\nForest way '{name}' (nodes={len(coords)}):")
            print("First 10 nodes:", [f"({pt[0]:.2f}, {pt[1]:.2f})" for pt in coords[:10]])
            print("Last 10 nodes:", [f"({pt[0]:.2f}, {pt[1]:.2f})" for pt in coords[-10:]])

if __name__ == '__main__':
    main()
