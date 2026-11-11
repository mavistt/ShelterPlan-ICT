import pandas as pd
import geopandas as gpd
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELTERS = ROOT / "data/processed/shelters_geocoded.geojson"
DEMAND = ROOT / "data/processed/demand_nodes.geojson"
MATRIX = ROOT / "data/processed/travel_matrix.parquet"

@pytest.fixture(scope="module")
def outputs():
    missing = [p for p in [SHELTERS, DEMAND, MATRIX] if not p.exists()]
    if missing:
        pytest.skip("Run the data pipeline before testing: " + ", ".join(str(p) for p in missing))
    return (
        gpd.read_file(SHELTERS),
        gpd.read_file(DEMAND),
        pd.read_parquet(MATRIX)
    )

def test_shelter_ids_and_capacity(outputs):
    shelters, _, _ = outputs
    assert shelters["shelter_id"].is_unique
    assert (pd.to_numeric(shelters["capacity_persons"]) > 0).all()
    assert shelters.crs.to_epsg() == 4326

def test_demand_nodes(outputs):
    _, demand, _ = outputs
    assert demand["demand_id"].is_unique
    assert (pd.to_numeric(demand["pop_est"]) >= 0).all()
    assert demand.crs.to_epsg() == 4326

def test_travel_matrix_join_and_reachability(outputs):
    shelters, demand, matrix = outputs
    assert not matrix.duplicated(["demand_id", "shelter_id"]).any()
    assert set(matrix["demand_id"]).issubset(set(demand["demand_id"].astype(str)))
    assert set(matrix["shelter_id"]).issubset(set(shelters["shelter_id"].astype(str)))
    reachable = matrix["reachable"].astype(bool)
    assert matrix.loc[reachable, ["distance_m", "time_min"]].notna().all().all()
    assert (matrix.loc[reachable, "distance_m"] >= 0).all()
