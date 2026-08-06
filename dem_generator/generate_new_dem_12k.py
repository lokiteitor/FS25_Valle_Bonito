import os
import time
import math
import numpy as np
from PIL import Image
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
    binary_mountain = (data_base > 5000.0) & is_in_central_y
    M_mountain = gaussian_filter(binary_mountain.astype(np.float32), sigma=40.0 * scale_m_to_px)
    
    H_plain = 3500.0
    terrain_inside = M_mountain * data_mountain_scaled + (1.0 - M_mountain) * (H_plain + noise_plain)
    
    print("4.1. Implementing flat northern plain (1.5 km wide: Y_playable [0, 1500] meters)...")
    # Flat northern plain from northern playable border towards south (Y_absolute in [2048, 3548])
    rx0, rx1 = 2048.0, 10240.0
    ry0, ry1 = 2048.0, 3548.0
    
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
    
    print("4.2. Implementing lake (600m x 1200m), farmyard (5 ha) and town (20 ha) at the East...")
    # Lake: 600m width x 1200m height, starts 15m from East playable border
    # X: [10240 - 15 - 600, 10240 - 15] = [9625, 10225]
    # Y: [2048 + 1000, 2048 + 1000 + 1200] = [3048, 4248] (1 km from North playable border)
    lake_x0, lake_x1 = 10240.0 - 15.0 - 600.0, 10240.0 - 15.0
    lake_y0, lake_y1 = 2048.0 + 1000.0, 2048.0 + 1000.0 + 1200.0
    H_lake_floor = 2000.0  # 15m depth below 35m surface

    # Farmyard: 5 ha = 100m width x 500m height, starts to the West of lake with 20m gap
    # X: [9625 - 20 - 100, 9625 - 20] = [9505, 9605]
    # Y: [3048, 3548] (1 km from North playable border)
    fy_x0, fy_x1 = lake_x0 - 20.0 - 100.0, lake_x0 - 20.0
    fy_y0, fy_y1 = lake_y0, lake_y0 + 500.0
    
    # Town: 20 ha = 400m width x 500m height, starts to the West of farmyard with 20m gap
    # X: [9505 - 20 - 400, 9505 - 20] = [9085, 9485]
    # Y: [3048, 3548]
    t_x0, t_x1 = fy_x0 - 20.0 - 400.0, fy_x0 - 20.0
    t_y0, t_y1 = fy_y0, fy_y1
    
    # Explicitly set height values to H_plain (3500.0) in these zones inside terrain_inside
    terrain_inside[int(fy_y0):int(fy_y1), int(fy_x0):int(fy_x1)] = 3500.0
    terrain_inside[int(t_y0):int(t_y1), int(t_x0):int(t_x1)] = 3500.0

    print("4.4. Implementing South-East farmyard (50 ha = 1000m x 500m)...")
    # SE Farmyard: 50 ha = 1000m width x 500m height, starts 15m from East and South playable borders in-game
    # (Since FS25 inverts X-axis in-game, East in-game is West/low X in image coordinates)
    # X: [2048 + 15, 2048 + 15 + 1000] = [2063, 3063]
    # Y: [10240 - 15 - 500, 10240 - 15] = [9725, 10225]
    se_x0, se_x1 = 2063.0, 3063.0
    se_y0, se_y1 = 9725.0, 10225.0
    
    # Calculate target height dynamically as median of this region
    sub_se = terrain_inside[int(se_y0):int(se_y1), int(se_x0):int(se_x1)]
    H_se_target = np.median(sub_se)
    print(f"   SE Farmyard target height: {H_se_target:.1f}")
    
    # Flatten with 100m smooth transition
    margin_se = 100.0
    bx0 = max(0, int(se_x0 - margin_se - 5))
    bx1 = min(S_px-1, int(se_x1 + margin_se + 5))
    by0 = max(0, int(se_y0 - margin_se - 5))
    by1 = min(S_px-1, int(se_y1 + margin_se + 5))
    
    terrain_ref = terrain_inside.copy()
    
    ny = by1 - by0 + 1
    nx = bx1 - bx0 + 1
    local_ramp = np.zeros((ny, nx), dtype=bool)
    
    for y_offset, y in enumerate(range(by0, by1 + 1)):
        for x_offset, x in enumerate(range(bx0, bx1 + 1)):
            dx_pt = max(0.0, se_x0 - x, x - se_x1)
            dy_pt = max(0.0, se_y0 - y, y - se_y1)
            d = np.sqrt(dx_pt*dx_pt + dy_pt*dy_pt)
            
            if d == 0:
                terrain_inside[y, x] = H_se_target
            elif d <= margin_se:
                w = 0.5 * (1.0 + np.cos(np.pi * d / margin_se))
                terrain_inside[y, x] = w * H_se_target + (1.0 - w) * terrain_ref[y, x]
                local_ramp[y_offset, x_offset] = True
                
    # Local Gaussian smoothing specifically to the transition ramp
    local_terrain = terrain_inside[by0:by1+1, bx0:bx1+1].copy()
    local_smoothed = gaussian_filter(local_terrain, sigma=10.0 * scale_m_to_px)
    for y_offset, y in enumerate(range(by0, by1 + 1)):
        for x_offset, x in enumerate(range(bx0, bx1 + 1)):
            if local_ramp[y_offset, x_offset]:
                terrain_inside[y, x] = local_smoothed[y_offset, x_offset]

    print("5. Integrating playable area with outside borders...")
    dx_play = np.maximum(0.0, np.maximum(2048.0 - x_m, x_m - 10240.0))
    dy_play = np.maximum(0.0, np.maximum(2048.0 - y_m, y_m - 10240.0))
    dist_play = np.sqrt(dx_play*dx_play + dy_play*dy_play)

    W_TRANS = 500.0
    w_playable = np.zeros_like(dist_play)
    w_playable[dist_play == 0] = 1.0
    trans_mask = (dist_play > 0) & (dist_play <= W_TRANS)
    t = dist_play[trans_mask] / W_TRANS
    w_playable[trans_mask] = 0.5 * (1.0 + np.cos(np.pi * t))
    
    terrain = w_playable * terrain_inside + (1.0 - w_playable) * data_mountain_scaled
    
    print("5.1. Excavating lake...")
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
    
    rect_farmyard = plt.Rectangle((fy_x0, fy_y0), (fy_x1 - fy_x0), (fy_y1 - fy_y0),
                                  fill=False, edgecolor='green', linewidth=1.5, linestyle='-', label='Farmyard (5 ha)')
    rect_town = plt.Rectangle((t_x0, t_y0), (t_x1 - t_x0), (t_y1 - t_y0),
                              fill=False, edgecolor='cyan', linewidth=1.5, linestyle='-', label='Town (20 ha)')
    rect_lake = plt.Rectangle((lake_x0, lake_y0), (lake_x1 - lake_x0), (lake_y1 - lake_y0),
                              fill=False, edgecolor='blue', linewidth=1.5, linestyle='-', label='Lake (600x1200m)')
    rect_se_farmyard = plt.Rectangle((se_x0, se_y0), (se_x1 - se_x0), (se_y1 - se_y0),
                                     fill=False, edgecolor='magenta', linewidth=1.5, linestyle='-', label='SE Farmyard (50 ha)')
    ax.add_patch(rect_farmyard)
    ax.add_patch(rect_town)
    ax.add_patch(rect_lake)
    ax.add_patch(rect_se_farmyard)
    
    # Draw natural mountain shape contour lines at 150m, 200m, 250m, 300m, 350m elevation
    x_range_all = np.arange(1024) * 12.0
    x_grid_vis, y_grid_vis = np.meshgrid(x_range_all, x_range_all)
    cnt = ax.contour(x_grid_vis, y_grid_vis, terrain_vis, levels=[15000.0, 20000.0, 25000.0, 30000.0, 35000.0], colors=['#00FF00'], linewidths=[1.0, 1.0, 1.5, 2.0, 2.5], alpha=0.7)
    ax.clabel(cnt, inline=True, fmt=lambda x: f"{int(x/100)}m", fontsize=6, colors='#00FF00')
    
    plt.legend(handles=[rect_playable, rect_flat_north, rect_farmyard, rect_town, rect_lake, rect_se_farmyard], loc='upper right', facecolor='black', labelcolor='white')
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
    
    rect_flat_north_det = plt.Rectangle((rx0 - offset_m, ry0 - offset_m), (rx1 - rx0), (ry1 - ry0),
                                         fill=False, edgecolor='yellow', linewidth=2.5, linestyle=':')
    ax.add_patch(rect_flat_north_det)
    ax.text(100, (ry1 - offset_m) - 80, "FLAT NORTH PLAIN (1.5km)", color='yellow', fontsize=10, fontweight='bold')
    
    rect_farmyard_det = plt.Rectangle((fy_x0 - offset_m, fy_y0 - offset_m), (fy_x1 - fy_x0), (fy_y1 - fy_y0),
                                      fill=False, edgecolor='green', linewidth=2.0, linestyle='-')
    rect_town_det = plt.Rectangle((t_x0 - offset_m, t_y0 - offset_m), (t_x1 - t_x0), (t_y1 - t_y0),
                                  fill=False, edgecolor='cyan', linewidth=2.0, linestyle='-')
    rect_lake_det = plt.Rectangle((lake_x0 - offset_m, lake_y0 - offset_m), (lake_x1 - lake_x0), (lake_y1 - lake_y0),
                                  fill=False, edgecolor='blue', linewidth=2.0, linestyle='-')
    rect_se_farmyard_det = plt.Rectangle((se_x0 - offset_m, se_y0 - offset_m), (se_x1 - se_x0), (se_y1 - se_y0),
                                         fill=False, edgecolor='magenta', linewidth=2.0, linestyle='-')
    ax.add_patch(rect_farmyard_det)
    ax.add_patch(rect_town_det)
    ax.add_patch(rect_lake_det)
    ax.add_patch(rect_se_farmyard_det)
    ax.text((fy_x0 - offset_m) + 5, (fy_y0 - offset_m) - 10, "Farmyard\n(5 ha)", color='green', fontsize=8, fontweight='bold')
    ax.text((t_x0 - offset_m) + 10, (t_y0 - offset_m) - 10, "Town (20 ha)", color='cyan', fontsize=8, fontweight='bold')
    ax.text((lake_x0 - offset_m) + 5, (lake_y0 - offset_m) + 100, "Lake\n(15m Depth)", color='blue', fontsize=8, fontweight='bold')
    ax.text((se_x0 - offset_m) + 50, (se_y0 - offset_m) + 200, "SE Farmyard\n(50 ha)", color='magenta', fontsize=9, fontweight='bold')
    
    # Draw natural mountain shape contour lines for playable area
    x_range = np.arange(p_end - p_start) * 12.0
    x_grid_vis_playable, y_grid_vis_playable = np.meshgrid(x_range, x_range)
    terrain_vis_playable = terrain_vis[p_start:p_end, p_start:p_end]
    cnt = ax.contour(x_grid_vis_playable, y_grid_vis_playable, terrain_vis_playable, 
                     levels=[15000.0, 20000.0, 25000.0, 30000.0, 35000.0], colors=['#00FF00'], linewidths=[1.0, 1.0, 1.5, 2.0, 2.5], alpha=0.8)
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
