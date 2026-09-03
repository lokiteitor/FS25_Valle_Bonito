#!/usr/bin/env python3
"""Centre, size and projection of the playable area.

These used to be defined here. They now live in `map_layout.py` at the root of the tree,
together with the rest of the layout, because the DEM generator needs the same numbers
and two copies of a map centre is one copy too many: the terrain and the vectors would
quietly describe different places.

This module stays as the name the scripts in this folder import, and re-exports the
projection unchanged, so `visualize_osm.py` and `check_forest_nodes.py` keep working
without a line changed:

    local_to_global(x, y) / global_to_local(lat, lon)   playable metres <-> lat, lon
    bounds()                                            the OSM <bounds> element
    polyline_length(pts) / ring_area_ha(ring)           measurement helpers
    LAT_CENTER, LON_CENTER, PLAYABLE_M, HALF_M, M_PER_DEG, M_PER_DEG_LON

Local coordinates are playable metres, x east, y south from the north edge, so the
centre of the map sits at (PLAYABLE_M / 2, PLAYABLE_M / 2).
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from map_layout import (                                            # noqa: E402,F401
    LAT_CENTER, LON_CENTER, PLAYABLE_M, HALF_M, CANVAS_M, OFFSET_M,
    M_PER_DEG, M_PER_DEG_LON,
    local_to_global, global_to_local, bounds,
    polyline_length, ring_area_ha,
)
