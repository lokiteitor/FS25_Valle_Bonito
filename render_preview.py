import os
import sys
import math
import json
import shutil
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

Image.MAX_IMAGE_PIXELS = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
Parent_DIR = os.path.dirname(SCRIPT_DIR)
if os.path.basename(SCRIPT_DIR) == "FS25_Valle_Bonito_x16":
    ROOT_DIR = Parent_DIR
    MOD_DIR = SCRIPT_DIR
else:
    ROOT_DIR = SCRIPT_DIR
    MOD_DIR = os.path.join(ROOT_DIR, "FS25_Valle_Bonito_x16")

sys.path.insert(0, ROOT_DIR)
import map_layout

DEM_RGB16_PATH = os.path.join(ROOT_DIR, "visualizer", "dem_1024_rgb16.png")
DEM_12K_PATH = os.path.join(ROOT_DIR, "dem_generator", "dem_new_12k.png")
OVERVIEW_PNG_PATH = os.path.join(MOD_DIR, "map", "overview.png")
UI_PREVIEW_DDS_PATH = os.path.join(MOD_DIR, "map", "textures", "ui", "preview.dds")

TEXCONV_CANDIDATOS = [
    r"C:\Users\david\OneDrive\Desktop\Map4FS\executables\texconv.exe",
    os.path.join(ROOT_DIR, "texconv.exe"),
    shutil.which("texconv.exe"),
]
RUTA_TEXCONV = next((c for c in TEXCONV_CANDIDATOS if c and os.path.exists(c)), None)


def get_font(name, size):
    path = os.path.join("C:/Windows/Fonts", name)
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def build_shaded_relief_map(size=2048):
    """Builds a high-detail 2048x2048 shaded relief map of the 4K PDA overview blended with hillshading.

    Note on coordinate spaces:
    - overview.png (4096x4096) has 1024px border + 2048px playable (8192m -> 4m/px) + 1024px border.
      That means overview.png spans 16384 x 16384 m (-4096..12288 m in playable coords).
    - dem_1024_rgb16.png (1024x1024) spans 12288 x 12288 m (-2048..10240 m in playable coords),
      which corresponds to the central [512..3584] box of the 4096x4096 overview.png ([256..1792] at 2048x2048).
    """
    pda = Image.open(OVERVIEW_PNG_PATH).convert("RGB").resize((size, size), Image.LANCZOS)
    pda_np = np.array(pda, dtype=np.float32)

    elev = np.full((size, size), 25.0, dtype=np.float32)
    if os.path.exists(DEM_RGB16_PATH):
        rgb16 = Image.open(DEM_RGB16_PATH).convert("RGB")
        arr = np.array(rgb16, dtype=np.uint16)
        # Red = low byte (height % 256), Green = high byte (height // 256)
        elev_cm = (arr[:, :, 1] << 8) | arr[:, :, 0]
        elev_m = elev_cm.astype(np.float32) / 100.0
        # 12288m out of 16384m is 3/4 of size (1536x1536 centered at [256..1792])
        dem_px = int(round(size * (12288.0 / 16384.0)))
        pad_px = (size - dem_px) // 2
        elev_img = Image.fromarray(elev_m, mode="F").resize((dem_px, dem_px), Image.BICUBIC)
        elev[pad_px:pad_px + dem_px, pad_px:pad_px + dem_px] = np.array(elev_img, dtype=np.float32)

    # Compute normals / hillshade (pixel step = 16384 / 2048 = 8m)
    step_m = 16384.0 / size
    dy, dx = np.gradient(elev, step_m)
    # Exaggerate Z slightly for crisp relief
    z_exag = 2.8
    dx *= z_exag
    dy *= z_exag

    # Sun from NW-W (azimuth 315 deg, altitude 42 deg)
    az = math.radians(315.0)
    alt = math.radians(42.0)
    lx = math.cos(alt) * math.cos(az)
    ly = math.cos(alt) * math.sin(az)
    lz = math.sin(alt)

    norm = np.sqrt(dx * dx + dy * dy + 1.0)
    nx = -dx / norm
    ny = -dy / norm
    nz = 1.0 / norm

    shade = nx * lx + ny * ly + nz * lz
    shade = np.clip(shade, 0.0, 1.0)

    # Enhance contrast around flat datum (sin(alt) ~ 0.669)
    base_shade = math.sin(alt)
    shade_factor = 1.0 + (shade - base_shade) * 0.95
    shade_factor = np.clip(shade_factor, 0.45, 1.45)[:, :, None]

    # Add subtle hypsometric tint on high elevations (e.g. Colina Espiral > 60m)
    high_mask = np.clip((elev - 45.0) / 160.0, 0.0, 1.0)[:, :, None]
    warm_rock = np.array([150.0, 138.0, 115.0], dtype=np.float32)[None, None, :]
    pda_tinted = pda_np * (1.0 - 0.28 * high_mask) + warm_rock * (0.28 * high_mask)

    shaded = np.clip(pda_tinted * shade_factor, 0, 255).astype(np.uint8)
    return Image.fromarray(shaded, mode="RGB"), elev


def draw_rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def generate_preview_2k():
    size = 2048
    relief_full, elev_full = build_shaded_relief_map(size=2048)

    # Crop focusing on playable area + slight scenic border margin
    # In 2048 canvas: playable area is [512..1536] (1024x1024 px).
    # Let's crop [340..1708] so the playable area fills ~75% of the view with surrounding mountains visible
    crop_box = (360, 360, 1688, 1688)
    map_cropped = relief_full.crop(crop_box).resize((size, size), Image.LANCZOS)

    # Also load scenic background artwork if available to blend at the top header
    canvas = Image.new("RGBA", (size, size), (18, 26, 20, 255))
    canvas.paste(map_cropped.convert("RGBA"), (0, 0))

    # Create dark vignette & top/bottom gradient overlays so UI text & badges pop
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ov_np = np.zeros((size, size, 4), dtype=np.uint8)

    y_idx = np.linspace(0.0, 1.0, size)[:, None]
    x_idx = np.linspace(0.0, 1.0, size)[None, :]

    # Radial edge vignette
    dist_edge = np.maximum(np.abs(x_idx - 0.5) * 2.0, np.abs(y_idx - 0.5) * 2.0)
    vig = np.clip((dist_edge - 0.72) / 0.28, 0.0, 1.0) ** 1.6 * 150.0

    # Top banner gradient (0.0 to 0.24)
    top_grad = np.clip((0.25 - y_idx) / 0.25, 0.0, 1.0) ** 1.5 * 225.0

    # Bottom banner gradient (0.72 to 1.0)
    bot_grad = np.clip((y_idx - 0.70) / 0.30, 0.0, 1.0) ** 1.4 * 235.0

    alpha = np.clip(np.maximum(np.maximum(top_grad, bot_grad), vig), 0, 245).astype(np.uint8)
    ov_np[:, :, 0] = 12
    ov_np[:, :, 1] = 18
    ov_np[:, :, 2] = 14
    ov_np[:, :, 3] = alpha
    canvas = Image.alpha_composite(canvas, Image.fromarray(ov_np, mode="RGBA"))

    draw = ImageDraw.Draw(canvas)

    # Coordinate helper for playable metres (0..8192) -> cropped canvas (0..2048)
    # In 2048 relief_full: playable 0..8192m is at 512..1536 px.
    # After crop [360..1688] (width 1328) and resize to 2048:
    def playable_to_px(xm, ym):
        px_full = 512.0 + (xm / 8192.0) * 1024.0
        py_full = 512.0 + (ym / 8192.0) * 1024.0
        px = (px_full - 360.0) / 1328.0 * 2048.0
        py = (py_full - 360.0) / 1328.0 * 2048.0
        return px, py

    # Highlight playable boundary frame with crisp double border
    bx0, by0 = playable_to_px(0, 0)
    bx1, by1 = playable_to_px(8192, 8192)
    draw.rectangle([bx0 - 3, by0 - 3, bx1 + 3, by1 + 3], outline=(20, 30, 22, 200), width=8)
    draw.rectangle([bx0, by0, bx1, by1], outline=(235, 195, 95, 230), width=4)

    # Draw spiral road on Colina Espiral so it stands out visually
    if map_layout.HILLS:
        hill = map_layout.HILLS[0]
        s_pts = [playable_to_px(x, y) for x, y in hill["spiral_pts"]]
        if len(s_pts) >= 2:
            draw.line(s_pts, fill=(45, 35, 25, 210), width=8, joint="round")
            draw.line(s_pts, fill=(215, 180, 125, 235), width=4, joint="round")

    # Draw POI callout pins on key landmarks
    font_poi = get_font("segoeuib.ttf", 30)
    pois = [
        (1272, 1344, "Pueblo Valle Bonito", (235, 185, 75)),
        (6000, 2000, "Colina Espiral (225m)", (115, 215, 125)),
        (380, 4500, "Lago Valle Bonito", (85, 185, 245)),
        (7536, 4024, "Pueblo Este", (235, 185, 75)),
        (6846, 6646, "Granja Principal (50 ha)", (230, 145, 90)),
    ]

    for xm, ym, label, color in pois:
        px, py = playable_to_px(xm, ym)
        # Draw target dot
        r = 12
        draw.ellipse([px - r - 3, py - r - 3, px + r + 3, py + r + 3], fill=(15, 20, 15, 220))
        draw.ellipse([px - r, py - r, px + r, py + r], fill=color + (255,), outline=(255, 255, 255, 240), width=3)

        # Measure text pill
        bbox = draw.textbbox((0, 0), label, font=font_poi)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 18, 10
        # Position pill above or below dot
        pill_x0 = px - tw / 2 - pad_x
        pill_y0 = py - r - th - pad_y * 2 - 10
        if pill_x0 < bx0 + 16:
            pill_x0 = bx0 + 16
        if pill_x0 + tw + pad_x * 2 > bx1 - 16:
            pill_x0 = bx1 - 16 - (tw + pad_x * 2)
        pill_x1 = pill_x0 + tw + pad_x * 2
        pill_y1 = pill_y0 + th + pad_y * 2

        draw_rounded_rect(
            draw,
            [pill_x0, pill_y0, pill_x1, pill_y1],
            radius=14,
            fill=(18, 26, 20, 225),
            outline=color + (220,),
            width=2,
        )
        draw.text((pill_x0 + pad_x, pill_y0 + pad_y - 3), label, font=font_poi, fill=(248, 250, 245, 255))

    # ==========================================
    # TOP HEADER: TITLE & "x16" BADGE
    # ==========================================
    font_sub = get_font("segoeuib.ttf", 36)
    font_title = get_font("arialbd.ttf", 118)
    font_badge = get_font("arialbd.ttf", 72)

    # Top accent line
    draw.rectangle([80, 52, 1968, 58], fill=(235, 185, 65, 240))

    # Subtitle
    sub_text = "FARMING SIMULATOR 25  •  CUSTOM LARGE SCALE MAP"
    draw.text((88, 76), sub_text, font=font_sub, fill=(215, 195, 130, 245))

    # Main Title "VALLE BONITO" with drop shadow
    title_text = "VALLE BONITO"
    draw.text((92, 128), title_text, font=font_title, fill=(0, 0, 0, 210))
    draw.text((86, 122), title_text, font=font_title, fill=(252, 252, 248, 255))

    # "16x" / "8.2 x 8.2 KM" Gold Badge on top right
    badge_box = [1640, 82, 1968, 242]
    draw_rounded_rect(draw, badge_box, radius=24, fill=(225, 168, 45, 245), outline=(255, 230, 150, 255), width=4)
    b_text = "x16"
    bb = draw.textbbox((0, 0), b_text, font=font_badge)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    draw.text((1640 + (328 - bw) / 2, 96), b_text, font=font_badge, fill=(20, 26, 18, 255))

    b_sub = "8.2 × 8.2 KM"
    font_bsub = get_font("segoeuib.ttf", 32)
    bb2 = draw.textbbox((0, 0), b_sub, font=font_bsub)
    bw2 = bb2[2] - bb2[0]
    draw.text((1640 + (328 - bw2) / 2, 184), b_sub, font=font_bsub, fill=(25, 32, 20, 245))

    # ==========================================
    # BOTTOM FOOTER: MAP STATS & COMPASS
    # ==========================================
    stats = [
        ("6,710 HA", "ÁREA JUGABLE"),
        ("174", "CAMPOS"),
        ("245", "PARCELAS"),
        ("2 PUEBLOS", "+ 10 GRANJAS"),
        ("PF READY", "MAPA DE SUELOS"),
    ]
    card_w = 348
    card_h = 136
    gap = 22
    total_w = len(stats) * card_w + (len(stats) - 1) * gap
    start_x = (size - total_w) / 2
    card_y0 = 1836
    card_y1 = card_y0 + card_h

    font_stat_val = get_font("arialbd.ttf", 48)
    font_stat_lbl = get_font("segoeuib.ttf", 26)

    for i, (val, lbl) in enumerate(stats):
        cx0 = start_x + i * (card_w + gap)
        cx1 = cx0 + card_w
        draw_rounded_rect(
            draw,
            [cx0, card_y0, cx1, card_y1],
            radius=18,
            fill=(22, 32, 24, 230),
            outline=(225, 180, 75, 200),
            width=3,
        )
        vb = draw.textbbox((0, 0), val, font=font_stat_val)
        vw = vb[2] - vb[0]
        draw.text((cx0 + (card_w - vw) / 2, card_y0 + 18), val, font=font_stat_val, fill=(245, 205, 95, 255))

        lb = draw.textbbox((0, 0), lbl, font=font_stat_lbl)
        lw = lb[2] - lb[0]
        draw.text((cx0 + (card_w - lw) / 2, card_y0 + 82), lbl, font=font_stat_lbl, fill=(215, 225, 215, 245))

    # Outer frame border
    draw.rectangle([16, 16, size - 16, size - 16], outline=(235, 190, 80, 180), width=4)

    return canvas.convert("RGB"), map_cropped


def generate_icon_256(clean_map_img):
    """Generates a clean 256x256 icon for modDesc/savegame icons using the clean shaded relief map."""
    size = 256
    # Use the clean map background without POI label pills
    icon = clean_map_img.crop((240, 240, 1808, 1808)).resize((size, size), Image.LANCZOS).convert("RGBA")
    ov = Image.new("RGBA", (size, size), (12, 20, 14, 150))
    icon = Image.alpha_composite(icon, ov)
    draw = ImageDraw.Draw(icon)

    # Outer gold frame
    draw_rounded_rect(draw, [6, 6, size - 6, size - 6], radius=28, outline=(235, 185, 65, 245), width=5)

    # Center badge
    font_vb = get_font("arialbd.ttf", 78)
    font_sub = get_font("segoeuib.ttf", 28)

    t1 = "VALLE"
    t2 = "BONITO"
    b1 = draw.textbbox((0, 0), t1, font=font_sub)
    b2 = draw.textbbox((0, 0), t2, font=font_sub)
    draw.text(((size - (b1[2] - b1[0])) / 2, 34), t1, font=font_sub, fill=(245, 248, 240, 255))
    draw.text(((size - (b2[2] - b2[0])) / 2, 66), t2, font=font_sub, fill=(245, 248, 240, 255))

    # Gold x16 pill
    pill = [58, 118, 198, 198]
    draw_rounded_rect(draw, pill, radius=18, fill=(225, 168, 45, 245), outline=(255, 235, 160, 255), width=3)
    tx = "x16"
    bx = draw.textbbox((0, 0), tx, font=font_vb)
    draw.text(((size - (bx[2] - bx[0])) / 2, 110), tx, font=font_vb, fill=(18, 24, 16, 255))

    ts = "FS25 MAP"
    font_sm = get_font("segoeuib.ttf", 20)
    bs = draw.textbbox((0, 0), ts, font=font_sm)
    draw.text(((size - (bs[2] - bs[0])) / 2, 212), ts, font=font_sm, fill=(225, 195, 110, 245))

    return icon


def main():
    print("Generando preview.png (2048x2048) e icon.png (256x256) para Valle Bonito x16...")
    preview_rgb, clean_map = generate_preview_2k()

    preview_png_path = os.path.join(MOD_DIR, "preview.png")
    preview_rgb.save(preview_png_path, format="PNG")
    print(f"  -> Guardado: {preview_png_path}")

    ui_dir = os.path.join(MOD_DIR, "map", "textures", "ui")
    os.makedirs(ui_dir, exist_ok=True)
    ui_preview_png = os.path.join(ui_dir, "preview.png")
    preview_rgb.save(ui_preview_png, format="PNG")

    icon_rgba = generate_icon_256(clean_map)
    icon_png_path = os.path.join(MOD_DIR, "icon.png")
    icon_rgba.save(icon_png_path, format="PNG")
    print(f"  -> Guardado: {icon_png_path}")

    if RUTA_TEXCONV and os.path.exists(RUTA_TEXCONV):
        print(f"Convirtiendo a DDS con texconv ({RUTA_TEXCONV})...")
        # 1. preview.dds in root (BC1_UNORM, 2048x2048)
        cmd_prev = [
            RUTA_TEXCONV,
            "-f", "BC1_UNORM",
            "-m", "1",
            "-y",
            "-o", MOD_DIR,
            preview_png_path,
        ]
        res1 = subprocess.run(cmd_prev, capture_output=True, text=True)
        if res1.returncode == 0:
            root_preview_dds = os.path.join(MOD_DIR, "preview.dds")
            print(f"  -> Generado: {root_preview_dds}")
            shutil.copy2(root_preview_dds, os.path.join(ui_dir, "preview.dds"))
            print(f"  -> Copiado a: {os.path.join(ui_dir, 'preview.dds')}")
        else:
            print("Error texconv preview:", res1.stderr)

        # 2. icon.dds in root (BC3_UNORM / DXT5, 256x256)
        cmd_icon = [
            RUTA_TEXCONV,
            "-f", "BC3_UNORM",
            "-m", "1",
            "-y",
            "-o", MOD_DIR,
            icon_png_path,
        ]
        res2 = subprocess.run(cmd_icon, capture_output=True, text=True)
        if res2.returncode == 0:
            print(f"  -> Generado: {os.path.join(MOD_DIR, 'icon.dds')}")
        else:
            print("Error texconv icon:", res2.stderr)
    else:
        print("[AVISO] No se encontró texconv.exe; se guardaron los archivos PNG.")


if __name__ == "__main__":
    main()
