"""
Spreads the monthly water use of each plant over the cells of its footprint.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas
import numpy
import pandas
import rasterio
import shapely
from numpy.typing import NDArray
from rasterio import mask
from rasterio import transform as rasterio_transform

from utilities import raster_utilities

# output file name part: estimates column
VARIABLES = {"consumption": "cu_mgd", "withdrawal": "wd_mgd"}

# lossless; predictor 3 is the GDAL predictor for floating-point data
COMPRESSION = {"compress": "deflate", "predictor": 3, "tiled": True}


@dataclass(frozen=True)
class CellWeights:
    """
    The land cells under one footprint, and the fraction of the footprint's area in each.

    Attributes:
        rows (NDArray[numpy.intp]): The template row of each cell.
        columns (NDArray[numpy.intp]): The template column of each cell.
        weights (NDArray[numpy.float64]): The fraction of the footprint's area in each cell. The sum is less than 1 when
            part of the footprint is outside the land cells.
    """

    rows: NDArray[numpy.intp]
    columns: NDArray[numpy.intp]
    weights: NDArray[numpy.float64]


def compute_cell_weights(footprints: geopandas.GeoDataFrame) -> dict[int, CellWeights]:
    """
    Finds the land cells of the template under each footprint, and the fraction of the footprint's area in each.

    A cell is under a footprint if the footprint touches it.

    Parameters:
        footprints (geopandas.GeoDataFrame): One footprint per plant, indexed by plant code.

    Returns:
        weights (dict[int, CellWeights]): The cell weights per plant code.
    """

    weights: dict[int, CellWeights] = {}
    with rasterio.open(raster_utilities.TEMPLATE_PATH) as template:
        projected = footprints.geometry.to_crs(template.crs)

        plant_codes: list[int] = projected.index.tolist()
        for plant_code, footprint in zip(plant_codes, projected, strict=True):
            masked, _ = mask.mask(template, [footprint], all_touched=True, nodata=0)
            rows, columns = numpy.nonzero(masked[0] != 0)

            min_x, min_y = rasterio_transform.xy(template.transform, rows, columns, offset="ul")
            max_x, max_y = rasterio_transform.xy(template.transform, rows + 1, columns + 1, offset="ul")
            cells = shapely.box(min_x, min_y, max_x, max_y)

            weights[plant_code] = CellWeights(
                rows, columns, shapely.area(shapely.intersection(footprint, cells)) / footprint.area
            )

    return weights


def run(estimates: pandas.DataFrame, weights: dict[int, CellWeights], years: Sequence[int]) -> None:
    """
    Writes the monthly and annual rasters of each variable in VARIABLES, in mgd, on the template grid.

    Each plant-month adds its water use times its cell weights to the month's raster. A plant-month with no positive
    consumption or withdrawal adds nothing. The annual raster is the day-weighted mean of the 12 monthly rasters.
    Land cells with no plant hold 0, and other cells hold NaN (nodata).

    Writes output/<year>-<month>-<variable>.tif with a 2-digit month, and output/<year>-annual-<variable>.tif, as float64
    GeoTIFFs with COMPRESSION.

    Parameters:
        estimates (pandas.DataFrame): The plant-months, as from plants.read_estimates.
        weights (dict[int, CellWeights]): The cell weights of each plant in estimates.
        years (Sequence[int]): The years to write.
    """

    with rasterio.open(raster_utilities.TEMPLATE_PATH) as template:
        land = template.read(1) == 1
        profile = template.meta | {"dtype": "float64", "nodata": numpy.nan} | COMPRESSION

    for year in years:
        annual = {variable: empty_raster(land) for variable in VARIABLES}

        for month in range(1, 12 + 1):
            monthly = {variable: empty_raster(land) for variable in VARIABLES}
            plant_months = estimates[(estimates["year"] == year) & (estimates["month"] == month)]

            for plant_code, *values in plant_months[["plant_code", *VARIABLES.values()]].itertuples(index=False):
                if max(values) <= 0:
                    continue

                cells = weights[plant_code]
                for raster, value in zip(monthly.values(), values, strict=True):
                    raster[cells.rows, cells.columns] += value * cells.weights

            days = calendar.monthrange(year, month)[1]
            for variable, raster in monthly.items():
                raster_utilities.write_raster(Path("output", f"{year}-{month:02d}-{variable}.tif"), raster, profile)
                annual[variable] += raster * days

        days_in_year = 366 if calendar.isleap(year) else 365
        for variable, raster in annual.items():
            raster /= days_in_year
            raster_utilities.write_raster(Path("output", f"{year}-annual-{variable}.tif"), raster, profile)


def empty_raster(land: NDArray[numpy.bool_]) -> NDArray[numpy.float64]:
    """
    Makes a raster with 0 on land cells and NaN elsewhere.

    Parameters:
        land (NDArray[numpy.bool_]): True on the land cells of the template.

    Returns:
        raster (NDArray[numpy.float64]): The raster, with the shape of land.
    """

    raster = numpy.full(land.shape, numpy.nan)
    raster[land] = 0.0
    return raster
