import os
import shutil
import json
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw
import subprocess
import sys

# Desactivar límite de tamaño de imagen en Pillow
Image.MAX_IMAGE_PIXELS = None

# ==========================================
# 1. DIMENSIONES DEL MAPA (4K TOTAL: 2K JUGABLE + 2K BORDE)
# ==========================================
# - Área jugable (Playable)       : 2048 x 2048 px (2K centrado, representando los 8192m)
# - Margen exterior (Borde BG)    : 1024 px por lado (2048 px en total)
# - Lienzo total (Canvas Total)   : 4096 x 4096 px (4K)
TAMANO_JUGABLE = 2048
TAMANO_BORDE = 1024
TAMANO_TOTAL = 4096     # 1024 + 2048 + 1024 = 4096 px

PLAYABLE_M = 8192.0     # 8192 metros reales del área jugable

# Ancla de proyección geográfica (de map_layout.py)
LAT_CENTER = 43.0600
LON_CENTER = -95.2800
M_PER_DEG = 111111.0
M_PER_DEG_LON = M_PER_DEG * math.cos(math.radians(LAT_CENTER))
HALF_M = PLAYABLE_M / 2.0

# ==========================================
# 2. CONFIGURACIÓN DE COLORES Y ESTILOS
# ==========================================
COLOR_BORDE_BG = (28, 42, 24)           # Fondo exterior (Montañas / Fuera de límite)
COLOR_PASTO = (108, 142, 86)            # Pasto base jugable (#6C8E56)
COLOR_CAMPO = (126, 156, 98)            # Campos de cultivo (#7E9C62)
COLOR_CAMPO_BORDE = (92, 122, 70)       # Bordes de campos (#5C7A46)
COLOR_BOSQUE = (42, 74, 34)             # Bosques (#2A4A22)
COLOR_BOSQUE_BORDE = (30, 56, 24)       # Bordes de bosques
COLOR_AGUA = (52, 108, 148)             # Agua Río y Lago (#346C94)
COLOR_AGUA_BORDE = (36, 80, 114)        # Bordes de agua
COLOR_GRANJA = (138, 118, 92)           # Granjas y corrales (#8A765C)
COLOR_GRANJA_BORDE = (105, 88, 68)
COLOR_INDUSTRIAL = (112, 118, 126)      # Edificios e industria (#70767E)
COLOR_INDUSTRIAL_BORDE = (82, 88, 96)
COLOR_ASFALTO = (62, 65, 72)            # Asfalto real de carreteras (#3E4148)
COLOR_ASFALTO_BORDE = (45, 48, 54)
COLOR_PUENTE = (180, 50, 50)            # Puentes (#B43232)
COLOR_LIMITE_JUGABLE = (255, 255, 255)  # Línea límite del área jugable 2K

# ==========================================
# 3. RUTAS DE ARCHIVOS Y DETECCIÓN
# ==========================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAPA_DIR = os.path.join(SCRIPT_DIR, "FS25_Granja_bonita")
MAP_DIR = os.path.join(MAPA_DIR, "map")
TEXTURES_JSON = os.path.join(MAPA_DIR, "info_layers", "textures.json")
BG_TEXTURE_PATH = os.path.join(MAPA_DIR, "assets", "background", "background_texture.png")

OSM_CANDIDATOS = [
    os.path.join(SCRIPT_DIR, "osm_generator", "custom.osm"),
    os.path.join(SCRIPT_DIR, "osm_generator", "map.osm"),
    os.path.join(MAPA_DIR, "custom_osm.osm"),
]

OSM_PATH = None
for cand in OSM_CANDIDATOS:
    if os.path.exists(cand):
        OSM_PATH = cand
        break

if not OSM_PATH:
    raise FileNotFoundError("No se encontró ningún archivo OSM en las rutas esperadas.")

TEXCONV_CANDIDATOS = [
    r"C:\Users\david\OneDrive\Desktop\Map4FS\executables\texconv.exe",
    os.path.join(SCRIPT_DIR, "texconv.exe"),
    shutil.which("texconv.exe"),
]

RUTA_TEXCONV = None
for cand in TEXCONV_CANDIDATOS:
    if cand and os.path.exists(cand):
        RUTA_TEXCONV = cand
        break

OVERVIEW_DDS = os.path.join(MAP_DIR, "overview.dds")
OVERVIEW_PNG = os.path.join(MAP_DIR, "overview.png")
BACKUP_DDS = os.path.join(MAP_DIR, "overview_orig.dds")

print("=" * 60)
print("GENERADOR DE PDA PARA FS25 (4K TOTAL: 2K JUGABLE + 2K BORDE)")
print("=" * 60)
print(f"Lienzo total        : {TAMANO_TOTAL} x {TAMANO_TOTAL} px (4K)")
print(f"Área jugable        : {TAMANO_JUGABLE} x {TAMANO_JUGABLE} px (2K centrado)")
print(f"Margen exterior     : {TAMANO_BORDE} px por lado (2048 px total)")
print(f"Directorio del mapa : {MAPA_DIR}")
print(f"Polígonos exactos   : {TEXTURES_JSON}")
print(f"Archivo OSM         : {OSM_PATH}")
print(f"Ruta texconv.exe    : {RUTA_TEXCONV}")
print(f"Destino overview.dds: {OVERVIEW_DDS}")

# Respaldo original
if os.path.exists(OVERVIEW_DDS) and not os.path.exists(BACKUP_DDS):
    print(f"Creando respaldo de overview.dds -> {BACKUP_DDS}")
    shutil.copy2(OVERVIEW_DDS, BACKUP_DDS)

# ==========================================
# 4. CARGA DE DATOS VECTORIALES
# ==========================================
tex_data = {}
if os.path.exists(TEXTURES_JSON):
    print("Cargando polígonos exactos de info_layers/textures.json...")
    with open(TEXTURES_JSON, 'r') as f:
        tex_data = json.load(f)

print("Leyendo archivo OSM...")
tree = ET.parse(OSM_PATH)
root = tree.getroot()

nodes = {}
for node in root.findall('node'):
    nid = node.get('id')
    lat = float(node.get('lat'))
    lon = float(node.get('lon'))
    nodes[nid] = (lat, lon)

def global_to_canvas_px(lat, lon, size_total=TAMANO_TOTAL):
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))
    x_m = HALF_M + (lon - LON_CENTER) * M_PER_DEG_LON
    y_m = HALF_M - (lat - LAT_CENTER) * M_PER_DEG
    px = borde + (x_m / PLAYABLE_M) * jugable
    py = borde + (y_m / PLAYABLE_M) * jugable
    return px, py

def local_m_to_canvas_px(x_m, y_m, size_total=TAMANO_TOTAL):
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))
    px = borde + (x_m / PLAYABLE_M) * jugable
    py = borde + (y_m / PLAYABLE_M) * jugable
    return px, py

# ==========================================
# 5. FUNCIÓN DE RENDERIZADO PDA 4K (2K JUGABLE)
# ==========================================
def renderizar_pda(size_total=4096):
    print(f"\nRenderizando lienzo {size_total} x {size_total} px (área jugable 2K centrada)...")
    img = Image.new('RGB', (size_total, size_total), COLOR_BORDE_BG)
    
    # 0. Textura de fondo exterior si existe
    if os.path.exists(BG_TEXTURE_PATH):
        try:
            bg_im = Image.open(BG_TEXTURE_PATH).convert('RGB').resize((size_total, size_total), Image.BILINEAR)
            img.paste(bg_im, (0, 0))
        except Exception as e:
            print(f"Aviso con textura de fondo: {e}")

    draw = ImageDraw.Draw(img)
    escala = size_total / 4096.0
    borde = int(round(1024.0 * escala))
    jugable = int(round(2048.0 * escala))

    # 1. Área jugable central (2K)
    x0, y0 = borde, borde
    x1, y1 = borde + jugable, borde + jugable
    draw.rectangle([x0, y0, x1, y1], fill=COLOR_PASTO)

    # 2. Campos de cultivo (Polígonos exactos de textures.json)
    borde_campo = max(1, int(1.2 * escala))
    if 'fields' in tex_data:
        for f in tex_data['fields']:
            poly = [local_m_to_canvas_px(p[0], p[1], size_total) for p in f]
            if len(poly) >= 3:
                draw.polygon(poly, fill=COLOR_CAMPO, outline=COLOR_CAMPO_BORDE, width=borde_campo)

    # 3. Bosques y rompevientos (de custom.osm)
    borde_bosque = max(1, int(1.5 * escala))
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        name = tags.get('name', '')
        if (tags.get('natural') == 'wood' or tags.get('landuse') == 'forest') and 'Isla' not in name:
            pts = [nodes[nd.get('ref')] for nd in way.findall('nd') if nd.get('ref') in nodes]
            if len(pts) >= 3:
                poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in pts]
                draw.polygon(poly, fill=COLOR_BOSQUE, outline=COLOR_BOSQUE_BORDE, width=borde_bosque)

    # 4. Cuerpos de agua: Río y Lago del Norte
    borde_agua = max(1, int(2 * escala))
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        if tags.get('natural') == 'water' or 'water' in tags:
            pts = [nodes[nd.get('ref')] for nd in way.findall('nd') if nd.get('ref') in nodes]
            if len(pts) >= 3:
                poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in pts]
                draw.polygon(poly, fill=COLOR_AGUA, outline=COLOR_AGUA_BORDE, width=borde_agua)

    # 5. Isla del Lago (Renderizada sobre el lago)
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        name = tags.get('name', '')
        if 'Isla' in name:
            pts = [nodes[nd.get('ref')] for nd in way.findall('nd') if nd.get('ref') in nodes]
            if len(pts) >= 3:
                poly = [global_to_canvas_px(lat, lon, size_total) for lat, lon in pts]
                draw.polygon(poly, fill=COLOR_BOSQUE, outline=COLOR_BOSQUE_BORDE, width=borde_bosque)

    # 6. Granjas, Patios y Cuadras Urbanas (Polígonos exactos de textures.json)
    borde_granja = max(1, int(1.2 * escala))
    if 'farmyards' in tex_data:
        for f in tex_data['farmyards']:
            poly = [local_m_to_canvas_px(p[0], p[1], size_total) for p in f]
            if len(poly) >= 3:
                draw.polygon(poly, fill=COLOR_GRANJA, outline=COLOR_GRANJA_BORDE, width=borde_granja)

    # 7. Edificios y Zonas Industriales (Polígonos exactos de textures.json)
    if 'buildings' in tex_data:
        for b in tex_data['buildings']:
            poly = [local_m_to_canvas_px(p[0], p[1], size_total) for p in b]
            if len(poly) >= 3:
                draw.polygon(poly, fill=COLOR_INDUSTRIAL, outline=COLOR_INDUSTRIAL_BORDE, width=borde_granja)

    # 8. Red de Carreteras y Calles EXACTAS (Polígonos reales de textures.json)
    borde_asfalto = max(1, int(1.2 * escala))
    if 'roads' in tex_data:
        for r in tex_data['roads']:
            poly = [local_m_to_canvas_px(p[0], p[1], size_total) for p in r]
            if len(poly) >= 3:
                draw.polygon(poly, fill=COLOR_ASFALTO, outline=COLOR_ASFALTO_BORDE, width=borde_asfalto)

    # 9. Puentes (de custom.osm)
    ancho_puente = max(3, int(10 * escala))
    for way in root.findall('way'):
        tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
        if tags.get('bridge') == 'yes':
            pts = [nodes[nd.get('ref')] for nd in way.findall('nd') if nd.get('ref') in nodes]
            if len(pts) >= 2:
                line = [global_to_canvas_px(lat, lon, size_total) for lat, lon in pts]
                draw.line(line, fill=COLOR_PUENTE, width=ancho_puente, joint="butt")

    # 10. Línea de límite jugable (Borde sutil blanco del área 2K)
    draw.rectangle([x0, y0, x1, y1], outline=COLOR_LIMITE_JUGABLE, width=max(1, int(2 * escala)))

    return img

# ==========================================
# 6. GENERAR Y GUARDAR IMÁGENES
# ==========================================
os.makedirs(MAP_DIR, exist_ok=True)
os.makedirs(os.path.join(MAPA_DIR, 'satellite'), exist_ok=True)
os.makedirs(os.path.join(MAPA_DIR, 'previews'), exist_ok=True)

print("\nGenerando imagen principal 4K (4096 x 4096 px con área jugable 2048 px)...")
img_4k = renderizar_pda(4096)

ruta_satellite_png = os.path.join(MAPA_DIR, 'satellite', 'overview.png')
img_4k.save(ruta_satellite_png, format="PNG")
img_4k.save(OVERVIEW_PNG, format="PNG")
print(f"  -> Guardado: {ruta_satellite_png}")
print(f"  -> Guardado: {OVERVIEW_PNG}")

print("\nGenerando preview 2K (2048 x 2048 px)...")
img_2k = renderizar_pda(2048)
ruta_preview_png = os.path.join(MAPA_DIR, 'previews', 'satellite_overview.png')
img_2k.save(ruta_preview_png, format="PNG")
print(f"  -> Guardado: {ruta_preview_png}")

# ==========================================
# 7. CONVERSIÓN A DDS (TEXCONV)
# ==========================================
if RUTA_TEXCONV and os.path.exists(RUTA_TEXCONV):
    print("\nConvirtiendo a DDS para FS25 usando texconv.exe...")
    comando = [
        RUTA_TEXCONV,
        '-f', 'BC1_UNORM',
        '-m', '13',
        '-y',
        '-o', MAP_DIR,
        OVERVIEW_PNG
    ]
    
    res = subprocess.run(comando, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"¡Éxito! Textura DDS 4K generada: {OVERVIEW_DDS}")
    else:
        print(f"Error al ejecutar texconv: {res.stderr}")
else:
    print("\n[AVISO] texconv.exe no encontrado; se generó overview.png.")

print("\n==========================================")
print("¡PDA de 4096 px (2K jugable + 2K borde) completado con éxito!")
print("==========================================")
