# FS25 Valle Bonito

Generador de mapas procedural para **Farming Simulator 25 (FS25)**.

Este proyecto permite generar los artefactos necesarios para la creación de un mapa personalizado de gran escala (8x8 km jugables en un lienzo de 12x12 km con cordillera montañosa exterior), listo para importar en **Giants Editor**.

## Dimensiones del Mapa

- **Área Jugable:** 8192 × 8192 m (8 km, centrado en `4096, 4096`).
- **Lienzo Total (Canvas):** 12288 × 12288 m (12 km, con 2048 m de margen montañoso en los cuatro costados).
- **Formato de Altura:** PNG de 16 bits (`uint16`), donde `valor / 100 = metros`.
- **Cota Base:** 46.4 metros de elevación.
- **Borde Exterior (Rim):** Montañas perimetrales que alcanzan entre 200 y 250 metros de elevación, con un faldón plano de transición de 100 metros.

## Estructura del Proyecto

- [`map_layout.py`](map_layout.py): **Única fuente de verdad geométrica**. Define dimensiones, coordenadas de proyección y registros de características (`CORRIDORS`, `WATER`, `PADS`, `AREAS`, `FIELDS`).
- [`dem_generator/`](dem_generator/):
  - `generate_new_dem_12k.py`: Genera el mapa de elevación `dem_new_12k.png` (12288x12288 px), `terrain_stats.json` y previsualizaciones.
  - `measure_elevation.py`: Batería de pruebas y reporte de aceptación geométrica y topográfica.
- [`osm_generator/`](osm_generator/):
  - `generate_osm.py`: Exporta los vectores en formato OpenStreetMap (`map.osm`) para delimitar parcelas, caminos y vegetación en Giants Editor.
  - `check_osm.py`: Validación de invariantes geoespaciales y topología vectorial.
  - `visualize_osm.py`: Genera un render 2D de inspección rápida (`map_osm_visual.png`).
- [`visualizer/`](visualizer/):
  - `create_3d_viewer.py`: Genera una aplicación web interactiva en Three.js (`dem_viewer_3d.html`) para explorar el terreno en 3D en el navegador.
- [`render_pda.py`](render_pda.py): Dibuja las texturas de mapa y satélite PDA (4K y 2K) para el mod.
- [`pf_generator/`](pf_generator/): Generador de mapas de suelos para Precision Farming (`soilMap.png`).

## Pipeline de Generación

```bash
# 1. Validar geometría del layout
python3 map_layout.py

# 2. Generar y verificar el mapa de elevación (DEM)
python3 dem_generator/generate_new_dem_12k.py
python3 dem_generator/measure_elevation.py

# 3. Generar y verificar vectores OSM
python3 osm_generator/generate_osm.py
python3 osm_generator/check_osm.py
python3 osm_generator/visualize_osm.py

# 4. Visor 3D web
python3 visualizer/create_3d_viewer.py
# Para verlo en el navegador: python3 -m http.server 8000 (abrir http://localhost:8000/visualizer/dem_viewer_3d.html)

# 5. Texturas PDA / Minimapa
python3 render_pda.py
```