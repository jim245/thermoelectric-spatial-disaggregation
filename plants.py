"""
Reads the USGS thermoelectric water use estimates and the plant locations.
"""

from pathlib import Path

import pandas

ESTIMATES_PATH = Path("input", "published_monthly_thermoelectric_water_use_estimates_2008-2020.csv")

# ScienceBase item 63adc826d34e92aad3ca5af4
USGS_PLANTS_PATH = Path("input", "te_plants.csv")

EIA_PLANTS_PATH = Path("input", "2___Plant_Y2020.xlsx")

# the years of the USGS estimates
YEARS = tuple(range(2008, 2020 + 1))

# plant code: (latitude, longitude), for plants whose EIA-860 location is wrong
LOCATION_FIXES = {
    468: (39.148975, -108.318069),
    658: (27.4513504, -80.3243010),
    2326: (35.2097, -119.585),
    55990: (41.909208, -80.769817),
}


def read_estimates() -> pandas.DataFrame:
    """
    Reads the monthly water use of each plant in USGS_PLANTS_PATH.

    Returns:
        estimates (pandas.DataFrame): One row per plant and month, with the columns plant_code, year, month, cu_mgd
            (consumption) and wd_mgd (withdrawal). The rows keep the order of USGS_PLANTS_PATH, and the order of
            ESTIMATES_PATH within a plant.
    """

    plant_codes = pandas.read_csv(USGS_PLANTS_PATH, usecols=["Plant.Code"])
    # the default float parser of read_csv isn't correctly rounded
    estimates = pandas.read_csv(
        ESTIMATES_PATH, usecols=["Plant.Code", "Month", "YEAR", "cu_mgd", "wd_mgd"], float_precision="round_trip"
    )

    return (
        pandas.merge(plant_codes, estimates, on="Plant.Code")
        .rename(columns={"Plant.Code": "plant_code", "YEAR": "year", "Month": "month"})
        .loc[:, ["plant_code", "year", "month", "cu_mgd", "wd_mgd"]]
    )


def read_plant_locations() -> pandas.DataFrame:
    """
    Reads the point location of each plant in USGS_PLANTS_PATH.

    The location is the EIA-860 one, because many USGS locations are HUC-12 points and not plant sites. A plant retired
    before 2020 isn't in the 2020 EIA-860, so it keeps its USGS location. LOCATION_FIXES replaces both.

    Returns:
        locations (pandas.DataFrame): The columns latitude and longitude (EPSG:4326), indexed by plant_code.
    """

    usgs = pandas.read_csv(
        USGS_PLANTS_PATH, usecols=["Plant.Code", "latitude", "longitude"], float_precision="round_trip"
    )
    eia = pandas.read_excel(
        EIA_PLANTS_PATH, sheet_name="Plant", header=1, usecols=["Plant Code", "Latitude", "Longitude"]
    )

    locations = (
        usgs.merge(eia, left_on="Plant.Code", right_on="Plant Code", how="left")
        .set_index("Plant.Code")
        .rename_axis("plant_code")
    )

    # EIA-860 has " " for an unknown location
    for coordinate in ["latitude", "longitude"]:
        eia_coordinate = pandas.to_numeric(locations[coordinate.capitalize()], errors="coerce")
        locations[coordinate] = eia_coordinate.fillna(locations[coordinate])

    for plant_code, coordinates in LOCATION_FIXES.items():
        locations.loc[plant_code, ["latitude", "longitude"]] = coordinates

    return locations.loc[:, ["latitude", "longitude"]]
