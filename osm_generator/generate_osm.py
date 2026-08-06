#!/usr/bin/env python3
import os
import math
import re
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Map parameters
lat_center = 43.145692357357156
lon_center = -95.1450786604236
size_m = 8192.0 # Playable area size (8.192 km)

# Local coordinate to Lat/Lon conversion
def local_to_global(x, y):
    # x: 0 to 8192 (West to East)
    # y: 0 to 8192 (North to South)
    delta_y = 4096.0 - y
    delta_x = x - 4096.0
    lat = lat_center + (delta_y / 111111.0)
    lon = lon_center + (delta_x / (111111.0 * math.cos(math.radians(lat_center))))
    return lat, lon

def main():
    print("=== Generating OSM data for FS25 map ===")
    
    # 0. Load DEM once for elevation checks
    Image.MAX_IMAGE_PIXELS = None
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dem_path = os.path.normpath(os.path.join(script_dir, "../dem_generator/dem_new_12k.png"))
    
    if not os.path.exists(dem_path):
        print(f"Error: DEM file not found at {dem_path}")
        return
        
    img = Image.open(dem_path)
    data = np.array(img, dtype=np.float32)
    playable = data[2048:10240, 2048:10240]
    
    # Forest polygon extraction (elevations >= 55m in the new DEM). Pulled up here
    # because the forest ways AND the is_in_forest() queries below are both driven
    # by the same kept polygons.
    def get_border(pt):
        x, y = pt
        if math.isclose(x, 0.0, abs_tol=1e-3): return 'W'
        if math.isclose(x, 8192.0, abs_tol=1e-3): return 'E'
        if math.isclose(y, 0.0, abs_tol=1e-3): return 'N'
        if math.isclose(y, 8192.0, abs_tol=1e-3): return 'S'
        return None

    def close_segment(seg):
        start = seg[0]
        end = seg[-1]

        if np.allclose(start, end, atol=1e-3):
            return [tuple(pt) for pt in seg]

        border_start = get_border(start)
        border_end = get_border(end)

        path = [tuple(pt) for pt in seg]

        if border_start and border_end:
            if border_start == border_end:
                path.append(tuple(start))
            else:
                corners = {
                    ('E', 'S'): (8192.0, 8192.0),
                    ('S', 'E'): (8192.0, 8192.0),
                    ('W', 'S'): (0.0, 8192.0),
                    ('S', 'W'): (0.0, 8192.0),
                    ('E', 'N'): (8192.0, 0.0),
                    ('N', 'E'): (8192.0, 0.0),
                    ('W', 'N'): (0.0, 0.0),
                    ('N', 'W'): (0.0, 0.0),
                }
                pair = (border_end, border_start)
                if pair in corners:
                    path.append(corners[pair])
                path.append(tuple(start))
        else:
            path.append(tuple(start))

        return path

    def get_forest_polygons():
        grid_size = 257
        idx = np.linspace(0, 8191, grid_size, dtype=int)
        playable_sub = playable[idx, :][:, idx]

        x_grid = np.linspace(0, 8192, grid_size)
        y_grid = np.linspace(0, 8192, grid_size)
        X, Y = np.meshgrid(x_grid, y_grid)

        fig, ax = plt.subplots()
        cs = ax.contour(X, Y, playable_sub, levels=[5500.0])
        plt.close(fig)

        segs = cs.allsegs[0]
        polygons = []
        for seg in segs:
            closed_poly = close_segment(seg)
            polygons.append(closed_poly)

        return polygons

    # All DEM forest contours are kept: the central-western band and the eastern
    # mountain's forest ring. What no longer exists is the old elevation blanket -
    # is_in_forest() below is driven by these polygons instead of raw elevation, so
    # only the contoured woods block fields and roads, and the flats around the
    # mountain get farmed.
    forest_polys_kept = get_forest_polygons()

    # Rasterise the kept forest for the clearance queries below. is_in_forest() is
    # driven by the kept polygons, not by raw elevation, so the high ground where
    # the eastern forest stood is reclaimed by fields and roads.
    FOREST_MASK_SCALE = 4.0     # metres per pixel
    mask_n = int(round(8192.0 / FOREST_MASK_SCALE))
    _fimg = Image.new('L', (mask_n, mask_n), 0)
    _fdraw = ImageDraw.Draw(_fimg)
    for _poly in forest_polys_kept:
        _fdraw.polygon([(px / FOREST_MASK_SCALE, py / FOREST_MASK_SCALE)
                        for px, py in _poly], fill=255, outline=255)
    forest_mask = np.array(_fimg) > 0

    def is_in_forest(x, y, buffer_m=5.0):
        x_min = max(0, int((x - buffer_m) / FOREST_MASK_SCALE))
        x_max = min(mask_n - 1, int((x + buffer_m) / FOREST_MASK_SCALE))
        y_min = max(0, int((y - buffer_m) / FOREST_MASK_SCALE))
        y_max = min(mask_n - 1, int((y + buffer_m) / FOREST_MASK_SCALE))
        if x_max < x_min or y_max < y_min:
            return False
        return bool(forest_mask[y_min:y_max+1, x_min:x_max+1].any())

    def get_forest_limit_y(x, y_start, y_end, buffer_m=5.0):
        # Scan from y_start to y_end to find forest entry point, then step back by buffer_m
        y_limit = y_end
        for y in range(int(y_start), int(y_end) + 1):
            if is_in_forest(x, float(y), buffer_m=buffer_m):
                y_limit = float(y) - buffer_m
                break
        return y_limit

    # Pools
    nodes = {} # (x, y) -> node_id
    node_coords = {} # node_id -> (lat, lon)
    next_node_id = 1

    def get_node(x, y):
        nonlocal next_node_id
        key = (round(x, 3), round(y, 3))
        if key not in nodes:
            lat, lon = local_to_global(x, y)
            nodes[key] = next_node_id
            node_coords[next_node_id] = (lat, lon)
            next_node_id += 1
        return nodes[key]

    ways = [] # list of dicts: {'id': id, 'node_refs': [...], 'tags': {...}}
    next_way_id = 1

    def add_way(coords, tags):
        nonlocal next_way_id
        node_refs = [get_node(x, y) for x, y in coords]
        ways.append({
            'id': next_way_id,
            'node_refs': node_refs,
            'coords': [(float(x), float(y)) for x, y in coords], # local metres, used by the infill pass
            'tags': tags
        })
        next_way_id += 1

    # 1. Bounding box calculations
    minlat, minlon = local_to_global(0, 8192) # South-West
    maxlat, maxlon = local_to_global(8192, 0) # North-East

    # 2. Yard 7 (SE Farmyard in DEM)
    # X: 15 to 1015, Y: 7677 to 8177
    yard7_coords = [
        (15.0, 7677.0),
        (1015.0, 7677.0),
        (1015.0, 8177.0),
        (15.0, 8177.0),
        (15.0, 7677.0) # Closed
    ]
    add_way(yard7_coords, {'landuse': 'farmyard', 'name': 'Yard 7'})

    # 3. Yard Town (Town Farmyard)
    # X: 7457 to 7557, Y: 1000 to 1500
    yard_town_coords = [
        (7457.0, 1000.0),
        (7557.0, 1000.0),
        (7557.0, 1500.0),
        (7457.0, 1500.0),
        (7457.0, 1000.0) # Closed
    ]
    add_way(yard_town_coords, {'landuse': 'farmyard', 'name': 'Town Farmyard'})

    # 4. Town Reservoir / East Lake
    # X: 7577 to 8177, Y: 1000 to 2200
    lake_coords = [
        (7577.0, 1000.0),
        (8177.0, 1000.0),
        (8177.0, 2200.0),
        (7577.0, 2200.0),
        (7577.0, 1000.0) # Closed
    ]
    add_way(lake_coords, {'natural': 'water', 'name': 'Town Reservoir'})

    # 5. East Canal (Canal)
    # Removed in the new DEM (subsumed by the lake)

    # 6. Town
    # X: 7037 to 7437, Y: 1000 to 2200 - extended south level with the Town
    # Reservoir, whose southern shore sits at y=2200.
    town_coords = [
        (7037.0, 1000.0),
        (7437.0, 1000.0),
        (7437.0, 2200.0),
        (7037.0, 2200.0),
        (7037.0, 1000.0) # Closed
    ]
    add_way(town_coords, {'landuse': 'farmyard', 'name': 'Town'})

    # 7. Primary Road (East-West)
    # Passing 15m north of Town (Y: 1000 - 15 = 985)
    # Spans from X: 0 to X: 8192.
    xs_sec = [7037.0, 7237.0, 7437.0, 7457.0, 7557.0]
    xs_ter_v = [800.0, 1600.0, 2400.0, 3200.0, 4000.0, 4800.0, 5600.0, 6400.0]
    # 7022 is where the Mountain Pass Road (7c) leaves southwards; putting it in
    # the list gives both primaries a shared junction node.
    xs_primary_all = sorted(xs_ter_v + xs_sec + [7022.0])
    primary_coords = [(0.0, 985.0)] + [(x, 985.0) for x in xs_primary_all] + [(8192.0, 985.0)]
    add_way(primary_coords, {'highway': 'primary', 'name': 'Primary Road'})

    # 7b. Southern Primary Road (straight East-West)
    # Runs the full width of the map at Y = 7650 and no longer bends north, so it
    # never joins the northern primary road.
    new_primary_coords_base = [(0.0, 7650.0), (8192.0, 7650.0)]

    # The road is a straight horizontal line, so its Y is constant everywhere.
    def get_road_y(x):
        return 7650.0

    # Collect and insert road-grid intersection nodes: only the vertical tertiary
    # roads meet it now, each one straight down on the road line.
    ys_ter_h = [1809.0, 2609.0, 3409.0, 4209.0, 5009.0, 5809.0, 6609.0, 7409.0]
    # (2450, 7650) is where the Mountain Pass Road (7c) comes in from the north.
    intersections = [(x, 7650.0) for x in xs_ter_v] + [(2450.0, 7650.0)]

    # The forest haul road used to leave from the removed vertical run of this road.
    # Kept only for the (disabled) forestry-track code in section 12; it no longer
    # sits on any road.
    forest_access_pt = (7022.0, 3200.0)

    all_road_nodes = sorted(set(new_primary_coords_base + intersections),
                            key=lambda pt: pt[0])
    add_way(all_road_nodes, {'highway': 'primary', 'name': 'Southern Link Road'})

    # 7c. Mountain Pass Road (North-South primary link)
    # Joins the two east-west primaries. It drops straight south past the town
    # along the PLSS eastern boundary (x=7022, where fields already stop at
    # 7017), crosses the flats diagonally along the NW flank of the eastern
    # mountain, threads the saddle between the two forest massifs, and runs the
    # last stretch straight south beside Cell Forest 1 (the wooded PLSS cell
    # x[1600-2400] y[7409-7650]) into the Southern Link Road. The bends were
    # fitted against the forest mask; minimum clearance to any wood is ~70 m.
    MOUNTAIN_PASS_CTRL = [
        (7022.0, 985.0),    # shared junction node on the Primary Road
        (7022.0, 1809.0),   # shared node with the first PLSS section-line road
        (7022.0, 2000.0),   # end of the straight run beside the town
        (5700.0, 2500.0),   # heading SW across the northern flats
        (5150.0, 4250.0),   # along the NW flank of the eastern mountain
        (4960.0, 4620.0),   # pass entry
        (4650.0, 4760.0),   # saddle between the two forests
        (4250.0, 4850.0),   # pass exit
        (2450.0, 6900.0),   # SW descent across the fields
        (2450.0, 7650.0),   # shared junction node on the Southern Link Road
    ]

    # Open curves instead of sharp corners: every bend is swept with a
    # tangent-continuous arc (a quadratic Bezier whose control point is the
    # original corner, so it leaves and enters the straights along their own
    # directions). Collinear vertices - the shared node at y=1809 - have no
    # turn and pass through exactly, keeping their junction coordinate.
    MOUNTAIN_PASS_BEND_R = 200.0    # target curve radius at each bend
    MOUNTAIN_PASS_ARC_STEP = 25.0   # sampling distance along each arc

    def fillet_polyline(pts, radius, step):
        lens, dirs, phis = [], [], [0.0] * len(pts)
        for a, b in zip(pts[:-1], pts[1:]):
            L = math.dist(a, b)
            lens.append(L)
            dirs.append(((b[0] - a[0]) / L, (b[1] - a[1]) / L))
        t_fil = [0.0] * len(pts)
        for k in range(1, len(pts) - 1):
            dot = max(-1.0, min(1.0,
                      dirs[k-1][0] * dirs[k][0] + dirs[k-1][1] * dirs[k][1]))
            phis[k] = math.acos(dot)
            if phis[k] >= math.radians(2.0):
                t_fil[k] = radius * math.tan(phis[k] / 2.0)
        # The two arcs eating into a leg from either end must leave some of the
        # straight in between; scale them back where a leg is too short.
        for k, L in enumerate(lens):
            used = t_fil[k] + t_fil[k+1]
            if used > 0.9 * L:
                sc = 0.9 * L / used
                t_fil[k] *= sc
                t_fil[k+1] *= sc
        out = [pts[0]]
        for k in range(1, len(pts) - 1):
            if t_fil[k] <= 0.0:
                out.append(pts[k])
                continue
            t = t_fil[k]
            p = pts[k]
            a = (p[0] - dirs[k-1][0] * t, p[1] - dirs[k-1][1] * t)
            b = (p[0] + dirs[k][0] * t, p[1] + dirs[k][1] * t)
            arc_len = (t / math.tan(phis[k] / 2.0)) * phis[k]
            n_s = max(2, int(math.ceil(arc_len / step)))
            for i in range(n_s + 1):
                u = i / n_s
                out.append(((1-u)*(1-u) * a[0] + 2*u*(1-u) * p[0] + u*u * b[0],
                            (1-u)*(1-u) * a[1] + 2*u*(1-u) * p[1] + u*u * b[1]))
        out.append(pts[-1])
        return out

    MOUNTAIN_PASS_PTS = fillet_polyline(MOUNTAIN_PASS_CTRL, MOUNTAIN_PASS_BEND_R,
                                        MOUNTAIN_PASS_ARC_STEP)
    add_way(MOUNTAIN_PASS_PTS, {'highway': 'primary', 'name': 'Mountain Pass Road'})
    mountain_pass_way = ways[-1]

    # The polyline sampled every ~10 m, for the box tests below (random-forest
    # placement and the field-cutting pass both need to know what the road runs
    # through, and a sample landing in a box is cheaper and more robust than
    # segment-rectangle intersection).
    mountain_pass_samples = []
    for _a, _b in zip(MOUNTAIN_PASS_PTS[:-1], MOUNTAIN_PASS_PTS[1:]):
        _ns = max(1, int(math.dist(_a, _b) / 10.0))
        for _i in range(_ns):
            _t = _i / _ns
            mountain_pass_samples.append((_a[0] + _t * (_b[0] - _a[0]),
                                          _a[1] + _t * (_b[1] - _a[1])))
    mountain_pass_samples.append(MOUNTAIN_PASS_PTS[-1])

    # 8. Railway
    # Passing 15m north of Primary Road (Y: 985 - 15 = 970)
    # Parallel to Primary Road
    # rail_coords = [(0.0, 970.0), (8192.0, 970.0)]
    # add_way(rail_coords, {'railway': 'rail', 'name': 'Railway'})

    # 9. Secondary Roads (Grid in Town)
    # The town proper (x 7037-7437) runs down to y=2200 since its extension to
    # the lake's southern shore; the Town Farmyard streets (x 7457/7557) still
    # stop at 1500.
    ys_sec_v = [985.0, 1000.0, 1250.0, 1500.0]
    ys_sec_v_town = ys_sec_v + [1750.0, 2000.0, 2200.0]
    for x in xs_sec:
        v_coords = [(x, y) for y in (ys_sec_v_town if x <= 7437.0 else ys_sec_v)]
        add_way(v_coords, {'highway': 'secondary'})

    # The horizontal streets reach x=7022 again: the Mountain Pass Road (7c)
    # runs straight down that line as far as y~1860, which gives the grid its
    # junctions with the primary network back (section 13 splices the shared
    # nodes). The streets south of where the road curves away start at the town
    # itself, and the Town Farmyard rows keep their old x extent.
    xs_sec_h = xs_sec
    ys_sec_h = [1000.0, 1250.0, 1500.0]
    for y in ys_sec_h:
        h_coords = [(7022.0, y)] + [(x, y) for x in xs_sec_h]
        add_way(h_coords, {'highway': 'secondary'})
    xs_sec_town = [7037.0, 7237.0, 7437.0]
    for y in [1750.0, 2000.0, 2200.0]:
        h_coords = ([(7022.0, y)] if y < 1860.0 else []) \
            + [(x, y) for x in xs_sec_town]
        add_way(h_coords, {'highway': 'secondary'})

    # The PLSS grid. Defined here rather than in section 9c, where the cells are filled,
    # because section 9b below has to know which of its roads a merged cell swallows.
    xs_grid_lines = [0.0] + xs_ter_v + [7022.0]
    ys_grid_lines = [985.0] + ys_ter_h + [7650.0]

    # What goes in each cell is decided first, then the roads that have to respect it are
    # drawn (9b), and only then are the cells filled in (9c).

    # 1. Select 10 random cells to place forests
    import random
    candidates = []
    clear_cells = set()
    for i in range(len(xs_grid_lines) - 1):
        x_a = xs_grid_lines[i]
        x_b = xs_grid_lines[i+1]
        for j in range(len(ys_grid_lines) - 1):
            y_a = ys_grid_lines[j]
            y_b = ys_grid_lines[j+1]

            # Forest box size (10 hectares = 316.227m x 316.227m)
            forest_w = 316.227
            x_f_start = x_a + 5.0
            x_f_end = x_f_start + forest_w
            y_f_start = y_a + 5.0
            y_f_end = y_f_start + forest_w

            if x_f_end > 7017.0:
                continue

            # Check road clearance
            road_clear = True
            for x in np.linspace(x_f_start, x_f_end, 5):
                if y_f_end > get_road_y(x) - 5.0:
                    road_clear = False
                    break
            if not road_clear:
                continue

            candidates.append((i, j))

            # Check forest clearance (don't overlay on the existing mountain/hill forests).
            # Recorded rather than filtered here: dropping cells before the shuffle would
            # reshuffle every draw and move all ten forests, so the check is applied to the
            # shuffled order instead and only the offending cells are skipped.
            corners = [
                (x_f_start, y_f_start),
                (x_f_end, y_f_start),
                (x_f_end, y_f_end),
                (x_f_start, y_f_end)
            ]
            # ...and clear of the Mountain Pass Road too: a box the corridor
            # runs through cannot hold a forest, so the road never gets walled
            # in by one of the random woods.
            ROAD_BOX_CLEAR_M = 25.0
            near_road = any(
                x_f_start - ROAD_BOX_CLEAR_M <= sx <= x_f_end + ROAD_BOX_CLEAR_M
                and y_f_start - ROAD_BOX_CLEAR_M <= sy <= y_f_end + ROAD_BOX_CLEAR_M
                for sx, sy in mountain_pass_samples)
            if (not near_road
                    and not any(is_in_forest(cx, cy, buffer_m=5.0) for cx, cy in corners)):
                clear_cells.add((i, j))

    print(f"   Found {len(candidates)} candidate cells for 10-hectare random forests "
          f"({len(clear_cells)} clear of the mountain forests).")
    # Deterministic seeded selection
    rng = random.Random(42)
    candidates.sort()
    rng.shuffle(candidates)
    selected_cells = set([c for c in candidates if c in clear_cells][:10])

    # PLSS cells (x-index, y-index into xs_grid_lines / ys_grid_lines) that are
    # forested instead of farmed. (2, 8) is the strip x[1600-2400] y[7409-7650],
    # wedged between the last tertiary road and the Southern Link Road, facing the
    # south-western forest across the road.
    wooded_cells = {(2, 8)}

    # PLSS cells kept as yard rather than farmland. (5, 8) is x[4000-4800] y[7409-7650],
    # the 18.2 ha strip against the Southern Link Road that used to be Field 61.
    yard_cells = {(5, 8)}

    # PLSS cells merged into one field, as (column, first row, last row). Only cells whose
    # field fills the whole cell qualify - one clipped by the forest or by the Southern
    # Link Road, or one holding a random forest, is left alone - so no merged field ever
    # runs into a wood. The tertiary road along each interior boundary is cut to match.
    plss_merges = [(0, 2, 3), (1, 0, 1), (2, 4, 5), (3, 6, 7), (4, 2, 3),
                   (5, 4, 5), (6, 0, 1), (7, 3, 4), (8, 1, 2)]

    merge_of = {}       # (i, j) -> (i, j0, j1) for every cell a merge covers
    road_gaps = {}      # y of a horizontal road -> [(x from, x to)] it must not span
    for i, j0, j1 in plss_merges:
        cells = [(i, j) for j in range(j0, j1 + 1)]
        # A merge is only sound over cells that hold one plain full-cell field. Checked
        # rather than assumed, because a different seed or a different DEM can turn one of
        # them into a random forest and the merge would then straddle it unnoticed.
        taken = [c for c in cells if c in selected_cells or c in wooded_cells
                 or c in yard_cells]
        if taken:
            print(f"   WARNING: PLSS merge {(i, j0, j1)} skipped, cells {taken} are not "
                  f"plain fields.")
            continue
        for c in cells:
            merge_of[c] = (i, j0, j1)
        for jb in range(j0 + 1, j1 + 1):
            road_gaps.setdefault(ys_grid_lines[jb], []).append(
                (xs_grid_lines[i], xs_grid_lines[i+1]))

    def split_at_gaps(pts, gaps, axis):
        # Emit the polyline in pieces, dropping the stretches the gaps cover. The boundary
        # points stay on both sides, so each piece still ends on the junction it shares
        # with the crossing road and the network stays connected.
        pieces = [[]]
        for k, pt in enumerate(pts):
            pieces[-1].append(pt)
            if k + 1 < len(pts):
                lo, hi = sorted((pt[axis], pts[k+1][axis]))
                if any(g0 - 1e-6 <= lo and hi <= g1 + 1e-6 for g0, g1 in gaps):
                    pieces.append([])
        return [p for p in pieces if len(p) >= 2]

    # 9b. Tertiary Roads (PLSS Dirt Grid) - Stopping before entering forests
    # Vertical tertiary roads
    for x in xs_ter_v:
        y_int = get_road_y(x)
        v_pts = [(x, 985.0)]
        for y in ys_ter_h:
            if y < y_int - 0.1:
                if is_in_forest(x, y, buffer_m=5.0):
                    break
                v_pts.append((x, y))
        
        if not is_in_forest(x, y_int, buffer_m=5.0):
            v_pts.append((x, y_int))
        else:
            y_forest_limit = get_forest_limit_y(x, 985.0, y_int, buffer_m=5.0)
            if y_forest_limit > v_pts[-1][1] + 1.0:
                v_pts.append((x, y_forest_limit))
                
        v_pts.sort(key=lambda pt: pt[1])
        add_way(v_pts, {'highway': 'tertiary'})

    # Horizontal tertiary roads
    for y in ys_ter_h:
        # With the primary road straight along the south, the grid's eastern edge
        # is the fixed PLSS boundary at x=7022 instead of the road's old vertical run.
        x_int = 7022.0
        h_pts = [(0.0, y)]
        for x in xs_ter_v:
            if x < x_int - 0.1:
                if is_in_forest(x, y, buffer_m=5.0):
                    break
                h_pts.append((x, y))
                
        if not is_in_forest(x_int, y, buffer_m=5.0):
            h_pts.append((x_int, y))
        else:
            x_forest_limit = x_int
            for x_scan in range(0, int(x_int) + 1):
                if is_in_forest(float(x_scan), y, buffer_m=5.0):
                    x_forest_limit = float(x_scan) - 5.0
                    break
            if x_forest_limit > h_pts[-1][0] + 1.0:
                h_pts.append((x_forest_limit, y))
                
        h_pts.sort(key=lambda pt: pt[0])
        # A vertically merged cell leaves this road running through the middle of the
        # field, so the stretch it covers is left out.
        for piece in split_at_gaps(h_pts, road_gaps.get(y, []), 0):
            add_way(piece, {'highway': 'tertiary'})

    # 9c. PLSS Farmlands & Random Forests
    field_idx = 1
    forest_idx = 1
    cell_forest_idx = 1

    print("   Generating PLSS farmlands (fields) and 10 random forests...")
    for i in range(len(xs_grid_lines) - 1):
        x_a = xs_grid_lines[i]
        x_b = xs_grid_lines[i+1]
        for j in range(len(ys_grid_lines) - 1):
            y_a = ys_grid_lines[j]
            y_b = ys_grid_lines[j+1]

            # A merged cell is emitted once, at its top row, as a field reaching down to
            # the bottom of the last row of the merge. The 10 m road corridors in between
            # become farmland, and 9b already left those stretches of road out.
            mrg = merge_of.get((i, j))
            if mrg:
                if j != mrg[1]:
                    continue
                y_b = ys_grid_lines[mrg[2] + 1]

            if (i, j) in selected_cells:
                # Add 10-hectare forest at the top-left of the cell
                forest_w = 316.227
                x_f_start = x_a + 5.0
                x_f_end = x_f_start + forest_w
                y_f_start = y_a + 5.0
                y_f_end = y_f_start + forest_w
                
                forest_coords = [
                    (x_f_start, y_f_start),
                    (x_f_end, y_f_start),
                    (x_f_end, y_f_end),
                    (x_f_start, y_f_end),
                    (x_f_start, y_f_start)
                ]
                add_way(forest_coords, {
                    'natural': 'wood',
                    'landuse': 'farmyard',
                    'leaf_type': 'broadleave',
                    'name': f'Random Forest {forest_idx}'
                })
                forest_idx += 1
                
                # Split remaining cell area into two fields: Right and Bottom
                # 1. Right Field
                xs_sample = np.linspace(x_f_end + 10.0, x_b - 5.0, 5)
                poly_top = []
                poly_bottom = []
                valid = True
                for x in xs_sample:
                    y_t = y_a + 5.0
                    y_b_limit = min(y_f_end, get_road_y(x) - 5.0)
                    y_b_limit = get_forest_limit_y(x, y_t, y_b_limit, buffer_m=5.0)
                    if x < 5.0 or x > 7017.0:
                        valid = False
                        break
                    if y_t + 15.0 > y_b_limit:
                        valid = False
                        break
                    poly_top.append((x, y_t))
                    poly_bottom.append((x, y_b_limit))
                if valid:
                    coords = poly_top + list(reversed(poly_bottom)) + [poly_top[0]]
                    add_way(coords, {'landuse': 'farmland', 'name': f'Field {field_idx}'})
                    field_idx += 1
                    
                # 2. Bottom Field
                xs_sample = np.linspace(x_a + 5.0, x_b - 5.0, 5)
                poly_top = []
                poly_bottom = []
                valid = True
                for x in xs_sample:
                    y_t = y_f_end + 10.0
                    y_b_limit = min(y_b - 5.0, get_road_y(x) - 5.0)
                    y_b_limit = get_forest_limit_y(x, y_t, y_b_limit, buffer_m=5.0)
                    if x < 5.0 or x > 7017.0:
                        valid = False
                        break
                    if y_t + 15.0 > y_b_limit:
                        valid = False
                        break
                    poly_top.append((x, y_t))
                    poly_bottom.append((x, y_b_limit))
                if valid:
                    coords = poly_top + list(reversed(poly_bottom)) + [poly_top[0]]
                    add_way(coords, {'landuse': 'farmland', 'name': f'Field {field_idx}'})
                    field_idx += 1
            else:
                # Normal full field in cell
                xs_sample = np.linspace(x_a + 5.0, x_b - 5.0, 5)
                poly_top = []
                poly_bottom = []
                valid = True
                for x in xs_sample:
                    y_t = y_a + 5.0
                    y_b_limit = min(y_b - 5.0, get_road_y(x) - 5.0)
                    y_b_limit = get_forest_limit_y(x, y_t, y_b_limit, buffer_m=5.0)
                    if x < 5.0 or x > 7017.0:
                        valid = False
                        break
                    if y_t < 990.0:
                        y_t = 990.0
                    if is_in_forest(x, y_t, buffer_m=5.0):
                        valid = False
                        break
                    if y_t + 15.0 > y_b_limit:
                        valid = False
                        break
                    poly_top.append((x, y_t))
                    poly_bottom.append((x, y_b_limit))
                if valid:
                    coords = poly_top + list(reversed(poly_bottom)) + [poly_top[0]]
                    if (i, j) in wooded_cells:
                        # No field here: the cell is given over to the forest that
                        # already borders it on the far side of the Southern Link Road.
                        add_way(coords, {
                            'natural': 'wood',
                            'landuse': 'farmyard',
                            'leaf_type': 'needleleave',
                            'name': f'Cell Forest {cell_forest_idx}'
                        })
                        cell_forest_idx += 1
                    else:
                        # Yard cells still consume a field number, so converting one
                        # does not renumber every field that comes after it.
                        if (i, j) in yard_cells:
                            add_way(coords, {'landuse': 'farmyard', 'name': f'Yard {field_idx}'})
                        else:
                            add_way(coords, {'landuse': 'farmland', 'name': f'Field {field_idx}'})
                        field_idx += 1
                elif mrg:
                    # Losing a merged field costs every cell it covered, not just one, so
                    # it is worth saying out loud rather than quietly leaving a hole.
                    print(f"   WARNING: merged PLSS field {mrg} came out invalid; "
                          f"{mrg[2] - mrg[1] + 1} cells left empty.")

    print(f"   Added {field_idx - 1} PLSS fields, {forest_idx - 1} random forests "
          f"and {cell_forest_idx - 1} wooded cells.")

    # 9d. Northern Farmlands & Tertiary Roads (Horizontal layout, 15m borders)
    # Adjacent columns are merged until a parcel reaches this size, so a strip ends up
    # with a handful of large fields instead of a long run of small ones. The 10 m gap
    # inside a merged parcel becomes farmland and the road that sat in it goes away.
    NORTH_MIN_PARCEL_HA = 22.0

    # ...except at the eastern end of every strip, which faces the town (x 7118-8142)
    # across the railway. That end is packed with fixed-size columns instead of the random
    # mix, and they are exempt from the merge, so the map keeps small parcels for small
    # machinery near the town: four 5 ha smallholdings against the eastern edge, then two
    # 10 ha columns behind them. Listed from the eastern edge westwards, and laid out
    # backwards from x=8177, so the block finishes flush with the edge and no column ends
    # up as a runt.
    NORTH_FIXED_BANDS = [(5.0, 4), (10.0, 2)]  # (hectares, number of columns)

    def pack_strip_horiz(y_start, y_end, seed):
        # Returns the raw column boundaries as (x_start, x_end, is_fixed). Sizes are
        # recomputed from the geometry after the merge pass, because merging absorbs
        # the gaps in between, and the roads are derived from the surviving parcels.
        import random
        rng = random.Random(seed)
        fields = []

        x_curr = 15.0
        field_h = y_end - y_start

        # Place the fixed bands first, walking west from the eastern edge.
        bands = []
        x_edge = 8177.0
        for band_ha, count in NORTH_FIXED_BANDS:
            w = (band_ha * 10000.0) / field_h
            x_band = x_edge - (count * w + (count - 1) * 10.0)
            bands.append((x_band, w, count))
            x_edge = x_band - 10.0
        x_fixed = x_edge + 10.0     # western edge of the whole fixed block

        # We select sizes from [5, 10, 20]
        # To have a good mix, we weight them: 5 ha (weight 2), 10 ha (weight 2), 20 ha (weight 1)
        choices = [5, 5, 10, 10, 20]

        while True:
            size = rng.choice(choices)
            w = (size * 10000.0) / field_h

            # Check if this field fits (needs at least w + 10m before the fixed block)
            if x_curr + w + 10.0 > x_fixed - 10.0:
                # Last field of the mix: adjust to fill the space up to the fixed block
                if x_fixed - 10.0 - x_curr >= 50.0:
                    fields.append((x_curr, x_fixed - 10.0, False))
                break

            fields.append((x_curr, x_curr + w, False))
            x_curr += w + 10.0

        # The bands were built east to west, so they go back in reversed to keep the
        # column list running west to east like the rest of the strip.
        for x_band, w, count in reversed(bands):
            for k in range(count):
                x_s = x_band + k * (w + 10.0)
                fields.append((x_s, x_s + w, True))

        return fields

    def merge_strip_fields(fields, field_h, s_idx):
        def ha(x_s, x_e):
            return (x_e - x_s) * field_h / 10000.0

        spans = [(a, b) for sn, a, b in north_yards if sn == s_idx + 1]

        def kind_of(x_s, x_e, is_fixed):
            if any(x_s <= b and a <= x_e for a, b in spans):
                return 'Y'
            return 'S' if is_fixed else 'F'

        merged = []  # (x_start, x_end, kind), kind: 'F' field, 'Y' yard, 'S' fixed column
        run = None   # [x_start, x_end, kind]

        def close():
            # A field run cut short - by a yard, by the smallholdings, or by the end of
            # the strip - is folded into the field before it rather than surviving as an
            # undersized parcel of its own. Only a plain field can absorb it: doing it
            # to a yard or a fixed column would defeat the point of having one.
            nonlocal run
            if not run:
                return
            if (run[2] == 'F' and ha(run[0], run[1]) < NORTH_MIN_PARCEL_HA
                    and merged and merged[-1][2] == 'F'):
                merged[-1] = (merged[-1][0], run[1], 'F')
            else:
                merged.append((run[0], run[1], run[2]))
            run = None

        for x_s, x_e, is_fixed in fields:
            kind = kind_of(x_s, x_e, is_fixed)
            # A yard grows over as many columns as its span covers, a plain field over
            # as many as it needs to reach the target size, and a fixed column over none
            # at all - keeping its declared size is the whole point of it.
            if run and (run[2] != kind or kind == 'S'):
                close()
            run = [run[0] if run else x_s, x_e, kind]
            if kind == 'F' and ha(run[0], run[1]) >= NORTH_MIN_PARCEL_HA:
                merged.append((run[0], run[1], 'F'))
                run = None
        close()
        return merged

    print("   Generating Northern Farmlands (Horizontal standard, 15m borders)...")

    # Northern parcels kept as yard, not farmland, given as (strip number, X from, X to):
    # every column of that strip the span touches joins the yard, so widening a yard is
    # a matter of widening its span. Spans rather than column indices, because the
    # indices shift whenever the packing changes. (5, 15, 20) is the western corner;
    # (5, 7400, 7700) faces the town and covers two of the 5 ha columns.
    north_yards = {(5, 15.0, 20.0), (5, 7400.0, 7700.0)}

    # Define 5 strips of height 180m
    strips = [
        (15.0, 195.0),
        (205.0, 385.0),
        (395.0, 575.0),
        (585.0, 765.0),
        (775.0, 955.0)
    ]
    
    # Add horizontal boundary roads & horizontal roads in the gaps
    # Boundary North
    add_way([(15.0, 15.0), (8177.0, 15.0)], {'highway': 'tertiary'})
    # Gaps horizontal roads
    add_way([(0.0, 200.0), (8192.0, 200.0)], {'highway': 'tertiary'})
    add_way([(0.0, 390.0), (8192.0, 390.0)], {'highway': 'tertiary'})
    add_way([(0.0, 580.0), (8192.0, 580.0)], {'highway': 'tertiary'})
    add_way([(0.0, 770.0), (8192.0, 770.0)], {'highway': 'tertiary'})
    
    # Boundary West (15m offset)
    add_way([(15.0, 15.0), (15.0, 985.0)], {'highway': 'tertiary'})
    # Boundary East (15m offset, which is 8177.0)
    add_way([(8177.0, 15.0), (8177.0, 985.0)], {'highway': 'tertiary'})

    # Generate fields & vertical roads for each strip
    for s_idx, (y_s, y_e) in enumerate(strips):
        field_h = y_e - y_s
        parcels = merge_strip_fields(pack_strip_horiz(y_s, y_e, seed=(303 + s_idx)),
                                     field_h, s_idx)

        # Add fields
        for p_idx, (x_start, x_end, kind) in enumerate(parcels, 1):
            coords = [
                (x_start, y_s),
                (x_end, y_s),
                (x_end, y_e),
                (x_start, y_e),
                (x_start, y_s)
            ]
            label = f'N{s_idx+1}_{p_idx}'
            if kind == 'Y':
                add_way(coords, {'landuse': 'farmyard', 'name': f'Yard {label}'})
            else:
                size = (x_end - x_start) * field_h / 10000.0
                add_way(coords, {'landuse': 'farmland', 'name': f'Field {label} ({size:.1f} ha)'})

        # Only the gaps that still separate two parcels keep their road; the ones
        # swallowed by a merge go with it.
        roads = [x_end + 5.0 for x_start, x_end, _ in parcels[:-1]]

        # Add vertical roads in gaps connecting adjacent horizontal roads
        for rx in roads:
            if s_idx == 0:
                # North border to Gap 1
                add_way([(rx, 15.0), (rx, 200.0)], {'highway': 'tertiary'})
            elif s_idx == 1:
                # Gap 1 to Gap 2
                add_way([(rx, 200.0), (rx, 390.0)], {'highway': 'tertiary'})
            elif s_idx == 2:
                # Gap 2 to Gap 3
                add_way([(rx, 390.0), (rx, 580.0)], {'highway': 'tertiary'})
            elif s_idx == 3:
                # Gap 3 to Gap 4
                add_way([(rx, 580.0), (rx, 770.0)], {'highway': 'tertiary'})
            elif s_idx == 4:
                # Gap 4 to primary road (Y = 985)
                add_way([(rx, 770.0), (rx, 985.0)], {'highway': 'tertiary'})

    # 9e. Eastern Farmlands & Tertiary Roads (Vertical layout, 15m borders)
    print("   Generating Eastern Farmlands (Vertical 30 and 45 hectares) and tertiary roads...")
    
    col_w = 373.3
    col_xs = [
        (7037.0, 7410.3),
        (7420.3, 7793.6),
        (7803.6, 8177.0)
    ]
    vertical_gaps = [7415.3, 7798.6]
    
    # Pack fields for each column, keeping track of last horizontal road Y
    import random
    rng = random.Random(404)
    # Every column now starts below y=2200: columns 2-3 because they overlap the
    # Town Reservoir (x >= 7577, down to y=2200), column 1 because the extended
    # town itself reaches that line.
    col_last_ys = [2200.0, 2200.0, 2200.0]
    col_has_fields = [False, False, False]

    for c_idx, (x_s, x_e) in enumerate(col_xs):
        y_curr = 2215.0
        choices = [30, 30, 45]
        f_idx = 1
        
        # Determine column road boundaries
        x_road_start = 7037.0 if c_idx == 0 else vertical_gaps[c_idx - 1]
        x_road_end = 8177.0 if c_idx == 2 else vertical_gaps[c_idx]
        
        while True:
            # Get forest limit Y for this column width
            col_y_limit = min(
                min(get_road_y(x) - 15.0, get_forest_limit_y(x, y_curr, 8192.0, buffer_m=15.0))
                for x in np.linspace(x_s, x_e, 5)
            )
            
            size = rng.choice(choices)
            h = (size * 10000.0) / col_w
            
            if y_curr + h + 10.0 > col_y_limit:
                # Discard the last field to avoid broken shapes bordering the forest
                break
                
            # Place field
            coords = [
                (x_s, y_curr),
                (x_e, y_curr),
                (x_e, y_curr + h),
                (x_s, y_curr + h),
                (x_s, y_curr)
            ]
            add_way(coords, {'landuse': 'farmland', 'name': f'Field E{c_idx+1}_{f_idx} ({size:.1f} ha)'})
            col_has_fields[c_idx] = True
            
            # Add horizontal road in the gap (restricted strictly to the column width)
            y_road = y_curr + h + 5.0
            add_way([(x_road_start, y_road), (x_road_end, y_road)], {'highway': 'tertiary'})
            
            col_last_ys[c_idx] = y_road
            
            y_curr += h + 10.0
            f_idx += 1

    # Add vertical roads in the column gaps, trimmed to the last horizontal road of the adjacent columns
    for g_idx, rx in enumerate(vertical_gaps):
        if col_has_fields[g_idx] or col_has_fields[g_idx + 1]:
            # Gap road goes down to the maximum of the last road in its left and right columns
            y_lim = max(col_last_ys[g_idx], col_last_ys[g_idx + 1])
            y_start = min(col_last_ys[g_idx], col_last_ys[g_idx + 1])
            add_way([(rx, y_start), (rx, y_lim)], {'highway': 'tertiary'})
        
    # Boundary East road extension down to the last road in column 2
    if col_has_fields[2]:
        y_lim_east = col_last_ys[2]
        add_way([(8177.0, 2200.0), (8177.0, y_lim_east)], {'highway': 'tertiary'})

    # 9f. Southern Farmlands & Tertiary Roads (Horizontal layout, 15m borders)
    print("   Generating Southern Farmlands (Horizontal 10 hectares) and tertiary roads...")
    
    # Unlike the northern and eastern packers, this one draws no random sizes: every
    # column is a fixed 10 ha, so there is no seed to pass in.
    def pack_south_pocket(x_start, x_end, p_name):
        y_s1, y_e1 = 7665.0, 7916.0
        y_s2, y_e2 = 7926.0, 8177.0
        
        field_h = 251.0
        w_10ha = 398.4
        
        # Add horizontal roads at Y = 7921.0 and Y = 8177.0
        add_way([(x_start, 7921.0), (x_end, 7921.0)], {'highway': 'tertiary'})
        add_way([(x_start, 8177.0), (x_end, 8177.0)], {'highway': 'tertiary'})
        
        # Add boundary vertical roads
        add_way([(x_start, 7650.0), (x_start, 8177.0)], {'highway': 'tertiary'})
        add_way([(x_end, 7650.0), (x_end, 8177.0)], {'highway': 'tertiary'})
        
        # The column boundaries are worked out before anything is emitted, because
        # the last one is not a plain 10 ha column: the leftover strip at the end of
        # the pocket is absorbed by it instead of being left as a runt parcel, and
        # the road that used to separate the two goes with it.
        cols = []
        x_curr = x_start
        while x_curr + w_10ha + 10.0 <= x_end:
            cols.append((x_curr, x_curr + w_10ha))
            x_curr += w_10ha + 10.0
        if cols:
            cols[-1] = (cols[-1][0], x_end)
        elif x_end - x_start >= 100.0:
            cols.append((x_start, x_end))

        for strip_idx, (y_s, y_e) in enumerate([(y_s1, y_e1), (y_s2, y_e2)]):
            for f_idx, (x_s, x_e) in enumerate(cols, 1):
                size = ((x_e - x_s) * field_h) / 10000.0
                coords = [
                    (x_s, y_s),
                    (x_e, y_s),
                    (x_e, y_e),
                    (x_s, y_e),
                    (x_s, y_s)
                ]
                add_way(coords, {
                    'landuse': 'farmland',
                    'name': f'Field S_{p_name}{strip_idx+1}_{f_idx} ({size:.1f} ha)'
                })

        # Vertical roads in the gaps between columns. Both strips share the same
        # column layout, so each road is emitted once and spans the whole pocket.
        for x_s, x_e in cols[:-1]:
            add_way([(x_e + 5.0, 7650.0), (x_e + 5.0, 8177.0)], {'highway': 'tertiary'})

    # Pocket A: between Yard 7 and bottom-left forest
    pack_south_pocket(1030.0, 1981.8, p_name="A")
    # Pocket B: between bottom-left forest and Southern Link Road curve 1
    pack_south_pocket(2675.4, 5053.0, p_name="B")

    # 10. Forest polygons (extracted and filtered in section 0; the eastern mass is
    # already dropped there).
    # The south-western forest has a box of its own: the gap the two southern pockets
    # leave between them (see pack_south_pocket above), bounded north by the straight run
    # of the Southern Link Road. The 55 m contour pokes a narrow tongue out of it to the
    # north, across the road and into the wooded PLSS cell behind, so that forest is
    # clipped back to the box. Its X already sits inside with the same 15 m clearance the
    # southern fields keep, so only the northern edge needs cutting.
    SOUTH_FOREST_STRAIGHT_X = 5068.0    # x limit that singles out the south-western forest
    SOUTH_FOREST_BOX = (1981.8 + 15.0, 7650.0 + 15.0, 2675.4 - 15.0)  # x from, y from, x to

    def clip_to_south_of(poly, y_min):
        # Sutherland-Hodgman against the single edge y >= y_min.
        ring = list(poly)
        while len(ring) > 1 and math.dist(ring[0], ring[-1]) < 1e-6:
            ring.pop()
        out = []
        for k, a in enumerate(ring):
            b = ring[(k + 1) % len(ring)]
            a_in, b_in = a[1] >= y_min, b[1] >= y_min
            if a_in:
                out.append((float(a[0]), float(a[1])))
            if a_in != b_in:
                t = (y_min - a[1]) / (b[1] - a[1])
                out.append((float(a[0] + t * (b[0] - a[0])), y_min))
        return out + [out[0]] if len(out) >= 3 else None

    print("   Generating forest areas kept from the DEM (elevation >= 55m)...")
    forest_polys = forest_polys_kept
    for i, poly in enumerate(forest_polys):
        # Everywhere else there is no clipping against the Southern Link Road: the 55m
        # contour is followed as-is, so the forest spills over to the North/West side
        # where the terrain actually rises. The roads and fields already keep clear of it
        # via is_in_forest(), and the infill pass below closes whatever is left over.
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x_from, y_from, x_to = SOUTH_FOREST_BOX
        if max(xs) < SOUTH_FOREST_STRAIGHT_X and max(ys) > y_from and min(ys) < y_from:
            clipped = clip_to_south_of(poly, y_from)
            if clipped is None:
                print(f"   WARNING: south-western forest emptied by the clip; left as-is.")
            else:
                if min(xs) < x_from - 0.1 or max(xs) > x_to + 0.1:
                    print(f"   WARNING: south-western forest spans x[{min(xs):.1f},"
                          f"{max(xs):.1f}], outside its box x[{x_from:.1f},{x_to:.1f}].")
                print(f"   Clipped the south-western forest to y >= {y_from:.0f}: "
                      f"{len(poly)} -> {len(clipped)} nodes.")
                poly = clipped
        add_way(list(poly), {
            'natural': 'wood',
            'landuse': 'farmyard',
            'leaf_type': 'needleleave'
        })
        print(f"   Added forest way {i+1} with {len(poly)} nodes.")

    # 10b. Cut the dirt roads out of the mountain forests. The PLSS roads only test
    # for forest at the 800 m grid crossings, so a band lying between two crossings
    # gets run straight through; here every tertiary way is resampled against the
    # forest mask, split at the forest edge, and the stretches inside the woods are
    # dropped along with orphan fragments.
    def cut_tertiary_out_of_forest():
        MIN_PIECE_M = 30.0
        SAMPLE_M = 2.0

        def inside(p):
            px = min(max(int(p[0] / FOREST_MASK_SCALE), 0), mask_n - 1)
            py = min(max(int(p[1] / FOREST_MASK_SCALE), 0), mask_n - 1)
            return bool(forest_mask[py, px])

        def length(pts):
            return sum(math.dist(pts[i], pts[i+1]) for i in range(len(pts) - 1))

        kept = []
        to_add = []
        n_cut = n_dropped = 0
        for w in ways:
            if w['tags'].get('highway') != 'tertiary':
                kept.append(w)
                continue
            pts = w['coords']
            pieces = []     # (coords, starts_at_cut, ends_at_cut)
            state = inside(pts[0])
            cur = None if state else [pts[0]]
            cur_from_cut = False
            prev = pts[0]
            for k in range(len(pts) - 1):
                a, b = pts[k], pts[k+1]
                n_s = max(1, int(math.ceil(math.dist(a, b) / SAMPLE_M)))
                for i2 in range(1, n_s + 1):
                    t = i2 / n_s
                    p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                    s = inside(p)
                    if s != state:
                        if s:
                            # Entering the forest: close at the last outside sample.
                            if not cur or cur[-1] != prev:
                                cur.append(prev)
                            pieces.append((cur, cur_from_cut, True))
                            cur = None
                        else:
                            cur = [p]
                            cur_from_cut = True
                        state = s
                    elif not s and i2 == n_s:
                        cur.append(p)
                    prev = p
            if cur is not None:
                pieces.append((cur, cur_from_cut, False))

            if len(pieces) == 1 and not pieces[0][1] and not pieces[0][2]:
                kept.append(w)      # untouched: keep the original way as-is
                continue
            n_cut += 1
            for pc, from_cut, to_cut in pieces:
                # A fragment floating between two forest walls, or too short to
                # matter, goes away with the forest stretch.
                if length(pc) < MIN_PIECE_M or (from_cut and to_cut):
                    n_dropped += 1
                    continue
                to_add.append((pc, dict(w['tags'])))

        # A piece that shares no node with any other road is a stranded stub: the
        # forest swallowed the stretch that used to tie it into the network, so it
        # now just runs across fields ending nowhere. Those go away too.
        def ckey(pt):
            return (round(pt[0], 3), round(pt[1], 3))
        coord_count = {}
        for coords in ([w['coords'] for w in kept
                        if 'highway' in w['tags'] or 'railway' in w['tags']]
                       + [pc for pc, _ in to_add]):
            for pt in coords:
                coord_count[ckey(pt)] = coord_count.get(ckey(pt), 0) + 1
        connected = []
        for pc, tags in to_add:
            if any(coord_count[ckey(pt)] >= 2 for pt in pc):
                connected.append((pc, tags))
            else:
                n_dropped += 1
        to_add = connected

        ways[:] = kept
        for pc, tags in to_add:
            add_way(pc, tags)
        print(f"   Cut {n_cut} tertiary roads at the forest edge "
              f"({len(to_add)} pieces kept, {n_dropped} fragments dropped).")

    cut_tertiary_out_of_forest()

    # Iterative Douglas-Peucker (the raster outlines are far too dense to keep).
    # Defined here because both the road cut below (10c) and the infill (11) use it.
    def simplify(points, tol):
        keep = np.zeros(len(points), dtype=bool)
        keep[0] = keep[-1] = True
        stack = [(0, len(points) - 1)]
        pts = np.asarray(points, dtype=float)
        while stack:
            i0, i1 = stack.pop()
            if i1 <= i0 + 1:
                continue
            a, b = pts[i0], pts[i1]
            seg = b - a
            seg_len = math.hypot(seg[0], seg[1])
            chunk = pts[i0+1:i1]
            if seg_len < 1e-9:
                d = np.hypot(chunk[:, 0] - a[0], chunk[:, 1] - a[1])
            else:
                d = np.abs(np.cross(seg, chunk - a)) / seg_len
            k = int(np.argmax(d))
            if d[k] > tol:
                k += i0 + 1
                keep[k] = True
                stack.append((i0, k))
                stack.append((k, i1))
        return [tuple(p) for p in pts[keep]]

    # 10c. Cut the fields along the Mountain Pass Road. The PLSS grid and the
    # packers laid their parcels out before the road existed, so every farmland
    # (or procedurally-placed wood) the corridor crosses is rasterised, the
    # corridor subtracted, and the surviving pieces re-emitted. Slivers - tiny
    # or long-and-thin leftovers pinched off against the roadside - vanish into
    # the verge instead of surviving as unworkable parcels.
    ROAD_CUT_HALF_M = 16.0       # half-width of the reserved corridor
    ROAD_CUT_MIN_HA = 1.0        # pieces smaller than this are dropped
    ROAD_CUT_MIN_WIDTH_M = 35.0  # ...as are pieces with a mean width below this

    def cut_fields_along_mountain_pass():
        s = 4.0
        gn = int(round(8192.0 / s))
        corr_img = Image.new('L', (gn, gn), 0)
        ImageDraw.Draw(corr_img).line(
            [(x / s, y / s) for x, y in MOUNTAIN_PASS_PTS],
            fill=255, width=max(1, int(round(2 * ROAD_CUT_HALF_M / s))), joint='curve')
        corridor = np.array(corr_img) > 0

        def pieces_of(poly):
            # None = the corridor never touches this polygon; otherwise the list
            # of surviving pieces as (outline, hectares).
            pimg = Image.new('L', (gn, gn), 0)
            ImageDraw.Draw(pimg).polygon([(x / s, y / s) for x, y in poly],
                                         fill=255, outline=255)
            pmask = np.array(pimg) > 0
            if not (pmask & corridor).any():
                return None
            lab, k = ndimage.label(pmask & ~corridor)
            out = []
            for lid in range(1, k + 1):
                comp = lab == lid
                area_ha = comp.sum() * s * s / 10000.0
                if area_ha < ROAD_CUT_MIN_HA:
                    continue
                padded = np.zeros((gn + 2, gn + 2), dtype=np.float32)
                padded[1:-1, 1:-1] = comp
                axis = (np.arange(gn + 2) - 0.5) * s
                gx, gy = np.meshgrid(axis, axis)
                fig, ax = plt.subplots()
                cs = ax.contour(gx, gy, padded, levels=[0.5])
                plt.close(fig)
                if not cs.allsegs[0]:
                    continue
                seg = max(cs.allsegs[0], key=len)
                pts = [(min(max(px, 0.0), 8192.0), min(max(py, 0.0), 8192.0))
                       for px, py in seg]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                pts = simplify(pts, 8.0)
                if len(pts) < 4:
                    continue
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                perim = sum(math.dist(pts[q], pts[q+1]) for q in range(len(pts) - 1))
                if perim > 0 and (2.0 * area_ha * 10000.0 / perim) < ROAD_CUT_MIN_WIDTH_M:
                    continue
                out.append((pts, area_ha))
            return out

        kept = []
        pieces_to_add = []
        n_cut = n_dropped = 0
        for w in ways:
            tags = w['tags']
            # Farmland, plus the named procedural woods (Random Forest / Cell
            # Forest boxes). The DEM forests, water and the hand-placed yards
            # are never crossed, and the roads must obviously survive.
            cuttable = (tags.get('landuse') == 'farmland'
                        or (tags.get('natural') == 'wood' and 'name' in tags))
            if not cuttable:
                kept.append(w)
                continue
            xs = [p[0] for p in w['coords']]
            ys = [p[1] for p in w['coords']]
            pad = ROAD_CUT_HALF_M + 5.0
            if not any(min(xs) - pad <= sx <= max(xs) + pad
                       and min(ys) - pad <= sy <= max(ys) + pad
                       for sx, sy in mountain_pass_samples):
                kept.append(w)
                continue
            res = pieces_of(w['coords'])
            if res is None:
                kept.append(w)
                continue
            n_cut += 1
            base = tags.get('name', '')
            had_ha = re.search(r' \(\d+(?:\.\d+)? ha\)$', base)
            if had_ha:
                base = base[:had_ha.start()]
            n_dropped += 1 if not res else 0
            for p_idx, (pts, area_ha) in enumerate(res):
                new_tags = dict(tags)
                if base:
                    name = base if len(res) == 1 else f"{base}{chr(ord('a') + p_idx)}"
                    if had_ha:
                        name += f" ({area_ha:.1f} ha)"
                    new_tags['name'] = name
                pieces_to_add.append((pts, new_tags))
        ways[:] = kept
        for pts, tags in pieces_to_add:
            add_way(pts, tags)
        print(f"   Mountain Pass Road cut {n_cut} parcels into "
              f"{len(pieces_to_add)} pieces ({n_dropped} vanished entirely).")

    cut_fields_along_mountain_pass()

    # 11. Forest infill: absorb the leftover open ground next to the forests.
    # Everything generated so far is rasterised into an occupancy mask; whatever
    # is left unoccupied and sits next to a wood becomes forest too. Pockets on
    # the far side of the Southern Link Road qualify as well - a road splits the
    # empty ground into separate pockets and each one is judged on its own.
    INFILL_SCALE_M = 4.0        # raster resolution (metres per pixel)
    INFILL_MIN_RADIUS_M = 25.0  # a pocket must fit a disk of this radius to count
    INFILL_NEAR_M = 80.0        # ...and lie within this distance of an existing wood
    INFILL_SIMPLIFY_M = 8.0     # Douglas-Peucker tolerance for the emitted outlines
    ROAD_CORRIDOR_M = 15.0      # width reserved around linear features

    def disk_structure(radius_px):
        yy, xx = np.ogrid[-radius_px:radius_px+1, -radius_px:radius_px+1]
        return (xx*xx + yy*yy) <= radius_px*radius_px

    def build_infill_polygons():
        n = int(round(8192.0 / INFILL_SCALE_M))
        occ_img = Image.new('L', (n, n), 0)
        wood_img = Image.new('L', (n, n), 0)
        occ_draw = ImageDraw.Draw(occ_img)
        wood_draw = ImageDraw.Draw(wood_img)
        line_w = max(1, int(round(ROAD_CORRIDOR_M / INFILL_SCALE_M)))

        for w in ways:
            pts = [(x / INFILL_SCALE_M, y / INFILL_SCALE_M) for x, y in w['coords']]
            if len(pts) < 2:
                continue
            tags = w['tags']
            is_wood = tags.get('natural') == 'wood'
            if is_wood or 'landuse' in tags or tags.get('natural') == 'water':
                occ_draw.polygon(pts, fill=255, outline=255)
                if is_wood:
                    wood_draw.polygon(pts, fill=255, outline=255)
            else:
                occ_draw.line(pts, fill=255, width=line_w, joint='curve')

        void = np.array(occ_img) == 0
        wood = np.array(wood_img) > 0

        # Erode first: this drops the thin stuff (map margins, the strip along the
        # railway, verges) and keeps only pockets of genuinely open ground.
        disk = disk_structure(max(1, int(round(INFILL_MIN_RADIUS_M / INFILL_SCALE_M))))
        core = ndimage.binary_erosion(void, structure=disk)
        if not core.any():
            return ([], 0.0), ([], 0.0)

        # Judge each pocket as a whole: thick enough to have a core, and near a wood.
        lab, n_lab = ndimage.label(void)
        dist_m = ndimage.distance_transform_edt(~wood) * INFILL_SCALE_M
        near = np.atleast_1d(ndimage.minimum(dist_m, lab, range(1, n_lab + 1)))
        thick_ids = np.zeros(n_lab + 1, dtype=bool)
        thick_ids[np.unique(lab[core])] = True
        thick_ids[0] = False

        # A pocket south of the Southern Link Road never becomes forest, however
        # close the woods across the road are: the forests there were explicitly
        # removed, so that ground stays open instead.
        south = np.zeros((n, n), dtype=bool)
        south[int(round(7650.0 / INFILL_SCALE_M)):, :] = True
        south_frac = np.atleast_1d(
            ndimage.mean(south.astype(np.float32), lab, range(1, n_lab + 1)))

        near_ids = np.concatenate(
            ([False], (near <= INFILL_NEAR_M) & (south_frac < 0.5))) & thick_ids
        far_ids = thick_ids & ~near_ids

        def trace(selected_ids):
            # Dilate the core back out so the pocket recovers its real outline while
            # the thin tentacles the erosion removed stay out of it.
            sel = selected_ids[lab]
            if not sel.any():
                return [], 0.0
            mask = ndimage.binary_dilation(core & sel, structure=disk) & sel
            area = mask.sum() * INFILL_SCALE_M**2 / 10000.0

            enclosed = ndimage.binary_fill_holes(mask) & ~mask
            if enclosed.any():
                print(f"   WARNING: infill pockets enclose "
                      f"{enclosed.sum() * INFILL_SCALE_M**2 / 10000.0:.1f} ha of occupied land; "
                      f"those holes get swallowed by the outline.")

            # Trace the outlines. Padding with a ring of zeros keeps every contour a
            # closed loop even where the pocket runs into the edge of the map.
            padded = np.zeros((n + 2, n + 2), dtype=np.float32)
            padded[1:-1, 1:-1] = mask
            axis = (np.arange(n + 2) - 0.5) * INFILL_SCALE_M
            gx, gy = np.meshgrid(axis, axis)

            fig, ax = plt.subplots()
            cs = ax.contour(gx, gy, padded, levels=[0.5])
            plt.close(fig)

            polygons = []
            for seg in cs.allsegs[0]:
                pts = [(min(max(px, 0.0), 8192.0), min(max(py, 0.0), 8192.0)) for px, py in seg]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                pts = simplify(pts, INFILL_SIMPLIFY_M)
                if len(pts) < 4:
                    continue
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                polygons.append(pts)
            return polygons, area

        return trace(near_ids), trace(far_ids)

    print("   Filling unoccupied land next to the forests...")
    (wood_polys, wood_ha), (yard_polys, yard_ha) = build_infill_polygons()

    # No forest infill any more: a pocket that would have grown into the woods
    # becomes farmland instead when it is worth working (over 1 ha), and is
    # dropped entirely when it is not.
    def ring_area_ha(poly):
        return abs(sum(poly[k][0] * poly[k+1][1] - poly[k+1][0] * poly[k][1]
                       for k in range(len(poly) - 1))) / 2.0 / 10000.0

    # A pocket the size of a whole district must not come out as one huge field:
    # anything over INFILL_SPLIT_HA is cut on a ~670 m grid (parcels of ~45 ha,
    # matching the eastern columns) separated by 10 m gaps.
    INFILL_SPLIT_HA = 100.0
    INFILL_PARCEL_M = 670.0
    INFILL_GAP_M = 10.0

    def split_infill_poly(poly):
        s = INFILL_SCALE_M
        n = int(round(8192.0 / s))
        img = Image.new('L', (n, n), 0)
        ImageDraw.Draw(img).polygon([(x / s, y / s) for x, y in poly],
                                    fill=255, outline=255)
        full = np.array(img) > 0
        mask = full.copy()
        pitch = max(1, int(round(INFILL_PARCEL_M / s)))
        gap = max(1, int(round(INFILL_GAP_M / s)))
        cuts = list(range(pitch, n, pitch))
        for g in cuts:
            mask[:, g:g+gap] = False
            mask[g:g+gap, :] = False
        lab, k = ndimage.label(mask)
        pieces = []
        for lid in range(1, k + 1):
            padded = np.zeros((n + 2, n + 2), dtype=np.float32)
            padded[1:-1, 1:-1] = (lab == lid)
            axis = (np.arange(n + 2) - 0.5) * s
            gx, gy = np.meshgrid(axis, axis)
            fig, ax = plt.subplots()
            cs = ax.contour(gx, gy, padded, levels=[0.5])
            plt.close(fig)
            if not cs.allsegs[0]:
                continue
            seg = max(cs.allsegs[0], key=len)
            pts = [(min(max(px, 0.0), 8192.0), min(max(py, 0.0), 8192.0))
                   for px, py in seg]
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            pts = simplify(pts, INFILL_SIMPLIFY_M)
            if len(pts) < 4:
                continue
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            pieces.append(pts)

        # Dirt roads along the cut corridors, wherever the pocket actually spans
        # them, so every parcel is reachable. Runs shorter than MIN_ROAD_M are
        # fringe slivers and get no road.
        MIN_ROAD_M = 50.0

        def runs(line):
            found = []
            r0 = None
            for r, v in enumerate(line):
                if v and r0 is None:
                    r0 = r
                elif not v and r0 is not None:
                    found.append((r0, r - 1))
                    r0 = None
            if r0 is not None:
                found.append((r0, len(line) - 1))
            return [(a, b) for a, b in found if (b - a + 1) * s >= MIN_ROAD_M]

        roads = []
        for g in cuts:
            c = (g + gap / 2.0) * s
            for r0, r1 in runs(full[:, g]):
                roads.append([(c, r0 * s), (c, (r1 + 1) * s)])
            for r0, r1 in runs(full[g, :]):
                roads.append([(r0 * s, c), ((r1 + 1) * s, c)])
        return pieces, roads

    def splice_into_named_way(way_name, pt):
        # Insert pt as a shared node into an already-emitted way; the Southern
        # Link Road is a straight x-sorted line, so the geometry does not change.
        for w in ways:
            if w['tags'].get('name') == way_name:
                for k in range(len(w['coords']) - 1):
                    if w['coords'][k][0] < pt[0] < w['coords'][k+1][0]:
                        w['coords'].insert(k + 1, (float(pt[0]), float(pt[1])))
                        w['node_refs'].insert(k + 1, get_node(*pt))
                        return True
        return False

    infill_field_idx = 1
    dropped = 0
    split_count = 0
    for poly in wood_polys:
        ha = ring_area_ha(poly)
        if ha <= 1.0:
            dropped += 1
            continue
        if ha > INFILL_SPLIT_HA:
            parts, dirt_roads = split_infill_poly(poly)
            split_count += 1
            n_spliced = 0
            for rd in dirt_roads:
                (x0, y0), (x1, y1) = rd
                # A vertical corridor ending against the Southern Link Road gets
                # extended onto it and joined with a shared node.
                if x0 == x1 and max(y0, y1) >= 7635.0:
                    rd = [(x0, min(y0, y1)), (x0, 7650.0)]
                    if splice_into_named_way('Southern Link Road', (x0, 7650.0)):
                        n_spliced += 1
                add_way(rd, {'highway': 'tertiary'})
            print(f"   Split a {ha:.1f} ha infill pocket into {len(parts)} parcels "
                  f"with {len(dirt_roads)} dirt roads ({n_spliced} joined to the "
                  f"Southern Link Road).")
        else:
            parts = [poly]
        for part in parts:
            p_ha = ring_area_ha(part)
            if p_ha <= 1.0:
                dropped += 1
                continue
            add_way(part, {'landuse': 'farmland',
                           'name': f'Field I{infill_field_idx} ({p_ha:.1f} ha)'})
            infill_field_idx += 1
    print(f"   Converted infill pockets into {infill_field_idx - 1} farmland fields "
          f"({split_count} split up), dropped {dropped} under 1 ha "
          f"(was {wood_ha:.1f} ha of forest infill).")

    # Pockets too far from any wood to be absorbed by it stay open ground: they are
    # tagged farmyard only, so they read as yard rather than as forest or field.
    for i, poly in enumerate(yard_polys):
        add_way(poly, {
            'landuse': 'farmyard',
            'name': f'Open Ground {i+1}'
        })
    print(f"   Added {len(yard_polys)} leftover farmyard areas covering {yard_ha:.1f} ha.")

    # 11b. Fields retired from farming. These parcels stay in the map as open
    # ground (farmyard) instead of farmland: mostly infill slivers and road-cut
    # leftovers not worth working. Matched by their base field token, with or
    # without the trailing hectare suffix.
    RETIRED_FIELDS = {
        'I3', 'I5', 'I8', 'I11', 'I15', 'I19', 'I22', 'I25', 'I28',
        'I31', 'I33', 'I34', 'I36', 'I44', '51b', '33a', '26a',
    }

    def retire_fields():
        pat = re.compile(r'^Field (\S+)( \(\d+(?:\.\d+)? ha\))?$')
        n_retired = 0
        for w in ways:
            if w['tags'].get('landuse') != 'farmland':
                continue
            m = pat.match(w['tags'].get('name', ''))
            if not m or m.group(1) not in RETIRED_FIELDS:
                continue
            w['tags']['landuse'] = 'farmyard'
            w['tags']['name'] = f"Open Ground {m.group(1)}{m.group(2) or ''}"
            n_retired += 1
        missing = n_retired != len(RETIRED_FIELDS)
        print(f"   Retired {n_retired}/{len(RETIRED_FIELDS)} fields to open ground."
              + (" WARNING: some retired field names were not found." if missing else ""))

    retire_fields()

    # 12. Forestry tracks in the south-eastern forest.
    # Three dirt tracks run along contour lines, so machinery works on the level and
    # crosses the forest instead of climbing it. On their own they would be stranded
    # (every contour here is an arc that starts and ends on the edge of the map), so a
    # haul road climbs from the Southern Link Road to the summit and crosses all three;
    # that is what ties them back into the primary network.
    FOREST_TRACK_LEVELS = [75.0, 100.0, 125.0]  # metres above sea level
    FOREST_TRACK_SIMPLIFY_M = 10.0
    FOREST_HAUL_STEP_M = 25.0
    FOREST_HAUL_STEER = 0.55        # 1.0 = straight up the fall line, 0.0 = straight at the summit
    FOREST_SUMMIT = (6283.0, 3393.0)
    FOREST_SEED = (6280.0, 3400.0)  # a point known to sit inside the south-eastern forest

    def build_forest_tracks():
        s = 4.0
        gn = int(round(8192.0 / s))
        # Elevation in metres on a 4 m grid, smoothed to ~44 m so the contours come out
        # as usable alignments instead of following every ripple in the DEM.
        elev = ndimage.uniform_filter(playable[::int(s), ::int(s)] / 100.0, size=11)

        wimg = Image.new('L', (gn, gn), 0)
        wdraw = ImageDraw.Draw(wimg)
        for w in ways:
            if w['tags'].get('natural') != 'wood':
                continue
            pts = [(x / s, y / s) for x, y in w['coords']]
            if len(pts) >= 2:
                wdraw.polygon(pts, fill=255, outline=255)
        lab, _ = ndimage.label(np.array(wimg) > 0)
        seed = lab[int(FOREST_SEED[1] / s), int(FOREST_SEED[0] / s)]
        if seed == 0:
            print("   WARNING: forest seed is not inside a wood; skipping forestry tracks.")
            return []
        forest = lab == seed

        def cota(x, y):
            u = min(max(x / s, 0.0), gn - 1.001)
            v = min(max(y / s, 0.0), gn - 1.001)
            i, j = int(u), int(v)
            fu, fv = u - i, v - j
            return float(elev[j, i] * (1-fu) * (1-fv) + elev[j, i+1] * fu * (1-fv)
                         + elev[j+1, i] * (1-fu) * fv + elev[j+1, i+1] * fu * fv)

        # Haul road: each step blends the uphill direction with the bearing to the
        # summit, which climbs steadily instead of curling round onto the fall line.
        haul = [forest_access_pt]
        x, y = forest_access_pt
        for _ in range(400):
            h = 8.0
            gx = (cota(x + h, y) - cota(x - h, y)) / (2 * h)
            gy = (cota(x, y + h) - cota(x, y - h)) / (2 * h)
            g = math.hypot(gx, gy)
            if g > 1e-9:
                gx, gy = gx / g, gy / g
            tx, ty = FOREST_SUMMIT[0] - x, FOREST_SUMMIT[1] - y
            tl = math.hypot(tx, ty)
            if tl < FOREST_HAUL_STEP_M * 1.5:
                break
            tx, ty = tx / tl, ty / tl
            dx = FOREST_HAUL_STEER * gx + (1 - FOREST_HAUL_STEER) * tx
            dy = FOREST_HAUL_STEER * gy + (1 - FOREST_HAUL_STEER) * ty
            dl = math.hypot(dx, dy)
            if dl < 1e-9:
                break
            x += FOREST_HAUL_STEP_M * dx / dl
            y += FOREST_HAUL_STEP_M * dy / dl
            if not (40.0 <= x <= 8152.0 and 40.0 <= y <= 8152.0):
                break
            haul.append((x, y))

        def junction_at(level):
            # The haul road climbs monotonically, so it meets each contour exactly once.
            for k in range(len(haul) - 1):
                za, zb = cota(*haul[k]), cota(*haul[k+1])
                if (za - level) * (zb - level) <= 0 and za != zb:
                    f = (level - za) / (zb - za)
                    return (haul[k][0] + f * (haul[k+1][0] - haul[k][0]),
                            haul[k][1] + f * (haul[k+1][1] - haul[k][1]))
            return None

        def insert_point(poly, p):
            # Splice p into poly at its nearest segment, so both ways end up sharing
            # the node get_node() hands out for that exact coordinate.
            best_d, best_k = None, 0
            for k in range(len(poly) - 1):
                ax, ay = poly[k]
                vx, vy = poly[k+1][0] - ax, poly[k+1][1] - ay
                l2 = vx * vx + vy * vy
                t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((p[0]-ax)*vx + (p[1]-ay)*vy) / l2))
                d = math.hypot(p[0] - (ax + t*vx), p[1] - (ay + t*vy))
                if best_d is None or d < best_d:
                    best_d, best_k = d, k
            return poly[:best_k+1] + [p] + poly[best_k+1:], best_d

        masked = np.where(forest, elev, np.nan)
        axis = np.arange(gn) * s
        gx_grid, gy_grid = np.meshgrid(axis, axis)

        tracks = []
        junctions = []
        for level in FOREST_TRACK_LEVELS:
            fig, ax = plt.subplots()
            cs = ax.contour(gx_grid, gy_grid, masked, levels=[level])
            plt.close(fig)
            segs = [seg for seg in cs.allsegs[0] if len(seg) > 3]
            if not segs:
                print(f"   WARNING: no contour at {level:.0f} m inside the forest.")
                continue
            j = junction_at(level)
            if j is None:
                print(f"   WARNING: haul road never reaches {level:.0f} m; track left unconnected.")
                seg = max(segs, key=lambda a: np.hypot(*np.diff(np.asarray(a), axis=0).T).sum())
                poly = simplify([(float(px), float(py)) for px, py in seg], FOREST_TRACK_SIMPLIFY_M)
                gap = float('nan')
            else:
                # Find the segment closest to the junction point j
                def dist_to_j(s_pts):
                    return min(math.hypot(p[0] - j[0], p[1] - j[1]) for p in s_pts)
                seg = min(segs, key=dist_to_j)
                poly = simplify([(float(px), float(py)) for px, py in seg], FOREST_TRACK_SIMPLIFY_M)
                poly, gap = insert_point(poly, j)
                junctions.append((level, j))
            tracks.append((level, poly, gap))

        # Splice the junctions into the haul road too, furthest along the climb first so
        # the earlier insertion indices stay valid.
        haul_poly = simplify(haul, FOREST_TRACK_SIMPLIFY_M)
        for level, j in sorted(junctions, key=lambda z: -z[0]):
            haul_poly, _ = insert_point(haul_poly, j)

        def length(poly):
            return sum(math.dist(poly[k], poly[k+1]) for k in range(len(poly) - 1))

        def grade(poly):
            g = [abs(cota(*poly[k+1]) - cota(*poly[k])) / max(1e-9, math.dist(poly[k], poly[k+1]))
                 for k in range(len(poly) - 1)]
            return (100.0 * float(np.mean(g)), 100.0 * float(np.max(g))) if g else (0.0, 0.0)

        add_way(haul_poly, {'highway': 'tertiary', 'name': 'Forest Haul Road'})
        gm, gx_ = grade(haul_poly)
        print(f"   Forest Haul Road: {length(haul_poly)/1000:.2f} km, {len(haul_poly)} nodes, "
              f"{cota(*haul_poly[0]):.0f}->{cota(*haul_poly[-1]):.0f} m, grade {gm:.1f}% avg / {gx_:.1f}% max.")

        for idx, (level, poly, gap) in enumerate(tracks, 1):
            add_way(poly, {'highway': 'tertiary', 'name': f'Forest Track {level:.0f}m'})
            gm, gx_ = grade(poly)
            print(f"   Forest Track {level:.0f}m: {length(poly)/1000:.2f} km, {len(poly)} nodes, "
                  f"grade {gm:.1f}% avg / {gx_:.1f}% max, junction offset {gap:.1f} m.")
        return tracks

    # print("   Laying out forestry tracks in the south-eastern forest...")
    # build_forest_tracks()

    # 13. Shared nodes at every at-grade crossing of the Mountain Pass Road.
    # The grid roads were emitted independently of it, so each intersection
    # point is spliced into both ways; without this the junctions would only
    # overlap visually and never share a node. Runs last so it also catches the
    # tertiary pieces reshaped in 10b and the infill dirt roads from 11.
    def connect_mountain_pass_crossings():
        def seg_int(a, b, c, d):
            rx, ry = b[0] - a[0], b[1] - a[1]
            sx, sy = d[0] - c[0], d[1] - c[1]
            den = rx * sy - ry * sx
            if abs(den) < 1e-12:
                return None
            t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / den
            u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / den
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                return (a[0] + t * rx, a[1] + t * ry), t, u
            return None

        def has_vertex(coords, p):
            return any(abs(v[0] - p[0]) < 1e-3 and abs(v[1] - p[1]) < 1e-3
                       for v in coords)

        prim = mountain_pass_way
        prim_inserts = []
        prim_seen = set()
        n_junctions = 0
        for w in ways:
            if w is prim or 'highway' not in w['tags']:
                continue
            w_inserts = []
            for i in range(len(prim['coords']) - 1):
                for j in range(len(w['coords']) - 1):
                    hit = seg_int(prim['coords'][i], prim['coords'][i+1],
                                  w['coords'][j], w['coords'][j+1])
                    if not hit:
                        continue
                    p, t, u = hit
                    p = (round(p[0], 3), round(p[1], 3))
                    n_junctions += 1
                    if not has_vertex(w['coords'], p):
                        w_inserts.append((j, u, p))
                    if not has_vertex(prim['coords'], p) and p not in prim_seen:
                        prim_seen.add(p)
                        prim_inserts.append((i, t, p))
            # Deepest segment first, so earlier insertion indices stay valid.
            for j, u, p in sorted(w_inserts, key=lambda z: (-z[0], -z[1])):
                w['coords'].insert(j + 1, p)
                w['node_refs'].insert(j + 1, get_node(*p))
        for i, t, p in sorted(prim_inserts, key=lambda z: (-z[0], -z[1])):
            prim['coords'].insert(i + 1, p)
            prim['node_refs'].insert(i + 1, get_node(*p))
        print(f"   Mountain Pass Road: {n_junctions} crossings, "
              f"{len(prim_inserts)} nodes spliced into the pass road itself.")

    connect_mountain_pass_crossings()

    # Generate XML
    osm_elem = ET.Element('osm', version='0.6', generator='Antigravity')
    
    # Add bounds
    ET.SubElement(osm_elem, 'bounds', {
        'minlat': f"{minlat:.10f}",
        'minlon': f"{minlon:.10f}",
        'maxlat': f"{maxlat:.10f}",
        'maxlon': f"{maxlon:.10f}"
    })

    # Add nodes
    sorted_node_ids = sorted(node_coords.keys())
    for nid in sorted_node_ids:
        lat, lon = node_coords[nid]
        ET.SubElement(osm_elem, 'node', {
            'id': str(nid),
            'lat': f"{lat:.10f}",
            'lon': f"{lon:.10f}",
            'version': '1',
            'timestamp': '2026-07-24T12:00:00Z',
            'changeset': '1',
            'uid': '1',
            'user': 'Antigravity'
        })

    # Add ways
    for way in ways:
        way_elem = ET.SubElement(osm_elem, 'way', {
            'id': str(way['id']),
            'version': '1',
            'timestamp': '2026-07-24T12:00:00Z',
            'changeset': '1',
            'uid': '1',
            'user': 'Antigravity'
        })
        for ref in way['node_refs']:
            ET.SubElement(way_elem, 'nd', ref=str(ref))
        for k, v in way['tags'].items():
            ET.SubElement(way_elem, 'tag', k=k, v=v)

    # Convert to pretty XML string
    xml_str = ET.tostring(osm_elem, encoding='utf-8')
    parsed_xml = minidom.parseString(xml_str)
    pretty_xml = parsed_xml.toprettyxml(indent='  ', encoding='utf-8')

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(script_dir, "map.osm")
    
    with open(output_path, "wb") as f:
        f.write(pretty_xml)

    print(f"[+] Successfully wrote {len(node_coords)} nodes and {len(ways)} ways to '{output_path}'.")

if __name__ == '__main__':
    main()
