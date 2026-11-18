"""
Finds the footprint of each plant in OpenStreetMap (OSM) through the Overpass API.
"""

import gzip
import json
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import geopandas
import numpy
import pandas
import requests
import shapely
from shapely import geometry, ops
from shapely.geometry.base import BaseGeometry

from utilities import raster_utilities

FOOTPRINTS_PATH = Path("output", "footprints.gpkg")

# one gzipped JSON file per queried plant: {"request": the Overpass QL text sent, "response": the JSON response}
RESPONSES_DIRECTORY = Path("output", "overpass")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# every query reads the OSM data as it was at this time, so a new query gives the same features
OSM_DATE = "2026-03-25T00:00:00Z"
OVERPASS_SETTINGS = f'[out:json][date:"{OSM_DATE}"];'

# overpass-api.de answers 406 to a request without a named User-Agent
OVERPASS_HEADERS = {"User-Agent": "thermoelectric-spatial-disaggregation"}

# applies to the connection and to each socket read, not to the whole response
REQUEST_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 5
RETRY_WAIT_SECONDS = 30
# after each answered query, so that a full run doesn't send queries back to back
QUERY_WAIT_SECONDS = 2

# plant code: radius in meters of the OSM search around the plant location (see find_footprints). None means no
# search, so the plant gets a circle
SEARCH_RADII_METERS: dict[int, int | None] = {
    167: 90, 168: 249, 330: 82, 462: 76, 465: 210, 468: 14, 589: 31, 610: 39, 658: 5, 672: None, 709: 155,
    733: 18, 867: 152, 898: 94, 964: 108, 1010: 38, 1037: None, 1046: 31, 1058: 155, 1077: 419, 1175: None,
    1230: 58, 1235: 100, 1317: 56, 1449: 45, 1570: None, 1626: 98, 1642: 58, 1695: 64, 1726: 82, 1731: 134,
    1825: 76, 1830: 130, 1888: 61, 1943: 67, 2022: 27, 2171: 53, 2187: 78, 2326: 33, 2384: None, 2403: None,
    2491: None, 2497: 715, 2513: 90, 2527: 179, 2629: 41, 2642: 715, 2732: 278, 2830: 101, 2835: 199, 2838: 205,
    2840: 119, 2843: None, 2861: 56, 2864: 130, 2943: None, 3181: 53, 3280: 150, 3317: 22, 3454: 178, 3468: 152,
    3483: 50, 3502: 88, 3506: 145, 3574: 134, 3613: 28, 3627: None, 3644: 41, 3947: 335, 3982: 7, 4048: 56,
    4140: 47, 4151: 104, 6082: 202, 7286: 105, 7627: None, 8226: 194, 9864: 1, 10003: None, 10025: None,
    10090: None, 10091: None, 10148: None, 10471: None, 10472: None, 10473: None, 10478: 9, 10593: None,
    10678: 52, 50099: None, 50208: 72, 50273: None, 50630: 104, 50648: None, 50649: None, 50736: None,
    50748: None, 50774: 23, 50852: None, 50872: None, 50921: None, 50966: None, 50976: 14, 50993: None,
    52118: None, 54015: None, 54056: 77, 54529: None, 54693: 36, 54974: None, 55090: None, 55821: 119,
    55853: 117, 55990: 262, 56041: 33, 56707: None, 57241: 528, 58595: None, 60100: None, 60421: 11,
    60550: 101,
}  # fmt: skip

# an element whose ref:US:EIA tag holds the plant code, alone or in a ";"-separated list
EIA_TAG_QUERY = "nwr['ref:US:EIA'~'(^|;){plant_code}(;|$)'];"

# power features other than grid parts, and features whose name or note marks a plant site
RADIUS_QUERY = """(
  nwr(around:{radius}, {latitude}, {longitude})[~"^(demolished:power|disused:power|was:power|power|substation)$"~".*"][power!~"^(cable|insulator|line|minor_line|pole|portal|switch|tower|transformer)$"]["demolished:power"!~"^(cable|insulator|line|minor_line|pole|portal|switch|tower|transformer)$"];
  nwr(around:{radius}, {latitude}, {longitude})[name~"former.*?plant site|generating station|power plant",i][!"highway"];
  nwr(around:{radius}, {latitude}, {longitude})[note~"power.*?plant",i];
);"""

type Rings = dict[str, list[list[dict[str, float]]]]


def run(plant_codes: Sequence[int], locations: pandas.DataFrame) -> geopandas.GeoDataFrame:
    """
    Reads the plant footprints from FOOTPRINTS_PATH, or finds them and writes that file.

    An existing file is used as it is. Delete it to build the footprints again from the responses in
    RESPONSES_DIRECTORY, and from new queries for the plants with no stored response.

    Parameters:
        plant_codes (Sequence[int]): The plants to find footprints for.
        locations (pandas.DataFrame): The latitude and longitude of each plant, indexed by plant code.

    Returns:
        footprints (geopandas.GeoDataFrame): One footprint per plant (EPSG:4326), indexed by plant_code.
    """

    if FOOTPRINTS_PATH.exists():
        return geopandas.read_file(FOOTPRINTS_PATH).set_index("plant_code")

    footprints = find_footprints(plant_codes, locations)

    # the process ID keeps concurrent runs from sharing a temporary file
    temporary_path = FOOTPRINTS_PATH.with_name(f"{FOOTPRINTS_PATH.stem}.{os.getpid()}.tmp{FOOTPRINTS_PATH.suffix}")
    footprints.reset_index().to_file(temporary_path, driver="GPKG")
    _ = temporary_path.replace(FOOTPRINTS_PATH)

    return footprints


def find_footprints(plant_codes: Sequence[int], locations: pandas.DataFrame) -> geopandas.GeoDataFrame:
    """
    Finds the footprint of each plant in OSM.

    A plant in SEARCH_RADII_METERS gets the union of the features within its radius of its location, and no EIA tag
    search. Other plants get the union of the features with their EIA tag. A plant with no feature gets a circle at its
    location, with the median area of the found footprints.

    Reads each plant's response from RESPONSES_DIRECTORY. A plant with no stored response is queried, and its response
    is written there.

    Parameters:
        plant_codes (Sequence[int]): The plants to find footprints for.
        locations (pandas.DataFrame): The latitude and longitude of each plant, indexed by plant code.

    Returns:
        footprints (geopandas.GeoDataFrame): One footprint per plant (EPSG:4326), indexed by plant_code.
    """

    found: dict[int, BaseGeometry | None] = {}
    for plant_code in plant_codes:
        if plant_code in SEARCH_RADII_METERS:
            radius = SEARCH_RADII_METERS[plant_code]
            if radius is None:
                found[plant_code] = None
                continue

            print(f"Plant {plant_code}: search within {radius} m")
            query = RADIUS_QUERY.format(
                radius=radius,
                latitude=locations.at[plant_code, "latitude"],
                longitude=locations.at[plant_code, "longitude"],
            )
            closed_ways_only = True
        else:
            print(f"Plant {plant_code}: search by EIA tag")
            query = EIA_TAG_QUERY.format(plant_code=plant_code)
            closed_ways_only = False

        polygons: list[BaseGeometry] = []
        for element in stored_response(plant_code, query)["elements"]:
            polygon = rings_to_polygon(element_rings(element, closed_ways_only))
            if polygon is not None and not polygon.is_empty and polygon not in polygons:
                polygons.append(polygon)

        found[plant_code] = ops.unary_union(polygons) if polygons else None
        if not polygons:
            print(f"Plant {plant_code}: no footprint found")

    geometries = geopandas.GeoSeries(
        numpy.array(list(found.values()), dtype=object),
        index=pandas.Index(list(found), name="plant_code"),
        crs="EPSG:4326",
    )

    missing = geometries.isna()
    projected = geometries.to_crs(raster_utilities.REPROJECTED_CRS)
    found_areas = shapely.area(projected.dropna().drop_duplicates().to_numpy())
    radius = (numpy.median(found_areas) / numpy.pi) ** 0.5

    missing_locations = locations.loc[geometries.index[missing]]
    points = geopandas.GeoSeries(
        geopandas.points_from_xy(missing_locations["longitude"], missing_locations["latitude"]),
        index=missing_locations.index,
        crs="EPSG:4326",
    )
    geometries[missing] = points.to_crs(raster_utilities.REPROJECTED_CRS).buffer(radius).to_crs("EPSG:4326")

    return geopandas.GeoDataFrame(geometry=geometries)


def stored_response(plant_code: int, query: str) -> dict[str, Any]:
    """
    Reads a plant's response from RESPONSES_DIRECTORY, or sends the query and writes the response there. Waits
    QUERY_WAIT_SECONDS after a query.

    Parameters:
        plant_code (int): The plant that the query is for.
        query (str): The query statements, without the settings and the output statement.

    Returns:
        response (dict[str, Any]): The JSON response.

    Raises:
        ValueError: If the stored request differs from this request, for example after a change of OSM_DATE or of a
            search radius. Delete the file to query again.
    """

    path = RESPONSES_DIRECTORY / f"{plant_code}.json.gz"
    # "out geom" returns the geometry of each element
    request = f"{OVERPASS_SETTINGS}{query}out geom;"
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as file:
            stored = json.load(file)
        if stored["request"] != request:
            raise ValueError(f"{path} holds the response to a different request")
        return stored["response"]

    response = query_overpass(request)
    time.sleep(QUERY_WAIT_SECONDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    # the process ID keeps concurrent runs from sharing a temporary file
    temporary_path = path.with_name(f"{plant_code}.{os.getpid()}.tmp.json.gz")
    with gzip.open(temporary_path, "wt", encoding="utf-8") as file:
        json.dump({"request": request, "response": response}, file)
    _ = temporary_path.replace(path)

    return response


def query_overpass(request: str) -> dict[str, Any]:
    """
    Sends an Overpass QL request and returns the JSON response.

    Tries MAX_ATTEMPTS times, RETRY_WAIT_SECONDS apart. A response with a "remark" counts as a failed attempt, because
    Overpass then may return only part of the elements.

    Parameters:
        request (str): The complete Overpass QL text: settings, statements and output statement.

    Returns:
        response (dict[str, Any]): The JSON response, with the "elements" and the "osm3s" timestamps.
    """

    attempt = 1
    while True:
        try:
            response = requests.post(
                OVERPASS_URL,
                data={"data": request},
                headers=OVERPASS_HEADERS,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            result = response.json()
            if "remark" in result:
                raise requests.RequestException(result["remark"])
            return result
        except requests.RequestException as exception:
            if attempt == MAX_ATTEMPTS:
                raise
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS} failed: {exception}")
            time.sleep(RETRY_WAIT_SECONDS)
            attempt += 1


def element_rings(element: dict[str, Any], closed_ways_only: bool) -> Rings:
    """
    Groups the coordinate lists of an Overpass element by role.

    Parameters:
        element (dict[str, Any]): An element of an Overpass response with geometry.
        closed_ways_only (bool): If True, drops a way whose first and last points differ, or that has fewer than 3
            points.

    Returns:
        rings (Rings): The coordinate lists per role ("outer", "inner" or other). A relation gives one list per member,
            and a way gives one "outer" list. A node gives none.
    """

    rings: Rings = {}
    if "members" in element:
        for member in element["members"]:
            # a node member has no geometry list
            if "geometry" in member:
                rings.setdefault(member.get("role", ""), []).append(member["geometry"])
    elif "geometry" in element:
        points = element["geometry"]
        rings["outer"] = []
        if not closed_ways_only or (len(points) > 2 and points[-1] == points[0]):
            rings["outer"].append(points)

    return rings


def rings_to_polygon(rings: Rings) -> BaseGeometry | None:
    """
    Builds the polygons that the "outer" coordinate lists enclose, minus those that the "inner" lists enclose.

    Lists of other roles are ignored. A list can be part of a ring, because the lists of a role are merged first.

    Parameters:
        rings (Rings): The coordinate lists per role, as from element_rings.

    Returns:
        polygon (BaseGeometry | None): The polygons, or None if there is no "outer" list. Can be empty.
    """

    def polygonize(coordinate_lists: list[list[dict[str, float]]]) -> list[shapely.Polygon]:
        lines = [geometry.LineString([(point["lon"], point["lat"]) for point in points]) for points in coordinate_lists]
        return list(ops.polygonize(ops.unary_union(ops.linemerge(lines))))

    outer = rings.get("outer", [])
    if not outer:
        return None

    inner = rings.get("inner", [])
    holes = geometry.MultiPolygon(polygonize(inner) if inner else None)

    return geometry.MultiPolygon(polygonize(outer)).difference(holes)
