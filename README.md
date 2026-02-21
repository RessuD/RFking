# RFking
Modeling RF behavior in the wild

## Offline preprocessing + static web viewer

This adds a simple offline pipeline that turns LAZ LiDAR files into a compact canopy height grid and a static web app that computes area attenuation maps client-side.

### 1) Preprocess LiDAR into a compact grid

```bash
python3 scripts/preprocess_lidar.py \
  --input uploaded_lidar_data \
  --output web/data \
  --resolution 1024
```

This produces:
- `web/data/chm_u16.bin` (quantized canopy height grid)
- `web/data/dtm_u16.bin` (quantized terrain grid)
- `web/data/metadata.json`

### 2) Serve the static web app

Use any static server. For example:

```bash
python3 -m http.server 8000 --directory web
```

Then open `http://localhost:8000`.

### 3) Usage

- Click the map to compute an area attenuation map centered on that point.
- Change the frequency slider to recompute.
- Use the device picker and sensitivity inputs to generate coverage/detectability maps.
- Open `docs.html` from the app for model and defaults documentation.

## Notes
- Client-side attenuation uses the same model as the Streamlit version.
- Distance is planar in EPSG:3857 for speed.
