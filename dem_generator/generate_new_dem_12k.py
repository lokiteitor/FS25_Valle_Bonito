import os
import time
import math
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
from scipy.ndimage import gaussian_filter

# For generating visual maps
import matplotlib
matplotlib.use('Agg') # Non-interactive backend
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource

def val_noise(shape, grid_size, weight, seed=20260608):
    """Generates smooth value noise by upscaling a small random grid using bicubic interpolation."""
    np.random.seed(seed)
    small = np.random.uniform(-1.0, 1.0, size=(grid_size, grid_size)).astype(np.float32)
    temp_img = Image.fromarray(small)
    temp_img = temp_img.resize((shape[1], shape[0]), Image.Resampling.BICUBIC)
    return np.array(temp_img) * weight

def get_road_x_global(y_m, offset_m=2048.0, S_playable=8192.0):
    """Vectorized calculation of the road center x-coordinate in meters."""
    y_local = y_m - offset_m
    y_local = np.clip(y_local, 0.0, S_playable)
    y_miles = y_local / 1024.0
    
    x_miles = np.zeros_like(y_miles)
    
    mask1 = y_miles <= 2.2
    x_miles[mask1] = 7.0
    
    mask2 = (y_miles > 2.2) & (y_miles <= 3.8)
    u2 = (y_miles[mask2] - 2.2) / 1.6
    x_miles[mask2] = 4.0 + 3.0 * (1.0 + np.cos(np.pi * u2)) / 2.0
    
    mask3 = (y_miles > 3.8) & (y_miles <= 4.2)
    x_miles[mask3] = 4.0
    
    mask4 = (y_miles > 4.2) & (y_miles <= 5.8)
    u4 = (y_miles[mask4] - 4.2) / 1.6
    x_miles[mask4] = 1.0 + 3.0 * (1.0 + np.cos(np.pi * u4)) / 2.0
    
    mask5 = y_miles > 5.8
    x_miles[mask5] = 1.0
    
    x_local = x_miles * 1024.0
    return x_local + offset_m

def main():
    t_start = time.time()
    print("=== FS25 12K New DEM Generator (Exactly 12288x12288 for 8K Maps) ===")
    
    # Configuration
    S_px = 12288  # Heightmap resolution in pixels (exactly 12288x12288)
    S_m = 12288    # Heightmap size in meters (12288x12288m)
    scale_m_to_px = 1.0  # 1 pixel = 1 meter
    offset_m = 2048.0   # Playable area (8192x8192) centered in the 12288 canvas
    
    seed = 20260608
    np.random.seed(seed)
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dem_path = os.path.join(script_dir, "dem_new_12k.png")
    output_vis_path = os.path.join(script_dir, "dem_new_visual_12k.png")
    output_detail_vis_path = os.path.join(script_dir, "dem_new_visual_detail_12k.png")
    
    print(f"1. Generating coordinate grids for size {S_px}x{S_px} pixels ({S_m}x{S_m} meters)...")
    y_indices_px, x_indices_px = np.indices((S_px, S_px), dtype=np.float32)
    
    # Convert pixel indices to meter coordinates
    x_m = x_indices_px / scale_m_to_px
    y_m = y_indices_px / scale_m_to_px
    
    print("2. Loading base DEM from 'map_dem_new.png'...")
    input_dem_path = os.path.join(script_dir, "map_dem_new.png")
    img_base = Image.open(input_dem_path)
    data_base = np.array(img_base, dtype=np.float32)
    
    print("3. Generating flat plain noise (max 10m height difference)...")
    noise_plain = (
        val_noise((S_px, S_px), 16, 350.0, seed=seed) +
        val_noise((S_px, S_px), 32, 100.0, seed=seed+1) +
        val_noise((S_px, S_px), 64, 50.0, seed=seed+2)
    )
    noise_min = noise_plain.min()
    noise_max = noise_plain.max()
    noise_plain = (noise_plain - noise_min) / (noise_max - noise_min) * 1000.0 - 500.0
    
    print("4. Implementing central East-West mountain and blending...")
    # Mountain in Y_playable 2048 to 6144 (Y_absolute 4096 to 8192)
    is_in_central_y = (y_m >= 4096.0) & (y_m <= 8192.0)
    is_in_playable = (y_m >= 2048.0) & (y_m <= 10240.0) & (x_m >= 2048.0) & (x_m <= 10240.0)

    # Scale mountain to have max peak of 256.0m (raw: 25600.0) in the final terrain.
    # We target a pre-smoothed peak of 257.12m (raw: 25712.0) to compensate for macro-smoothing loss.
    # The mountain base is at H_plain (3500.0). Scale the height above base.
    mountain_peak = data_base[is_in_playable].max()
    scale_factor = (25712.0 - 3500.0) / (mountain_peak - 3500.0)
    data_mountain_scaled = 3500.0 + (data_base - 3500.0) * scale_factor
    binary_mountain = (data_base > 5000.0) & is_in_playable & is_in_central_y
    M_mountain = gaussian_filter(binary_mountain.astype(np.float32), sigma=40.0 * scale_m_to_px)
    
    H_plain = 3500.0
    terrain_inside = M_mountain * data_mountain_scaled + (1.0 - M_mountain) * (H_plain + noise_plain)
    
    print("4.1. Implementing flat northern plain (1.5 km wide across the north: Y <= 3548 meters)...")
    # Flat northern plain from northern map border towards south (Y_absolute in [0, 3548])
    rx0, rx1 = 0.0, 12288.0
    ry0, ry1 = 0.0, 3548.0
    
    dx_flat = np.maximum(0.0, np.maximum(rx0 - x_m, x_m - rx1))
    dy_flat = np.maximum(0.0, np.maximum(ry0 - y_m, y_m - ry1))
    dist_flat = np.sqrt(dx_flat*dx_flat + dy_flat*dy_flat)
    
    W_TRANS_FLAT = 300.0
    w_flat = np.zeros_like(dist_flat)
    w_flat[dist_flat == 0] = 1.0
    trans_mask_flat = (dist_flat > 0) & (dist_flat <= W_TRANS_FLAT)
    t_flat = dist_flat[trans_mask_flat] / W_TRANS_FLAT
    w_flat[trans_mask_flat] = 0.5 * (1.0 + np.cos(np.pi * t_flat))
    
    H_north_plain = 3500.0
    terrain_inside = w_flat * H_north_plain + (1.0 - w_flat) * terrain_inside
    
    print("4.2. Implementing lake (600m x 1200m) and town (65 ha) extending to lake at the East...")
    # Lake: 600m width x 1200m height, starts 15m from East playable border
    # X: [10240 - 15 - 600, 10240 - 15] = [9625, 10225]
    # Y: [2048 + 1000, 2048 + 1000 + 1200] = [3048, 4248] (1 km from North playable border)
    lake_x0, lake_x1 = 10240.0 - 15.0 - 600.0, 10240.0 - 15.0
    lake_y0, lake_y1 = 2048.0 + 1000.0, 2048.0 + 1000.0 + 1200.0
    H_lake_floor = 2000.0  # 15m depth below 35m surface

    # Town: 64.8 ha = 540m width x 1200m height (extends to the lake shore)
    # X: [9085, 9625]
    # Y: [3048, 4248]
    t_x0, t_x1 = 9085.0, lake_x0
    t_y0, t_y1 = lake_y0, lake_y1

    # Town plateau: exactly H_plain inside the rect, with a 100m cosine skirt
    # into the surroundings. North of y=3548 the surroundings are the flat plain
    # itself (already H_plain), so the skirt only shows along the southern
    # extension, where the plateau meets the rolling noise at lake level.
    dx_t = np.maximum(0.0, np.maximum(t_x0 - x_m, x_m - t_x1))
    dy_t = np.maximum(0.0, np.maximum(t_y0 - y_m, y_m - t_y1))
    dist_t = np.sqrt(dx_t*dx_t + dy_t*dy_t)
    W_TRANS_TOWN = 100.0
    w_town = np.zeros_like(dist_t)
    w_town[dist_t == 0] = 1.0
    trans_mask_town = (dist_t > 0) & (dist_t <= W_TRANS_TOWN)
    t_town = dist_t[trans_mask_town] / W_TRANS_TOWN
    w_town[trans_mask_town] = 0.5 * (1.0 + np.cos(np.pi * t_town))
    terrain_inside = w_town * H_plain + (1.0 - w_town) * terrain_inside

    print("4.4. Implementing South farmyard below southern road (100 ha = 2000m x 500m)...")
    # Farmyard below Southern Primary Road: 100 ha = 2000m width x 500m height, starts 15m from playable borders
    # (Below Southern Primary Road at Y_local=7650 / Y_global=9698)
    # X: [10240 - 15 - 2000, 10240 - 15] = [8225, 10225]
    # Y: [10240 - 15 - 500, 10240 - 15] = [9725, 10225]
    sw_x0, sw_x1 = 8225.0, 10225.0
    sw_y0, sw_y1 = 9725.0, 10225.0
    
    # Calculate target height dynamically as median of this region
    sub_sw = terrain_inside[int(sw_y0):int(sw_y1), int(sw_x0):int(sw_x1)]
    H_sw_target = np.median(sub_sw)
    print(f"   SW Farmyard target height: {H_sw_target:.1f}")
    
    # Flatten with 100m smooth transition
    margin_sw = 100.0
    bx0 = max(0, int(sw_x0 - margin_sw - 5))
    bx1 = min(S_px-1, int(sw_x1 + margin_sw + 5))
    by0 = max(0, int(sw_y0 - margin_sw - 5))
    by1 = min(S_px-1, int(sw_y1 + margin_sw + 5))
    
    terrain_ref = terrain_inside.copy()
    
    ny = by1 - by0 + 1
    nx = bx1 - bx0 + 1
    
    y_sub, x_sub = np.indices((ny, nx), dtype=np.float32)
    x_sub += bx0
    y_sub += by0
    dx_pt = np.maximum(0.0, np.maximum(sw_x0 - x_sub, x_sub - sw_x1))
    dy_pt = np.maximum(0.0, np.maximum(sw_y0 - y_sub, y_sub - sw_y1))
    d = np.sqrt(dx_pt*dx_pt + dy_pt*dy_pt)
    
    inside_mask = (d == 0)
    ramp_mask = (d > 0) & (d <= margin_sw)
    
    patch = terrain_inside[by0:by1+1, bx0:bx1+1]
    patch_ref = terrain_ref[by0:by1+1, bx0:bx1+1]
    
    patch[inside_mask] = H_sw_target
    w_ramp = 0.5 * (1.0 + np.cos(np.pi * d[ramp_mask] / margin_sw))
    patch[ramp_mask] = w_ramp * H_sw_target + (1.0 - w_ramp) * patch_ref[ramp_mask]
    
    local_smoothed = gaussian_filter(patch, sigma=10.0 * scale_m_to_px)
    patch[ramp_mask] = local_smoothed[ramp_mask]
    terrain_inside[by0:by1+1, bx0:bx1+1] = patch

    print("4.5. Implementing Yard Y1 flat terrain (50 ha = 1000m x 500m at South-West)...")
    # Yard Y1 below Southern Primary Road at West side:
    # X_local: [15, 1015] -> X_global: [2048 + 15, 2048 + 1015] = [2063, 3063]
    # Y_local: [7677, 8177] -> Y_global: [2048 + 7677, 2048 + 8177] = [9725, 10225]
    y1_x0, y1_x1 = 2048.0 + 15.0, 2048.0 + 1015.0
    y1_y0, y1_y1 = 2048.0 + 7677.0, 2048.0 + 8177.0

    sub_y1 = terrain_inside[int(y1_y0):int(y1_y1), int(y1_x0):int(y1_x1)]
    H_y1_target = np.median(sub_y1)
    print(f"   Yard Y1 target height: {H_y1_target:.1f}")

    margin_y1 = 100.0
    by0_y1 = max(0, int(y1_y0 - margin_y1 - 5))
    by1_y1 = min(S_px-1, int(y1_y1 + margin_y1 + 5))
    bx0_y1 = max(0, int(y1_x0 - margin_y1 - 5))
    bx1_y1 = min(S_px-1, int(y1_x1 + margin_y1 + 5))

    terrain_ref_y1 = terrain_inside.copy()
    ny_y1 = by1_y1 - by0_y1 + 1
    nx_y1 = bx1_y1 - bx0_y1 + 1

    y_sub_y1, x_sub_y1 = np.indices((ny_y1, nx_y1), dtype=np.float32)
    x_sub_y1 += bx0_y1
    y_sub_y1 += by0_y1
    dx_pt_y1 = np.maximum(0.0, np.maximum(y1_x0 - x_sub_y1, x_sub_y1 - y1_x1))
    dy_pt_y1 = np.maximum(0.0, np.maximum(y1_y0 - y_sub_y1, y_sub_y1 - y1_y1))
    d_y1 = np.sqrt(dx_pt_y1*dx_pt_y1 + dy_pt_y1*dy_pt_y1)

    inside_mask_y1 = (d_y1 == 0)
    ramp_mask_y1 = (d_y1 > 0) & (d_y1 <= margin_y1)

    patch_y1 = terrain_inside[by0_y1:by1_y1+1, bx0_y1:bx1_y1+1]
    patch_ref_y1 = terrain_ref_y1[by0_y1:by1_y1+1, bx0_y1:bx1_y1+1]

    patch_y1[inside_mask_y1] = H_y1_target
    w_ramp_y1 = 0.5 * (1.0 + np.cos(np.pi * d_y1[ramp_mask_y1] / margin_y1))
    patch_y1[ramp_mask_y1] = w_ramp_y1 * H_y1_target + (1.0 - w_ramp_y1) * patch_ref_y1[ramp_mask_y1]

    local_smoothed_y1 = gaussian_filter(patch_y1, sigma=10.0 * scale_m_to_px)
    patch_y1[ramp_mask_y1] = local_smoothed_y1[ramp_mask_y1]
    terrain_inside[by0_y1:by1_y1+1, bx0_y1:bx1_y1+1] = patch_y1

    # Non-playable area now follows the exact same natural morphology as playable area
    terrain = terrain_inside

    print("5. Excavating lake...")
    dx_lake = np.maximum(0.0, np.maximum(lake_x0 - x_m, x_m - lake_x1))
    dy_lake = np.maximum(0.0, np.maximum(lake_y0 - y_m, y_m - lake_y1))
    dist_lake = np.sqrt(dx_lake*dx_lake + dy_lake*dy_lake)
    
    W_SHORE = 20.0
    w_lake = np.zeros_like(dist_lake)
    w_lake[dist_lake == 0] = 1.0
    trans_mask_lake = (dist_lake > 0) & (dist_lake <= W_SHORE)
    t_lake = dist_lake[trans_mask_lake] / W_SHORE
    w_lake[trans_mask_lake] = 0.5 * (1.0 + np.cos(np.pi * t_lake))
    
    terrain = w_lake * H_lake_floor + (1.0 - w_lake) * terrain
    
    print("6. Smoothing entire terrain (macro-smoothing)...")
    # Smooth with adjusted sigma to scale with pixel resolution (6m = 9px)
    terrain = gaussian_filter(terrain, sigma=6 * scale_m_to_px)
    
    # Clamp terrain to valid 16-bit range
    terrain = np.clip(terrain, 2000.0, 62000.0)
    
    print(f"4. Saving final DEM heightmap to '{output_dem_path}'...")
    img_out = Image.fromarray(terrain.astype(np.int32), mode="I")
    img_out.save(output_dem_path)
    print(f"   Saved heightmap successfully (Min={terrain.min():.1f}, Max={terrain.max():.1f}).")
    
    print("5. Generating visual maps...")
    vis_scale = 12  # Upscaled to match 1024x1024 visual dimension (12288 / 12 = 1024)
    terrain_vis = terrain[::vis_scale, ::vis_scale]
    
    ls = LightSource(azdeg=315, altdeg=45)
    hs = ls.shade(terrain_vis, cmap=plt.get_cmap('terrain'), vert_exag=0.12, blend_mode='overlay')
    
    # --- Map 1: Full 12K Map View ---
    print("   Generating full map visualization...")
    fig, ax = plt.subplots(figsize=(12, 12), dpi=150)
    ax.imshow(hs, extent=[0, 12288, 12288, 0])
    
    # Grid and tick labels for 12.3km canvas
    ax.set_xlabel("X (East-West) [meters]", fontsize=12, fontweight='bold')
    ax.set_ylabel("Y (North-South) [meters]", fontsize=12, fontweight='bold')
    ax.set_xticks(np.arange(0, 12289, 1024))
    ax.set_yticks(np.arange(0, 12289, 1024))
    ax.grid(True, which='both', color='white', linestyle='--', linewidth=0.5, alpha=0.4)
    ax.tick_params(colors='white')
    # Dark theme styling
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    for spine in ax.spines.values():
        spine.set_color('white')
    ax.yaxis.label.set_color('white')
    ax.xaxis.label.set_color('white')
    ax.title.set_color('white')
    
    ax.set_title("Full 12K DEM Map (Exactly 12288x12288px - Clean Terrain)", fontsize=16, fontweight='bold', pad=15)
    
    rect_playable = plt.Rectangle((2048.0, 2048.0), 8192.0, 8192.0, 
                                  fill=False, edgecolor='white', linewidth=2, linestyle='--', label='Playable Border (8km)')
    ax.add_patch(rect_playable)
    
    rect_flat_north = plt.Rectangle((rx0, ry0), (rx1 - rx0), (ry1 - ry0),
                                     fill=False, edgecolor='yellow', linewidth=2, linestyle=':', label='Flat North Plain')
    ax.add_patch(rect_flat_north)
    
    rect_town = plt.Rectangle((t_x0, t_y0), (t_x1 - t_x0), (t_y1 - t_y0),
                              fill=False, edgecolor='cyan', linewidth=1.5, linestyle='-', label='Town (65 ha)')
    rect_lake = plt.Rectangle((lake_x0, lake_y0), (lake_x1 - lake_x0), (lake_y1 - lake_y0),
                              fill=False, edgecolor='blue', linewidth=1.5, linestyle='-', label='Lake (600x1200m)')
    rect_sw_farmyard = plt.Rectangle((sw_x0, sw_y0), (sw_x1 - sw_x0), (sw_y1 - sw_y0),
                                     fill=False, edgecolor='magenta', linewidth=1.5, linestyle='-', label='SW Farmyard (100 ha)')
    rect_y1 = plt.Rectangle((y1_x0, y1_y0), (y1_x1 - y1_x0), (y1_y1 - y1_y0),
                            fill=False, edgecolor='orange', linewidth=1.5, linestyle='-', label='Yard Y1 (50 ha)')
    ax.add_patch(rect_town)
    ax.add_patch(rect_lake)
    ax.add_patch(rect_sw_farmyard)
    ax.add_patch(rect_y1)
    
    # Draw natural mountain shape contour lines at 50m, 100m, 150m, 200m, 250m elevation
    x_range_all = np.arange(1024) * 12.0
    x_grid_vis, y_grid_vis = np.meshgrid(x_range_all, x_range_all)
    cnt = ax.contour(x_grid_vis, y_grid_vis, terrain_vis, levels=[5000.0, 10000.0, 15000.0, 20000.0, 25000.0], colors=['#00FF00'], linewidths=[0.8, 1.0, 1.2, 1.5, 2.0], alpha=0.7)
    ax.clabel(cnt, inline=True, fmt=lambda x: f"{int(x/100)}m", fontsize=6, colors='#00FF00')
    
    plt.legend(handles=[rect_playable, rect_flat_north, rect_town, rect_lake, rect_sw_farmyard, rect_y1], loc='upper right', facecolor='black', labelcolor='white')
    plt.savefig(output_vis_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"   Saved full visualization to '{output_vis_path}'.")
    
    # --- Map 2: Zoomed-in Playable Area View ---
    print("   Generating detailed playable area visualization...")
    p_start = int(2048.0 / vis_scale)  # 170
    p_end = int(10240.0 / vis_scale)   # 853
    hs_detail = hs[p_start:p_end, p_start:p_end]
    
    fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
    ax.imshow(hs_detail, extent=[0, 8192, 8192, 0])
    
    # Grid and tick labels for 8km playable area relative coordinates
    ax.set_xlabel("X (East-West) [meters]", fontsize=12, fontweight='bold')
    ax.set_ylabel("Y (North-South) [meters]", fontsize=12, fontweight='bold')
    ax.set_xticks(np.arange(0, 8193, 1024))
    ax.set_yticks(np.arange(0, 8193, 1024))
    ax.grid(True, which='both', color='white', linestyle='--', linewidth=0.5, alpha=0.4)
    ax.tick_params(colors='white')
    # Dark theme styling
    fig.patch.set_facecolor('#111111')
    ax.set_facecolor('#111111')
    for spine in ax.spines.values():
        spine.set_color('white')
    ax.yaxis.label.set_color('white')
    ax.xaxis.label.set_color('white')
    ax.title.set_color('white')
    
    ax.set_title("Detailed Playable Area (8km x 8km Grid)", fontsize=16, fontweight='bold', pad=15)
    
    rect_flat_north_det = plt.Rectangle((0.0, 0.0), 8192.0, (ry1 - offset_m),
                                         fill=False, edgecolor='yellow', linewidth=2.5, linestyle=':')
    ax.add_patch(rect_flat_north_det)
    ax.text(100, (ry1 - offset_m) - 80, "FLAT NORTH PLAIN (1.5km)", color='yellow', fontsize=10, fontweight='bold')
    ax.set_xlim(0, 8192)
    ax.set_ylim(8192, 0)
    
    rect_town_det = plt.Rectangle((t_x0 - offset_m, t_y0 - offset_m), (t_x1 - t_x0), (t_y1 - t_y0),
                                  fill=False, edgecolor='cyan', linewidth=2.0, linestyle='-')
    rect_lake_det = plt.Rectangle((lake_x0 - offset_m, lake_y0 - offset_m), (lake_x1 - lake_x0), (lake_y1 - lake_y0),
                                  fill=False, edgecolor='blue', linewidth=2.0, linestyle='-')
    rect_sw_farmyard_det = plt.Rectangle((sw_x0 - offset_m, sw_y0 - offset_m), (sw_x1 - sw_x0), (sw_y1 - sw_y0),
                                         fill=False, edgecolor='magenta', linewidth=2.0, linestyle='-')
    rect_y1_det = plt.Rectangle((y1_x0 - offset_m, y1_y0 - offset_m), (y1_x1 - y1_x0), (y1_y1 - y1_y0),
                                fill=False, edgecolor='orange', linewidth=2.0, linestyle='-')
    ax.add_patch(rect_town_det)
    ax.add_patch(rect_lake_det)
    ax.add_patch(rect_sw_farmyard_det)
    ax.add_patch(rect_y1_det)
    ax.text((t_x0 - offset_m) + 10, (t_y0 - offset_m) - 10, "Town (65 ha)", color='cyan', fontsize=8, fontweight='bold')
    ax.text((lake_x0 - offset_m) + 5, (lake_y0 - offset_m) + 100, "Lake\n(15m Depth)", color='blue', fontsize=8, fontweight='bold')
    ax.text((sw_x0 - offset_m) + 50, (sw_y0 - offset_m) + 200, "SW Farmyard\n(100 ha)", color='magenta', fontsize=9, fontweight='bold')
    ax.text((y1_x0 - offset_m) + 30, (y1_y0 - offset_m) + 200, "Yard Y1\n(50 ha)", color='orange', fontsize=9, fontweight='bold')
    
    # Draw natural mountain shape contour lines for playable area
    x_range = np.arange(p_end - p_start) * 12.0
    x_grid_vis_playable, y_grid_vis_playable = np.meshgrid(x_range, x_range)
    terrain_vis_playable = terrain_vis[p_start:p_end, p_start:p_end]
    cnt = ax.contour(x_grid_vis_playable, y_grid_vis_playable, terrain_vis_playable, 
                     levels=[5000.0, 10000.0, 15000.0, 20000.0, 25000.0], colors=['#00FF00'], linewidths=[0.8, 1.0, 1.2, 1.5, 2.0], alpha=0.8)
    ax.clabel(cnt, inline=True, fmt=lambda x: f"{int(x/100)}m", fontsize=8, colors='#00FF00')
    
    plt.savefig(output_detail_vis_path, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    print(f"   Saved detailed visualization to '{output_detail_vis_path}'.")
    
    t_end = time.time()
    print(f"\n=== Script Completed Successfully in {t_end - t_start:.2f} seconds ===")
    print(f"Output files:")
    print(f" - New Heightmap: {output_dem_path}")
    print(f" - Full Map Visual: {output_vis_path}")
    print(f" - Detailed Visual: {output_detail_vis_path}")

if __name__ == "__main__":
    main()
