# External source data

This directory contains source inputs that are not queried from ETM. Files are
kept separate from pipeline configuration and generated `output/` artifacts.

## Local inputs

### `regionalisation/`

SR2025 spatial allocation workbooks:

- `Regionalisering vraag SR2025.xlsx` maps municipalities to `E-xxx` nodes and
  provides CBS-based municipal industry drivers.
- `regionalisation/municipality_node_overrides.csv` records reviewed mappings
  for post-merger municipalities absent from the master workbook.
- `Scenario <name>/Scenario <name> <year>.xlsx` provides municipality and
  province demand/capacity drivers for each scenario-year.

These files distribute national ETM demand, generation, storage, and hydrogen
conversion capacities. They do not define hourly VRE weather.

Generation placement is recalculated from the scenario workbooks. Ordinary
technologies use technology-specific municipality capacity columns. Provincial
industrial CHP is distributed with CBS municipal industrial-power weights and
added to applicable municipality CHP capacity before normalization.
Offshore capacity is assigned to the landing `E-xxx` nodes associated with its
SR2025 municipalities. A nonzero national capacity without a spatial driver is
an error; historical generic fallbacks are not used.

## Retired regionalisation artifacts

The generated `Input I-ELGAS distributions/Regionalisation_*.xlsx` files and
their ad hoc `Regionalisation.py` generator were removed. They combined broad
technology categories, copied methane placement to green gas, emitted a zero EV
factor, and depended on manual mappings that were not fully reproducible. They
are historical comparison artifacts, not source data.

`ETM_transfer_script.py` was also removed. It targeted legacy II3050 CSVs and
did not create SR2025 nodal distributions.

### `i_elgas/`

- `Electricity Trading Capacities I-ELGAS.xlsx` defines directional internal
  `E-xxx` network and Dutch border transfer capacities.
- `I-ELGAS_Technology_Data.csv` supplies reformer marginal costs and residual
  emission factors.
- `README.md` documents the technology-data units and cost boundary.

## Linked external repository

Hourly Dutch and European VRE availability profiles are not duplicated here.
They are read from fixed-capacity Tulipa outputs produced by the adjacent
`TYNDP-26-to-Tulipa` repository. Those outputs derive VRE profiles from the
TYNDP 2026 PECD v4.2 data bundle.

The SR2025 exporter reads Dutch source profiles for:

- `NL00_Wind_Onshore`;
- `NL00_Wind_Offshore`;
- capacity-weighted `NL00_Solar_Photovoltaic` and `NL00_Solar_Rooftop`;
- `NL00_Hydro_Run_of_River`.

All Dutch assets of the same technology share the corresponding national hourly
availability profile after their capacity is allocated to `E-xxx` nodes.

## ETM data

ETM scenario metadata, annual demand, hourly demand and must-run curves,
generation parameters, methane supply quantities, and scenario fuel/CO2 prices
are fetched through the ETM API or consumed from generated audit CSVs. They are
therefore not stored in this source-data directory.

## Provenance still required

Before external publication, record source versions, extraction dates, owners,
licenses, redistribution permissions, and the currency year of I-ELGAS costs.
