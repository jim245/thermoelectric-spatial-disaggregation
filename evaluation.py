"""
Compares the water use of each plant-month with the sum of the cells it was spread over.
"""

from pathlib import Path

import numpy
import pandas
from matplotlib import pyplot

from disaggregation import VARIABLES, CellWeights

METRICS_PATH = Path("output", "evaluation.csv")
HISTOGRAM_PATH = Path("output", "coverage_histogram.png")


def run(estimates: pandas.DataFrame, weights: dict[int, CellWeights]) -> None:
    """
    Scores the cell sums of each plant-month against its water use.

    The cell sum of a plant-month is its water use times the sum of its cell weights (the coverage). A variable's
    metrics use the plant-months where that variable is positive. A plant-month with no land cells counts with a cell
    sum of 0.

    Writes METRICS_PATH with these metrics per variable: the R^2, the mean absolute error (mgd), the mean percentage
    error, and the smallest and largest difference (mgd). It also writes the fraction of the water outside the land
    cells (lost_fraction). Writes HISTOGRAM_PATH with the histogram of 1 - coverage over the plant-months with positive
    consumption or withdrawal. Prints both results.

    Parameters:
        estimates (pandas.DataFrame): The plant-months, as from plants.read_estimates.
        weights (dict[int, CellWeights]): The cell weights of each plant in estimates.
    """

    coverage = estimates["plant_code"].map(
        {plant_code: float(cells.weights.sum()) for plant_code, cells in weights.items()}
    )
    metrics: dict[str, dict[str, float]] = {}
    for variable, column in VARIABLES.items():
        positive = estimates[column] > 0
        actual = estimates.loc[positive, column].to_numpy()
        predicted = actual * coverage[positive].to_numpy()
        difference = actual - predicted

        metrics[variable] = {
            "r_squared": 1 - numpy.sum(difference**2) / numpy.sum((actual - numpy.mean(actual)) ** 2),
            "mae_mgd": numpy.mean(numpy.abs(difference)),
            "mpe_percent": numpy.mean(difference / actual) * 100,
            "min_difference_mgd": difference.min(),
            "max_difference_mgd": difference.max(),
            "lost_fraction": numpy.sum(difference) / numpy.sum(actual),
        }

    table = pandas.DataFrame(metrics).T.rename_axis("variable")
    table.to_csv(METRICS_PATH)
    print(table.to_string())

    with_water = (estimates[list(VARIABLES.values())] > 0).any(axis=1)
    uncovered = 1 - coverage[with_water].to_numpy()
    print(f"Uncovered fraction of the footprint: min {uncovered.min()}, max {uncovered.max()}")

    figure, axes = pyplot.subplots()
    _ = axes.hist(uncovered)
    _ = axes.set(xlabel="1 - coverage", ylabel="Plant-months")
    figure.savefig(HISTOGRAM_PATH)
    pyplot.close(figure)
