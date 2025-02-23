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
import matplotlib as mpl

UPLOAD_DIR = "uploaded_lidar_data"
os.makedirs(UPLOAD_DIR, exist_ok=True)

st.title("LiDAR-Based RF Attenuation Viewer")

# 1. Multi-File Upload Section: (no file list displayed below)
uploaded_files = st.file_uploader(
    "Upload LAZ LiDAR file(s)", 
    type=["laz"], 
    accept_multiple_files=True
)
if uploaded_files:
    for uf in uploaded_files:
        file_path = os.path.join(UPLOAD_DIR, uf.name)
        with open(file_path, "wb") as f:
            f.write(uf.getbuffer())
    st.success("Files uploaded successfully!")

# 2. Collect existing LAZ files from upload directory
lidar_files = sorted([f for f in os.listdir(UPLOAD_DIR) if f.lower().endswith(".laz")])
if len(lidar_files) == 0:
    st.warning("No LAZ files have been uploaded yet.")
    st.stop()

@st.cache_data
def load_and_compute_chm(lidar_path, resolution=50):
    """Load LAZ, transform to EPSG:3857, compute CHM (DSM-DTM)."""
    with laspy.open(lidar_path) as las:
        points = las.read()

    x = np.array(points.x)
    y = np.array(points.y)
    z = np.array(points.z)
    classification = np.array(points.classification)

    # Convert from EPSG:3067 -> EPSG:4326
    transformer_to_wgs84 = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
    lon, lat = transformer_to_wgs84.transform(x, y)

    # Then from EPSG:4326 -> EPSG:3857
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
        x_merc,
        y_merc,
        bins=resolution,
        weights=z,
        density=True
    )

    # CHM
    chm = np.clip(dsm - dtm, 0, None)
    return chm, x_edges, y_edges

def compute_tile_path_length_km(x_edges, y_edges):
    """Approx. path length by diagonal of average cell, in kilometers."""
    dx_m = (x_edges[-1] - x_edges[0]) / (len(x_edges) - 1)
    dy_m = (y_edges[-1] - y_edges[0]) / (len(y_edges) - 1)
    diagonal_m = np.sqrt(dx_m**2 + dy_m**2)
    return diagonal_m / 1000.0  # meters -> km

def itu_r_p833(canopy_height, freq_ghz, path_length_km=1.0):
    """Simple ITU-based attenuation model (dB)."""
    A0 = 0.2 * (freq_ghz ** 0.3) * (canopy_height ** 0.6)
    return A0 * path_length_km  # in dB

def create_colorized_overlay(att_map, x_edges, y_edges, vmin, vmax, cmap):
    """
    Convert a 2D attenuation map into a colorized RGBA image.
    Returns (PIL.Image, (lat_min, lat_max, lon_min, lon_max)).
    """
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    colormapped = cmap(norm(att_map))  # shape => (nx, ny, 4)

    # Fix orientation: transpose, then flip vertically
    colormapped_t = colormapped.transpose((1, 0, 2))
    colormapped_t = np.flipud(colormapped_t)

    # Per-pixel alpha to make low attenuation more transparent
    alpha_vals = norm(att_map).transpose()
    alpha_vals = np.flipud(alpha_vals)
    alpha_scaled = 0.1 + 0.9 * alpha_vals
    colormapped_t[..., 3] = alpha_scaled

    colormapped_8bit = (colormapped_t * 255).astype(np.uint8)
    img = Image.fromarray(colormapped_8bit, mode="RGBA")

    # Convert bounding box from EPSG:3857 -> EPSG:4326
    transformer_to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    minx, maxx = x_edges[0], x_edges[-1]
    miny, maxy = y_edges[0], y_edges[-1]
    bottom_left = transformer_to_wgs84.transform(minx, miny)  # (lon, lat)
    top_right   = transformer_to_wgs84.transform(maxx, maxy)

    lat_min, lon_min = bottom_left[1], bottom_left[0]
    lat_max, lon_max = top_right[1], top_right[0]

    return img, (lat_min, lat_max, lon_min, lon_max)

# --- Main Logic ---
freq_ghz = st.sidebar.slider("Frequency (GHz)", 0.1, 10.0, 2.4, 0.1)

maps_data = []
all_vals = []

for fname in lidar_files:
    path = os.path.join(UPLOAD_DIR, fname)
    chm, x_edges, y_edges = load_and_compute_chm(path)
    path_length_km = compute_tile_path_length_km(x_edges, y_edges)
    att_map = itu_r_p833(chm, freq_ghz, path_length_km)
    maps_data.append((att_map, x_edges, y_edges))
    all_vals.append(att_map.ravel())

# Determine global color scale
all_vals_flat = np.concatenate(all_vals)
all_vals_flat = all_vals_flat[~np.isnan(all_vals_flat)]
if len(all_vals_flat) == 0:
    st.warning("No valid attenuation data found.")
    st.stop()

global_min, global_max = float(all_vals_flat.min()), float(all_vals_flat.max())
cmap = plt.get_cmap("hot")
norm = mcolors.Normalize(vmin=global_min, vmax=global_max)

# Build a small colorbar figure
fig, ax = plt.subplots(figsize=(0.5, 3))
sm = mpl.cm.ScalarMappable(norm=norm, cmap=cmap)
cbar = fig.colorbar(sm, cax=ax, orientation="vertical")
cbar.set_label("Attenuation [dB]", rotation=90)

# Center the map on the first file
sample_chm, sx_edges, sy_edges = load_and_compute_chm(os.path.join(UPLOAD_DIR, lidar_files[0]))
cx = (sx_edges[0] + sx_edges[-1]) / 2
cy = (sy_edges[0] + sy_edges[-1]) / 2
transform_merc2ll = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
center_lon, center_lat = transform_merc2ll.transform(cx, cy)
m = folium.Map(location=[center_lat, center_lon], zoom_start=14)

# Overlay each tile on the Folium map
for (att_map, x_edges, y_edges) in maps_data:
    img, (lat_min, lat_max, lon_min, lon_max) = create_colorized_overlay(
        att_map, x_edges, y_edges, global_min, global_max, cmap
    )
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    data_uri = "data:image/png;base64," + b64encode(buffer.getvalue()).decode("utf-8")

    bounds = [[lat_min, lon_min], [lat_max, lon_max]]
    folium.raster_layers.ImageOverlay(
        image=data_uri,
        bounds=bounds,
        opacity=1.0,    # rely on per-pixel alpha
        origin="upper",
        interactive=False,
        cross_origin=False,
    ).add_to(m)

# Layout: map + colorbar side-by-side
col1, col2 = st.columns([4, 1])

with col1:
    st_folium(m, width=600, height=500)

with col2:
    st.pyplot(fig)
    
