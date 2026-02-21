#!/usr/bin/env python3
import argparse
import json
import math
import os
from pathlib import Path
from multiprocessing import Pool, cpu_count

import laspy
import numpy as np
from pyproj import Transformer


def iter_chunks(lidar_path, chunk_size):
    with laspy.open(lidar_path) as las:
        for points in las.chunk_iterator(chunk_size):
            yield points


def _bounds_for_file(args):
    path, chunk_size, source_crs = args
    transformer = Transformer.from_crs(source_crs, "EPSG:3857", always_xy=True)
    minx = math.inf
    miny = math.inf
    maxx = -math.inf
    maxy = -math.inf
    for points in iter_chunks(path, chunk_size):
        x = np.asarray(points.x)
        y = np.asarray(points.y)
        x_merc, y_merc = transformer.transform(x, y)
        minx = min(minx, float(np.min(x_merc)))
        miny = min(miny, float(np.min(y_merc)))
        maxx = max(maxx, float(np.max(x_merc)))
        maxy = max(maxy, float(np.max(y_merc)))
    return (minx, miny, maxx, maxy)


def compute_bounds_parallel(lidar_paths, chunk_size, source_crs, workers):
    minx = math.inf
    miny = math.inf
    maxx = -math.inf
    maxy = -math.inf
    tasks = [(p, chunk_size, source_crs) for p in lidar_paths]

    with Pool(processes=workers) as pool:
        total = len(tasks)
        done = 0
        for bx, by, Bx, By in pool.imap_unordered(_bounds_for_file, tasks):
            done += 1
            print(f"[bounds] {done}/{total} files processed")
            minx = min(minx, bx)
            miny = min(miny, by)
            maxx = max(maxx, Bx)
            maxy = max(maxy, By)

    if not np.isfinite([minx, miny, maxx, maxy]).all():
        raise RuntimeError("No valid points found in input files.")

    return minx, miny, maxx, maxy


def add_histogram(acc_sum, acc_cnt, x, y, z, x_edges, y_edges):
    h_sum, _, _ = np.histogram2d(x, y, bins=[x_edges, y_edges], weights=z)
    h_cnt, _, _ = np.histogram2d(x, y, bins=[x_edges, y_edges])
    acc_sum += h_sum
    acc_cnt += h_cnt


def _grids_for_file(args):
    path, resolution, chunk_size, source_crs, x_edges, y_edges = args
    transformer = Transformer.from_crs(source_crs, "EPSG:3857", always_xy=True)
    dsm_sum = np.zeros((resolution, resolution), dtype=np.float64)
    dsm_cnt = np.zeros((resolution, resolution), dtype=np.float64)
    dtm_sum = np.zeros((resolution, resolution), dtype=np.float64)
    dtm_cnt = np.zeros((resolution, resolution), dtype=np.float64)

    for points in iter_chunks(path, chunk_size):
        x = np.asarray(points.x)
        y = np.asarray(points.y)
        z = np.asarray(points.z)
        cls = np.asarray(points.classification)

        x_merc, y_merc = transformer.transform(x, y)
        add_histogram(dsm_sum, dsm_cnt, x_merc, y_merc, z, x_edges, y_edges)

        ground_mask = cls == 2
        if np.any(ground_mask):
            add_histogram(
                dtm_sum,
                dtm_cnt,
                x_merc[ground_mask],
                y_merc[ground_mask],
                z[ground_mask],
                x_edges,
                y_edges,
            )

    return dsm_sum, dsm_cnt, dtm_sum, dtm_cnt


def compute_grids_parallel(lidar_paths, resolution, chunk_size, source_crs, x_edges, y_edges, workers):
    dsm_sum = np.zeros((resolution, resolution), dtype=np.float64)
    dsm_cnt = np.zeros((resolution, resolution), dtype=np.float64)
    dtm_sum = np.zeros((resolution, resolution), dtype=np.float64)
    dtm_cnt = np.zeros((resolution, resolution), dtype=np.float64)

    tasks = [(p, resolution, chunk_size, source_crs, x_edges, y_edges) for p in lidar_paths]
    with Pool(processes=workers) as pool:
        total = len(tasks)
        done = 0
        for ds, dc, ts, tc in pool.imap_unordered(_grids_for_file, tasks):
            done += 1
            print(f"[grids] {done}/{total} files processed")
            dsm_sum += ds
            dsm_cnt += dc
            dtm_sum += ts
            dtm_cnt += tc

    dsm = np.divide(dsm_sum, dsm_cnt, out=np.zeros_like(dsm_sum), where=dsm_cnt > 0)
    dtm = np.divide(dtm_sum, dtm_cnt, out=np.zeros_like(dtm_sum), where=dtm_cnt > 0)
    chm = np.clip(dsm - dtm, 0, None)

    valid = (dsm_cnt > 0) & (dtm_cnt > 0)
    return chm, dtm, valid


def main():
    parser = argparse.ArgumentParser(description="Preprocess LAZ files into compact CHM grid.")
    parser.add_argument("--input", default="uploaded_lidar_data", help="Directory with .laz files")
    parser.add_argument("--output", default="web/data", help="Output directory")
    parser.add_argument("--resolution", type=int, default=1024, help="Grid resolution (NxN)")
    parser.add_argument("--chunk-size", type=int, default=5_000_000, help="Points per chunk")
    parser.add_argument("--source-crs", default="EPSG:3067", help="Source CRS of LAZ files")
    parser.add_argument("--workers", type=int, default=max(1, cpu_count() - 1), help="Parallel workers")

    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    lidar_paths = sorted([p for p in input_dir.iterdir() if p.suffix.lower() == ".laz"])
    if not lidar_paths:
        raise SystemExit(f"No .laz files found in {input_dir}")

    print(f"Using {args.workers} workers")

    minx, miny, maxx, maxy = compute_bounds_parallel(
        lidar_paths, args.chunk_size, args.source_crs, args.workers
    )

    x_edges = np.linspace(minx, maxx, args.resolution + 1)
    y_edges = np.linspace(miny, maxy, args.resolution + 1)

    chm, dtm, valid = compute_grids_parallel(
        lidar_paths,
        args.resolution,
        args.chunk_size,
        args.source_crs,
        x_edges,
        y_edges,
        args.workers,
    )

    # Convert to image order: row 0 = maxy, col 0 = minx
    chm_img = np.flipud(chm.T)
    dtm_img = np.flipud(dtm.T)
    valid_img = np.flipud(valid.T)

    min_chm = float(np.min(chm_img))
    max_chm = float(np.max(chm_img))
    if max_chm <= 0:
        chm_scale = 1.0
    else:
        chm_scale = max_chm / 65535.0

    chm_q = np.clip(np.round(chm_img / chm_scale), 0, 65535).astype(np.uint16)

    bin_path = output_dir / "chm_u16.bin"
    chm_q.tofile(bin_path)

    min_dtm = float(np.min(dtm_img))
    max_dtm = float(np.max(dtm_img))
    dtm_scale = (max_dtm - min_dtm) / 65535.0 if max_dtm > min_dtm else 1.0
    dtm_q = np.clip(np.round((dtm_img - min_dtm) / dtm_scale), 0, 65535).astype(np.uint16)
    dtm_path = output_dir / "dtm_u16.bin"
    dtm_q.tofile(dtm_path)

    mask_path = output_dir / "valid_u8.bin"
    valid_img.astype(np.uint8).tofile(mask_path)

    # Bounds in WGS84 for Leaflet
    to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    lon_min, lat_min = to_wgs84.transform(minx, miny)
    lon_max, lat_max = to_wgs84.transform(maxx, maxy)

    metadata = {
        "width": args.resolution,
        "height": args.resolution,
        "bounds": {
            "minx": minx,
            "miny": miny,
            "maxx": maxx,
            "maxy": maxy,
        },
        "bounds_wgs84": {
            "lat_min": lat_min,
            "lon_min": lon_min,
            "lat_max": lat_max,
            "lon_max": lon_max,
        },
        "crs": "EPSG:3857",
        "chm_scale": chm_scale,
        "chm_offset": 0.0,
        "chm_min_height": min_chm,
        "chm_max_height": max_chm,
        "dtm_scale": dtm_scale,
        "dtm_offset": min_dtm,
        "dtm_min_height": min_dtm,
        "dtm_max_height": max_dtm,
        "dx": (maxx - minx) / args.resolution,
        "dy": (maxy - miny) / args.resolution,
    }

    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    print(f"Wrote {bin_path}")
    print(f"Wrote {meta_path}")
    print(f"Wrote {dtm_path}")
    print(f"Wrote {mask_path}")


if __name__ == "__main__":
    main()
