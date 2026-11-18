"""
Builds the template grid and writes rasters.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import geopandas
import numpy
import pandas
import rasterio
from numpy.typing import NDArray
from rasterio import features
from rasterio import transform as rasterio_transform

REPROJECTED_CRS = "EPSG:5070"
CELL_SIZE = 1000

# the conterminous US
HUC2S = tuple(str(huc2) for huc2 in range(1, 18 + 1))

TEMPLATE_PATH = Path("output", f"raster_{CELL_SIZE}.tif")


def build_template() -> None:
    """
    Writes the template raster to TEMPLATE_PATH from the HUC-2 boundaries in input/HUC/<huc2>/Shape/WBDHU2.shp.

    Keeps an existing template that is newer than all HUC-2 boundary files.
    """

    boundary_paths = [Path("input", "HUC", huc2, "Shape", "WBDHU2.shp") for huc2 in HUC2S]
    if not is_newer(TEMPLATE_PATH, boundary_paths):
        templatize_geometries(boundary_paths, REPROJECTED_CRS, CELL_SIZE, TEMPLATE_PATH)


def is_newer(path: Path, source_paths: Sequence[Path]) -> bool:
    """
    Tells if a file exists and is newer than all source files.

    Parameters:
        path (Path): The path to the file.
        source_paths (Sequence[Path]): The paths to the files it is built from. Each must exist.

    Returns:
        newer (bool): True if path exists and its modification time is later than that of each source file.
    """

    return path.exists() and all(path.stat().st_mtime > source_path.stat().st_mtime for source_path in source_paths)


def templatize_geometries(
    geometry_paths: Sequence[Path], reprojected_crs: str, cell_size: int, output_path: Path
) -> None:
    """
    Rasterizes the combined extent of the provided geometries into a binary template raster.

    The grid starts at the top-left corner of the geometries' bounding box and holds only whole cells. It can stop less
    than one cell short of the east and south bounds. Cells touching a geometry hold 1, and other cells hold 0 (nodata).
    An interrupted write can't leave a partial raster at output_path (see write_raster).

    Parameters:
        geometry_paths (Sequence[Path]): The paths to the geometry files.
        reprojected_crs (str): The target CRS for the produced template.
        cell_size (int): The target cell size (in CRS units) for the produced template.
        output_path (Path): The path to the output file.
    """

    geometries = pandas.concat([geopandas.read_file(geometry_path) for geometry_path in geometry_paths])
    geometries = geopandas.GeoDataFrame(geometries, geometry="geometry")
    geometries.to_crs(reprojected_crs, inplace=True)

    min_x, min_y, max_x, max_y = geometries.total_bounds
    raster_width = int(abs(max_x - min_x) / cell_size)
    raster_height = int(abs(max_y - min_y) / cell_size)

    new_transform = rasterio_transform.from_origin(min_x, max_y, cell_size, cell_size)

    raster: NDArray[numpy.uint8] = features.rasterize(
        [(geometry, 1) for geometry in geometries.geometry],
        (raster_height, raster_width),
        nodata=0,
        transform=new_transform,
        all_touched=True,
        dtype=numpy.uint8,
    )
    raster_metadata: dict[str, Any] = {
        "count": 1,
        "crs": geometries.crs,
        "driver": "GTiff",
        "dtype": numpy.uint8,
        "height": raster_height,
        "nodata": 0,
        "transform": new_transform,
        "width": raster_width,
    }

    write_raster(output_path, raster, raster_metadata)


def write_raster(output_path: Path, data: NDArray[Any], profile: dict[str, Any]) -> None:
    """
    Writes a single-band raster to a temporary file in the same directory, then renames it to output_path.

    is_newer accepts any file that exists, so a raster must appear only when complete.

    Parameters:
        output_path (Path): The path to the output file.
        data (NDArray[Any]): The (height, width) values.
        profile (dict[str, Any]): The rasterio profile of the output file.
    """

    # the process ID keeps concurrent runs from sharing a temporary file
    temporary_path = Path(f"{output_path}.{os.getpid()}.tmp")
    with rasterio.open(temporary_path, "w", **profile) as output_raster:
        output_raster.write(data, 1)
    _ = temporary_path.replace(output_path)
