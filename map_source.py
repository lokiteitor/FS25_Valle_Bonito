#!/usr/bin/env python3
"""Compatibility shim: the name `visualizer/create_3d_viewer.py` imports.

The viewer does `import map_source as ms` and uses exactly two names from it,
`ms.CANVAS_M` and `ms.PLAYABLE_M` (create_3d_viewer.py:39-40). The module itself was
never in the tree, so the 3D viewer could not run at all.

Rather than reintroduce a third copy of the map's dimensions, this re-exports them from
`map_layout`, which is the one place they are defined. The whole layout is re-exported
alongside them, so anything else that reaches for `map_source` gets the real geometry
instead of a stub.
"""
from map_layout import *          # noqa: F401,F403
from map_layout import (CANVAS_M, PLAYABLE_M, OFFSET_M, LAT_CENTER, LON_CENTER,
                        local_to_global, global_to_local, bounds)   # noqa: F401
