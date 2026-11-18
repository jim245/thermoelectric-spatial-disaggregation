"""
Draws the point locations of the plants on a map of the conterminous US, colored by whether they used water in a year.
"""

import argparse
from pathlib import Path

import geopandas
import pandas
from matplotlib import pyplot

import plants
from utilities import raster_utilities

STATES_PATH = Path("input", "USA", "cb_2024_us_state_500k.shp")

# outside the conterminous US, which EPSG:5070 covers and which holds every plant
OTHER_STATES = ["AK", "HI", "PR", "VI", "GU", "MP", "AS"]


def run(year: int = 2020) -> None:
    """
    Draws the plants in plants.USGS_PLANTS_PATH. A plant is active if its consumption or withdrawal in the year sums to
    more than 0.

    Writes output/plants_active_<year>.png.

    Parameters:
        year (int = 2020): The year. Must be in plants.YEARS.
    """

    states = geopandas.read_file(STATES_PATH)
    states = states[~states["STUSPS"].isin(OTHER_STATES)].to_crs(raster_utilities.REPROJECTED_CRS)

    estimates = plants.read_estimates()
    year_totals = estimates[estimates["year"] == year].groupby("plant_code")[["cu_mgd", "wd_mgd"]].sum()
    active_codes = year_totals.index[(year_totals["cu_mgd"] > 0) | (year_totals["wd_mgd"] > 0)]

    locations = plants.read_plant_locations()
    points = geopandas.GeoDataFrame(
        {"active": locations.index.isin(active_codes)},
        geometry=geopandas.points_from_xy(locations["longitude"], locations["latitude"]),
        crs="EPSG:4326",
    ).to_crs(raster_utilities.REPROJECTED_CRS)
    active: pandas.Series[bool] = points["active"]

    figure, axes = pyplot.subplots(figsize=(14, 9))
    _ = states.plot(ax=axes, facecolor="none", edgecolor="gray", linewidth=0.5)
    _ = points[~active].plot(ax=axes, color="red", markersize=6, label=f"Inactive ({(~active).sum()})")
    _ = points[active].plot(ax=axes, color="blue", markersize=6, label=f"Active ({active.sum()})")

    _ = figure.suptitle(
        f"Point Locations of Spatially Disaggregated {year} USGS Thermoelectric Power Plants",
        fontweight="bold",
        fontsize=24,
        wrap=True,
    )
    _ = axes.legend(loc="lower left")
    axes.set_axis_off()
    figure.tight_layout()

    figure.savefig(Path("output", f"plants_active_{year}.png"), dpi=200)
    pyplot.close(figure)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--year", type=int, choices=plants.YEARS, default=2020, metavar="YEAR", help="2008-2020")
    run(parser.parse_args().year)
