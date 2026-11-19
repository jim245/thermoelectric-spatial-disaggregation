# Overview

This repository spatially disaggregates the monthly water use of thermoelectric power plants in the conterminous United States into gridded estimates. It spreads the USGS consumption and withdrawal estimates of each plant over the grid cells under the plant's footprint. Each cell gets the fraction of the water that equals the fraction of the footprint's area in that cell. A footprint is the union of the plant's [OpenStreetMap](https://www.openstreetmap.org) (OSM) features, found through the [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API). A plant with no feature gets a circle with the median footprint area. The grid is the 1000 m EPSG:5070 grid of the HUC water use disaggregation, so the products align cell for cell.

# Setup

Use [conda](https://docs.conda.io/projects/conda/en/latest/user-guide/install/) for package and environment management. The environment requires linux-64.

From the repository root, run `conda env create -f environment.yaml -p .conda` to create the environment in `.conda`, and `conda activate ./.conda` to activate it.

Run `.conda/bin/pre-commit install` once to install the [pre-commit hooks](./.pre-commit-config.yaml). They lint, type-check, spell-check and format each commit, according to the tool settings in [`pyproject.toml`](./pyproject.toml).

## Folder Structure

All inputs are in `input/`. Put the datasets at the paths in [Datasets Used](#datasets-used) before the run.

The pipeline writes its outputs to `output/`. The rasters are float64 GeoTIFFs with lossless DEFLATE compression, in million gallons per day (mgd) per cell, with NaN outside the land cells:

- `{year}-{month}-{consumption,withdrawal}.tif`: the monthly rasters, with a 2-digit month.
- `{year}-annual-{consumption,withdrawal}.tif`: the day-weighted mean of the 12 monthly rasters.
- `raster_1000.tif`: the template grid.
- `footprints.gpkg`: one footprint per plant. A later run reads this file and sends no Overpass query. Delete it to build the footprints again.
- `overpass/{plant_code}.json.gz`: the Overpass request and response of each queried plant. A new build of the footprints uses these responses, and queries only the plants with no file. Every query reads OSM as it was on the date in [`footprints.OSM_DATE`](./footprints.py), so a new query gives the same features.
- `evaluation.csv` and `coverage_histogram.png`: the metrics.

# Workflow

1. Build the [template raster](./utilities/raster_utilities.py) from the HUC-2 boundaries.
2. Read the [plant-months and plant locations](./plants.py), and find the [plant footprints](./footprints.py).
3. [Disaggregate](./disaggregation.py) each plant-month over the land cells under its footprint. Water use on the part of a footprint outside the land cells is lost.
4. [Evaluate](./evaluation.py) by comparing each plant-month with the sum of its cells.

The [main.py](./main.py) orchestrator script can be invoked as follows:

```text
usage: main.py [-h] [--years YEAR [YEAR ...]]

options:
  -h, --help            show this help message and exit
  --years YEAR [YEAR ...]
                        2008-2020
```

Example:

```sh
python main.py --years 2019 2020
```

To draw the plant locations of a year to `output/plants_active_{year}.png`, run `python -m utilities.plot_plants --year 2020`.

# Datasets Used

Paths are relative to `input/`.

| Dataset                                                                                                                                                                                         | Path                                                                  | Use                    |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- | ---------------------- |
| [Thermoelectric Power Water Use Reanalysis, 2008-2020](https://www.usgs.gov/data/thermoelectric-power-water-use-reanalysis-2008-2020-period-power-plant-month-and-year) (Galanter, 2023) [^1] | `published_monthly_thermoelectric_water_use_estimates_2008-2020.csv` | Plant-months           |
| [Thermoelectric plant attributes](https://www.sciencebase.gov/catalog/item/63adc826d34e92aad3ca5af4)                                                                                            | `te_plants.csv`                                                       | Plants, USGS locations |
| [EIA-860](https://www.eia.gov/electricity/data/eia860/), 2020                                                                                                                                   | `2___Plant_Y2020.xlsx`                                                | Plant locations        |
| [Watershed Boundary Dataset](https://www.usgs.gov/national-hydrography/watershed-boundary-dataset), HU-2 shapefiles of regions 1-18                                                            | `HUC/{huc2}/Shape/WBDHU2.shp`                                         | Template grid          |
| [Census Cartographic Boundary Files](https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html) (2024 states at 1:500,000)                                    | `USA/cb_2024_us_state_500k.shp`                                       | Plant map              |

[^1]: The reanalysis builds on the [condenser duty estimates](https://www.usgs.gov/data/thermoelectric-power-condenser-duty-estimates-month-and-cooling-type-use-calculate-water-use) (Sanisaca, 2023).

The WBD HU-2 shapefiles are at `https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/HU2/Shape/WBD_{region}_HU2_Shape.zip`, where `region` is the region number with 2 digits (`01` to `18`). Extract `Shape/WBDHU2.*` to `HUC/{huc2}/`, where `huc2` is the region number with no leading zero (`1` to `18`).

# License

Pending
