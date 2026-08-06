import numpy as np
from PIL import Image

def main():
    img_path = "dem_new_12k.png"
    img = Image.open(img_path)
    data = np.array(img, dtype=np.float32)
    
    offset_m = 2048.0
    
    # 1. Reservoir area
    rx0 = int(7758.0 + offset_m)
    rx1 = int(8118.0 + offset_m)
    ry0 = int(1176.0 + offset_m)
    ry1 = int(1536.0 + offset_m)
    
    res_sub = data[ry0:ry1+1, rx0:rx1+1]
    res_min_raw = res_sub.min()
    res_min_m = res_min_raw / 100.0
    
    # 2. Playable area SE quadrant (or full playable area)
    # Playable area bounds are x, y in [2048, 10240]
    px0 = int(2048)
    px1 = int(10240)
    py0 = int(2048)
    py1 = int(10240)
    
    playable_sub = data[py0:py1+1, px0:px1+1]
    max_idx = np.unravel_index(np.argmax(playable_sub), playable_sub.shape)
    
    max_y_rel = max_idx[0]
    max_x_rel = max_idx[1]
    max_y_abs = py0 + max_y_rel
    max_x_abs = px0 + max_x_rel
    
    max_raw = playable_sub[max_idx]
    max_m = max_raw / 100.0
    
    diff_raw = max_raw - res_min_raw
    diff_m = diff_raw / 100.0
    
    print(f"Reservoir Bottom Minimum Height:")
    print(f"  Raw value: {res_min_raw:.2f}")
    print(f"  In meters: {res_min_m:.2f} m")
    print(f"Playable Area Maximum Height (Peak):")
    print(f"  Raw value: {max_raw:.2f}")
    print(f"  In meters: {max_m:.2f} m")
    print(f"  Location (X, Y) relative to playable: ({max_x_abs - offset_m:.1f}, {max_y_abs - offset_m:.1f})")
    print(f"  Location (X, Y) absolute: ({max_x_abs:.1f}, {max_y_abs:.1f})")
    print(f"Height Difference:")
    print(f"  Raw units difference: {diff_raw:.2f}")
    print(f"  In meters: {diff_m:.2f} m")

if __name__ == "__main__":
    main()
