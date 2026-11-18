"""
Runs the spatial disaggregation of thermoelectric power plant water use.

The pipeline reads the monthly consumption and withdrawal of each plant, in million gallons per day (mgd). It writes
monthly and annual rasters of water use per grid cell, in mgd. Run this script from the repository root, because all
paths are relative to the root.

Terms:
    plant code: The EIA plant ID. The USGS estimates and OpenStreetMap (OSM) use it too.
    plant-month: The consumption and withdrawal of one plant in one month, from the USGS estimates.
    footprint: The area of a plant: the union of its OSM features or, for a plant with none, a circle with the median
        footprint area.
    template grid: The 1000 m EPSG:5070 grid of all rasters. Its land cells are those that touch a HUC-2 region 1-18.
    cell weight: The fraction of a footprint's area in one land cell of the template grid.

Stages:
    1. Template: Builds the template raster from the HUC-2 boundaries. All inputs must be in input/ before the run (see
       README.md).
    2. Footprints: Finds the footprint of each plant through the Overpass API, or reads them from an earlier run.
    3. Disaggregation: Spreads each plant-month over its cells in proportion to the cell weights. Water use on the part
       of a footprint outside the land cells is lost.
    4. Evaluation: Compares each plant-month with the sum of its cells.
"""

import argparse
import time
from collections.abc import Sequence

import disaggregation
import evaluation
import footprints
import plants
from utilities import raster_utilities


def main(years: Sequence[int] = plants.YEARS) -> None:
    """
    Runs all stages.

    Writes the template raster, the footprints, the rasters and the metrics to output/ (see the run function of each
    stage).

    Parameters:
        years (Sequence[int] = plants.YEARS): The years to write rasters for and to evaluate. Each must be in
            plants.YEARS.
    """

    start_time = time.perf_counter()

    raster_utilities.build_template()

    estimates = plants.read_estimates()
    plant_footprints = footprints.run(estimates["plant_code"].unique().tolist(), plants.read_plant_locations())
    weights = disaggregation.compute_cell_weights(plant_footprints)

    estimates = estimates[estimates["year"].isin(years)]
    disaggregation.run(estimates, weights, years)
    evaluation.run(estimates, weights)

    print(f"Time (h): {(time.perf_counter() - start_time) / 3600}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ = parser.add_argument(
        "--years", type=int, nargs="+", choices=plants.YEARS, default=plants.YEARS, metavar="YEAR", help="2008-2020"
    )
    arguments = parser.parse_args()

    years: list[int] = arguments.years
    if len(set(years)) != len(years):
        parser.error("--years has a duplicate year")

    main(years)
