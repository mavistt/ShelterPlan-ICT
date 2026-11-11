# Module A data dictionary

All GeoJSON vector outputs use WGS84 / EPSG:4326. The GraphML network is stored in a local projected CRS (metres); its CRS is embedded in the GraphML metadata.

## shelters_clean.csv

| Field | Type | Meaning |
|---|---|---|
| `shelter_id` | string | Stable row ID generated from source attributes and source row |
| `town` | string | Official town/subdistrict name |
| `community_name` | string | Source text label only; not a boundary geometry |
| `shelter_name` | string | Official shelter name |
| `address` | string | Original address text |
| `capacity_persons` | numeric | Official capacity after multiplying “万人” by 10,000 |
| `area_sqm` | numeric | Official area after multiplying “万㎡” by 10,000 |
| `source_file` | string | Original workbook name |
| `source_row` | integer | Approximate source row number (header is row 1) |
| `duplicate_candidate` | boolean | Possible duplicate; not automatically removed |
| `verified` | boolean | False until reviewed |

## shelters_geocoded.geojson

Only records that pass manual coordinate review are included. Key properties: `shelter_id`, `capacity_persons`, `verified_status`, `verification_note`, `lon_wgs84`, `lat_wgs84`, plus source and geocoding audit fields.

## demand_nodes.geojson

| Field | Type | Meaning |
|---|---|---|
| `demand_id` | string | Unique population-cell ID |
| `row`, `col` | integer | Row/column in the clipped raster |
| `pop_est` | float | WorldPop modelled population estimate in that cell |
| `population_year` | integer | WorldPop reference year |
| `population_status` | string | `modelled_estimate` |

`demand_persons` is scenario-dependent and should be computed by Module B as `pop_est * population_demand_ratio`.

## travel_matrix.parquet

| Field | Type | Unit / meaning |
|---|---|---|
| `demand_id` | string | Foreign key to demand node |
| `shelter_id` | string | Foreign key to verified shelter |
| `distance_m` | float/null | Shortest walking-network distance, metres |
| `time_min` | float/null | Estimated distance / assumed speed, minutes |
| `reachable` | boolean | A valid path exists and both snap distances pass the configured threshold |
| `reason` | string | `ok`, `demand_snap_too_far`, `shelter_snap_too_far`, `no_path` |
| `demand_snap_m` | float | Demand point to nearest network node |
| `shelter_snap_m` | float | Shelter point to nearest network node |
| `walking_speed_m_per_s` | float | Assumed walking speed |
| `data_version` | string | Dataset version |

The matrix does not encode the scenario's maximum walking time. Module B applies that threshold when evaluating assignments.
