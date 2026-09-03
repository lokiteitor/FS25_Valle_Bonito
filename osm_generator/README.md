# osm_generator

`generate_osm.py` writes `map.osm` for the 8192 x 8192 m playable area: northwest Iowa
farm country, laid out from `map_layout.py` at the root of the tree.

- `map_extent.py`         re-exports the projection from `map_layout.py`
- `generate_osm.py`       write map.osm from the shared layout
- `visualize_osm.py`      render map.osm to map_osm_visual.png
- `check_forest_nodes.py` inventory and invariants; exits 1 if one is broken

Standard library plus matplotlib for the render. The DEM has to run first: the parcelling
reads `dem_generator/terrain_stats.json` to make smaller fields on broken ground.

    python3 ../dem_generator/generate_new_dem_12k.py
    python3 generate_osm.py         # -> map.osm
    python3 check_forest_nodes.py   # -> inventory + invariants
    python3 visualize_osm.py        # -> map_osm_visual.png

## Map centre

    LAT_CENTER =  43.0600
    LON_CENTER = -95.2800

They live in `map_layout.py`, and everything else is derived from them. Royal, Clay
County, Iowa: the western edge of the Des Moines Lobe, deep prairie soils, and the flat
open country the map is modelled on. Moving the map means changing those two numbers and
re-running both generators.

## Extent

The playable area is 8192 x 8192 m. Local coordinates are playable metres, x east, y
south from the north edge, so the centre of the map sits at (4096, 4096).

Projection: equirectangular about the centre, 111111.0 m per degree of latitude and
111111.0 * cos(LAT_CENTER) m per degree of longitude, which puts the corners at

    minlat  43.0231359631      south edge, y = 8192
    maxlat  43.0968640369      north edge, y = 0
    minlon -95.3304546325      west edge,  x = 0
    maxlon -95.2295453675      east edge,  x = 8192

These are the four values in the `<bounds>` element of `map.osm`. The 3D viewer stretches
that box to fill the playable square whatever it says, so it has to stay right.

## What is on the map

| Feature | Tags |
|---|---|
| 420th Street, straight east-west through the middle | `highway=primary`, `ref=B40` |
| The Public Land Survey grid, one mile apart | `highway=secondary` |
| Farm lanes and village streets | `highway=tertiary` |
| The branch line, straight north-south, crossing the primary at the centre | `railway=rail` |
| Three river bridges and three creek culverts | the way's own tag plus `bridge=yes`, `layer=1` |
| 200 fields, 3 to 84 ha, 72% of the playable area | `landuse=farmland` |
| Three villages, seven farmsteads, the co-op elevator | `landuse=farmyard` |
| River timber, farmstead groves, snow fences and field hedgerows | `natural=wood` + `landuse=farmyard` + `leaf_type` |
| The river, the tributary feeding the lake, and the lake itself | `natural=water` (+ `water=river` / `water=lake`) |

The vocabulary is closed on purpose: it is exactly what `visualize_osm.py` and
`visualizer/create_3d_viewer.py` know how to draw, and both drop anything else without a
word. That is why the floodplain pasture carries no tag of its own - it is simply ground
the parcelling leaves out of cultivation, which is what wet bottom land amounts to
anyway. `check_forest_nodes.py` verifies that no way was emitted that neither renderer
can see.

## Invariants

`check_forest_nodes.py` fails the build if any of these break:

- at most 200 fields, none over 100 ha, none under 3 ha
- no field inside the river basin: 348 m clear of the river centreline (the edge of the
  floodplain the DEM cuts), 125 m clear of the creek, and outside the lake margin
- no timber standing in the water
- every node inside the playable area
- every area closed on its own first node - the 3D viewer decides polygon versus line by
  comparing the first and last coordinate exactly
- every way carrying a tag both renderers draw

## Windbreaks

Three jobs, and the job decides the placement:

- **Farmstead groves** around the buildings, one way per side with the lane side left
  open, for the heating and cooling bill.
- **Living snow fences** upwind of a road so the drift piles up in the trees. The winter
  wind is out of the northwest, so they stand on the **north** side of an east-west road
  and the **west** side of a north-south one - which is the south and east edge of a
  block. On the wrong side it is just a hedge.
- **Field hedgerows** across the inside of a block. They are laid before the parcelling,
  so the fields form either side of the trees rather than being cut up afterwards.

River timber is two strips, one per bank, starting outside the water's edge and
alternating sides along the reach. Buffering the centreline instead would put the inner
22 m of every wood in the river.

## The bocage generator

`generate_osm_bocage.py` is the previous English layout, centred on 52.0620, -1.3400.
Reference only: it needs a `map_source` API that no longer exists, and it writes to
`map.osm`, so running it would overwrite the real file.
