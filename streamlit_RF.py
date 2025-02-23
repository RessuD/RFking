import numpy as np
import laspy
import folium
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from pyproj import Transformer
import streamlit as st
from streamlit_folium import st_folium
from PIL import Image
from io import BytesIO
from base64 import b64encode
import os

UPLOAD_DIR = "uploaded_lidar_data"
os.makedirs(UPLOAD_DIR, exist_ok=True)

st.title("LiDAR-Based RF Attenuation Viewer")

# 1. Upload section
uploaded_file = st.file_uploader("Upload LAZ LiDAR file", type=["laz"])
if uploaded_file:
    file_path = os.path.join(UPLOAD_DIR, uploaded_file.name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    st.success(f"File {uploaded_file.name} uploaded successfully!")

# 2. Sidebar - list uploaded files
st.sidebar.header("Uploaded LAZ Files:")
lidar_files = sorted(
    [f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".laz")]
)
if len(lidar_files) == 0:
    st.sidebar.write("No files uploaded yet.")
    st.stop()
else:
    for fname in lidar_files:
        st.sidebar.write(f"• {fname}")

@st.cache_data
def load_and_compute_chm(lidar_path, resolution=50):
    """Return canopy height model (CHM) plus Mercator edges."""
    with laspy.open(lidar_path) as las:
        points = las.read()
    x = np.array(points.x)
    y = np.array(points.y)
    z = np.array(points.z)
    classification = np.array(points.classification)

    # Adjust if your LAZ uses a different CRS:
    transformer_to_wgs84 = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
    lon, lat = transformer_to_wgs84.transform(x, y)

    transformer_to_mercator = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x_merc, y_merc = transformer_to_mercator.transform(lon, lat)

    # Ground points => DTM
    ground_mask = (classification == 2)
    dtm, x_edges, y_edges = np.histogram2d(
        x_merc[ground_mask],
        y_merc[ground_mask],
        bins=resolution,
        weights=z[ground_mask],
        density=True
    )
    # DSM with all points
    dsm, _, _ = np.histogram2d(
        x_merc, y_merc,
        bins=resolution,
        weights=z,
        density=True
    )

    chm = np.clip(dsm - dtm, 0, None)
    return chm, x_edges, y_edges

def itu_r_p833(canopy_height, freq_ghz, path_km=1.0):
    """Simple ITU-based attenuation model."""
    A0 = 0.2 * (freq_ghz ** 0.3) * (canopy_height ** 0.6)
    return A0 * path_km

def create_colorized_overlay(att_map, x_edges, y_edges, vmin, vmax, cmap):
    """
    Convert a 2D attenuation map into a colorized RGBA image, then
    return (PIL.Image, (lat_min, lat_max, lon_min, lon_max)) for overlay.

    Key steps:
      - Transpose att_map to match image shape (height, width).
      - Flip vertically so it aligns properly (if needed).
      - Use per-pixel alpha: low attenuation => near 0 alpha, high => near 1.
    """
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    # Apply colormap => (nx, ny, 4) in [0..1]
    colormapped = cmap(norm(att_map))

    # 1) Transpose to (ny, nx, 4)
    colormapped_t = colormapped.transpose((1, 0, 2))

    # 2) Flip top-to-bottom if needed
    colormapped_t = np.flipud(colormapped_t)  # or colormapped_t[::-1, :, :]

    # 3) Adjust alpha channel for more transparency when values are low
    alpha_values = norm(att_map).transpose()
    alpha_values = np.flipud(alpha_values)          # flip alpha the same way
    alpha_scaled = 0.1 + 0.9 * alpha_values         # min alpha=0.1, max=1.0
    colormapped_t[..., 3] = alpha_scaled            # set per-pixel alpha

    # Convert RGBA [0..1] => [0..255]
    colormapped_8bit = (colormapped_t * 255).astype(np.uint8)

    # Convert to PIL Image
    img = Image.fromarray(colormapped_8bit, mode="RGBA")

    # Transform bounding box from EPSG:3857 -> EPSG:4326
    transformer_to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    minx, maxx = x_edges[0], x_edges[-1]
    miny, maxy = y_edges[0], y_edges[-1]

    bottom_left = transformer_to_wgs84.transform(minx, miny)  # (lon, lat)
    top_right   = transformer_to_wgs84.transform(maxx, maxy)

    lat_min, lon_min = bottom_left[1], bottom_left[0]
    lat_max, lon_max = top_right[1], top_right[0]
    return img, (lat_min, lat_max, lon_min, lon_max)

# --- MAIN section ---
freq_ghz = st.sidebar.slider("Frequency (GHz)", 0.1, 10.0, 2.4, 0.1)

maps_data = []
all_vals = []

for fname in lidar_files:
    lid_path = os.path.join(UPLOAD_DIR, fname)
    chm, x_edges, y_edges = load_and_compute_chm(lid_path)
    att_map = itu_r_p833(chm, freq_ghz)
    maps_data.append((att_map, x_edges, y_edges))
    all_vals.append(att_map.ravel())

all_vals_flat = np.concatenate(all_vals)
all_vals_flat = all_vals_flat[~np.isnan(all_vals_flat)]
if len(all_vals_flat) == 0:
    st.warning("No valid attenuation data found.")
    st.stop()

global_min, global_max = float(all_vals_flat.min()), float(all_vals_flat.max())
cmap = plt.get_cmap("hot")

# Center map on the first tile's bounding box
sample_chm, sx_edges, sy_edges = load_and_compute_chm(os.path.join(UPLOAD_DIR, lidar_files[0]))
cx = (sx_edges[0] + sx_edges[-1]) / 2
cy = (sy_edges[0] + sy_edges[-1]) / 2
transform_merc2ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
center_lon, center_lat = transform_merc2ll.transform(cx, cy)
m = folium.Map(location=[center_lat, center_lon], zoom_start=14)

# Add each tile as an overlay
for (att_map, x_edges, y_edges) in maps_data:
    img, (lat_min, lat_max, lon_min, lon_max) = create_colorized_overlay(
        att_map, x_edges, y_edges, global_min, global_max, cmap
    )

    # Convert image -> base64 data URI
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_bytes = buffer.getvalue()
    data_uri = "data:image/png;base64," + b64encode(img_bytes).decode("utf-8")

    bounds = [[lat_min, lon_min], [lat_max, lon_max]]
    folium.raster_layers.ImageOverlay(
        image=data_uri,
        bounds=bounds,
        # Use opacity=1 so the per-pixel alpha is visible
        opacity=1.0,
        origin="upper",
        interactive=False,
        cross_origin=False,
    ).add_to(m)

st_folium(m, width=700, height=500)