import os
import shutil
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw
import subprocess

# ==========================================
# 1. CONFIGURACIÓN DE COLORES (RGB)
# ==========================================
# Color del fondo del área jugable (Verde Pasto)
COLOR_PASTO = (110, 168, 83)       # Hex: #6EA853

# Color del área no jugable / bordes (Negro)
COLOR_BORDE = (0, 0, 0)            # Hex: #000000

# Color de los bosques (Verde más oscuro)
COLOR_BOSQUE = (34, 76, 27)        # Hex: #224C1B

# Color de los corrales / zonas industriales (Café barro)
COLOR_CORRAL = (139, 90, 43)       # Hex: #8B5A2B

# Color de los caminos y carreteras (Gris)
COLOR_CAMINOS = (112, 112, 112)    # Hex: #707070

# ==========================================
# 2. RUTAS DE ARCHIVOS
# ==========================================
# Ruta a la carpeta de tu mapa actual:
DIRECTORIO_MAPA = r"c:\Users\david\OneDrive\Desktop\FS25_Valle_Bonito"

# Ruta al convertidor texconv.exe:
RUTA_TEXCONV = r"C:\Users\david\OneDrive\Desktop\Map4FS\executables\texconv.exe"

OSM_PATH = os.path.join(DIRECTORIO_MAPA, "custom_osm.osm")
MAP_DIR = os.path.join(DIRECTORIO_MAPA, "map")
OVERVIEW_DDS = os.path.join(MAP_DIR, "overview.dds")
BACKUP_DDS = os.path.join(MAP_DIR, "overview_orig.dds")

# ==========================================
# 3. RESPALDO DEL ARCHIVO ORIGINAL
# ==========================================
if os.path.exists(OVERVIEW_DDS) and not os.path.exists(BACKUP_DDS):
    print("Creando respaldo de overview.dds original -> overview_orig.dds")
    shutil.copy2(OVERVIEW_DDS, BACKUP_DDS)

# ==========================================
# 4. LECTURA Y PROCESAMIENTO DE OSM
# ==========================================
print("Leyendo archivo OSM...")
tree = ET.parse(OSM_PATH)
root = tree.getroot()

# Leer límites geográficos del área jugable
bounds = root.find('bounds')
if bounds is not None:
    minlat = float(bounds.get('minlat'))
    maxlat = float(bounds.get('maxlat'))
    minlon = float(bounds.get('minlon'))
    maxlon = float(bounds.get('maxlon'))
else:
    raise ValueError("El archivo OSM no contiene la etiqueta <bounds> necesaria para delimitar el mapa.")

print(f"Límites del área jugable: lat={minlat} a {maxlat}, lon={minlon} a {maxlon}")

# Cargar todos los nodos en memoria (ID -> coordenadas lat, lon)
nodes = {}
for node in root.findall('node'):
    nid = node.get('id')
    lat = float(node.get('lat'))
    lon = float(node.get('lon'))
    nodes[nid] = (lat, lon)

# Cargar caminos (Ways) y clasificarlos por sus etiquetas (tags)
forests = []
farmyards = []
roads = []

for way in root.findall('way'):
    way_nodes = []
    for nd in way.findall('nd'):
        ref = nd.get('ref')
        if ref in nodes:
            way_nodes.append(nodes[ref])
    
    if not way_nodes:
        continue
        
    tags = {t.get('k'): t.get('v') for t in way.findall('tag')}
    
    # Clasificación usando etiquetas estándar de OSM y Map4FS
    is_forest = tags.get('natural') == 'wood' or tags.get('landuse') == 'forest'
    is_farmyard = tags.get('landuse') == 'farmyard'
    is_road = 'highway' in tags
    
    if is_forest:
        forests.append(way_nodes)
    elif is_farmyard:
        farmyards.append(way_nodes)
    elif is_road:
        roads.append((way_nodes, tags.get('highway')))

print(f"Estructuras cargadas: {len(forests)} bosques, {len(farmyards)} corrales, {len(roads)} caminos.")

# ==========================================
# 5. FUNCIÓN DE RENDERIZADO
# ==========================================
def renderizar_pda(tamano_total):
    """
    Dibuja los elementos del OSM en una imagen centrada.
    El área jugable ocupa exactamente el 50% central (2048px en una textura de 4096px).
    El 50% restante exterior se deja como borde no jugable (Negro).
    """
    tamano_jugable = tamano_total // 2
    tamano_borde = (tamano_total - tamano_jugable) // 2
    
    # 1. Crear el lienzo del mapa jugable con fondo verde pasto
    img_jugable = Image.new('RGB', (tamano_jugable, tamano_jugable), COLOR_PASTO)
    draw = ImageDraw.Draw(img_jugable)
    
    # 2. Dibujar Bosques (Polígonos rellenos de verde oscuro)
    for poly in forests:
        poly_px = []
        for lat, lon in poly:
            x = (lon - minlon) / (maxlon - minlon) * tamano_jugable
            y = (maxlat - lat) / (maxlat - minlat) * tamano_jugable
            poly_px.append((x, y))
        if len(poly_px) >= 3:
            draw.polygon(poly_px, fill=COLOR_BOSQUE)
            
    # 3. Dibujar Corrales (Polígonos rellenos de café barro)
    for poly in farmyards:
        poly_px = []
        for lat, lon in poly:
            x = (lon - minlon) / (maxlon - minlon) * tamano_jugable
            y = (maxlat - lat) / (maxlat - minlat) * tamano_jugable
            poly_px.append((x, y))
        if len(poly_px) >= 3:
            draw.polygon(poly_px, fill=COLOR_CORRAL)
            
    # 4. Dibujar Caminos (Líneas grises con grosor dinámico)
    for line, hw_type in roads:
        line_px = []
        for lat, lon in line:
            x = (lon - minlon) / (maxlon - minlon) * tamano_jugable
            y = (maxlat - lat) / (maxlat - minlat) * tamano_jugable
            line_px.append((x, y))
        if len(line_px) >= 2:
            # Escalar el grosor en base al tamaño de la imagen actual
            escala = tamano_jugable / 2048.0
            ancho = int(max(2, 6 * escala))
            if hw_type == 'primary':
                ancho = int(max(3, 8 * escala))
            draw.line(line_px, fill=COLOR_CAMINOS, width=ancho, joint="round")
            
    # 5. Crear el lienzo final completo (con fondo negro)
    img_final = Image.new('RGB', (tamano_total, tamano_total), COLOR_BORDE)
    
    # 6. Pegar el área jugable exactamente en el centro
    img_final.paste(img_jugable, (tamano_borde, tamano_borde))
    
    return img_final

# ==========================================
# 6. GENERAR Y GUARDAR IMÁGENES PNG
# ==========================================
os.makedirs(os.path.join(DIRECTORIO_MAPA, 'satellite'), exist_ok=True)
os.makedirs(os.path.join(DIRECTORIO_MAPA, 'previews'), exist_ok=True)

print("Generando imagen 4K (satellite/overview.png)...")
img_4k = renderizar_pda(4096)
img_4k.save(os.path.join(DIRECTORIO_MAPA, 'satellite', 'overview.png'))

print("Generando imagen 2K (previews/satellite_overview.png)...")
img_2k = renderizar_pda(2048)
img_2k.save(os.path.join(DIRECTORIO_MAPA, 'previews', 'satellite_overview.png'))

print("Generando imagen 3.8K (satellite/satellite_overview.png)...")
img_3k = renderizar_pda(3851)
img_3k.save(os.path.join(DIRECTORIO_MAPA, 'satellite', 'satellite_overview.png'))

# ==========================================
# 7. CONVERSIÓN A DDS (COMPRESIÓN DE TEXTURAS)
# ==========================================
print("Convirtiendo a DDS usando texconv.exe...")
comando = [
    RUTA_TEXCONV,
    '-f', 'BC1_UNORM', # Formato estándar para mapas PDA sin transparencia
    '-m', '13',        # 13 niveles de mipmaps para evitar aliasing al hacer zoom
    '-y',              # Sobrescribir archivos existentes
    '-o', MAP_DIR,
    os.path.join(DIRECTORIO_MAPA, 'satellite', 'overview.png')
]

resultado = subprocess.run(comando, capture_output=True, text=True)
if resultado.returncode == 0:
    print("¡Éxito! map/overview.dds generado correctamente.")
else:
    print("Error al ejecutar texconv:")
    print(resultado.stderr)
