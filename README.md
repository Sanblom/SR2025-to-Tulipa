# SR2025 to Tulipa

Build dispatch-only Tulipa inputs from the Netbeheer Nederland
Scenariorapport 2025 scenarios. ETM supplies national scenario data; the
versioned SR2025 workbooks place Dutch electricity demand and assets on
`E-xxx` nodes.

## Setup

Git LFS is required for the source workbooks.

```powershell
git lfs pull
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

For development and tests, install `.[dev]` instead.

## Build one scenario

A build needs a fixed-capacity TYNDP dispatch folder containing `asset.csv` and
`profiles-rep-periods.csv`. It supplies Dutch PECD weather profiles for wind,
solar, and run-of-river generation.

```powershell
.\.venv\Scripts\sr2025-build.exe koersvaste_middenweg 2040 `
    --renewable-profile-input `
        ..\TYNDP-26-to-Tulipa\tulipa_input_north_sea_2026_dispatch
```

The command performs the complete standalone build:

1. validates the configured public ETM scenario;
2. gathers electricity, hydrogen, methane, and hourly demand data from ETM;
3. groups capacities and carrier demand;
4. regionalises Dutch electricity demand and assets;
5. writes the Tulipa case to `output/tulipa/<scenario>_<year>/`.

Use `--collect-only` to gather and transform source data without exporting the
Tulipa tables:

```powershell
.\.venv\Scripts\sr2025-build.exe koersvaste_middenweg 2040 --collect-only
```

Scenario keys and enabled years are defined in `config/scenarios.csv`. Demand
boundaries are explicit in `config/model_options.csv`; aggregation and source
query mappings are in the other files under `config/`.

## Generated data

Generated files are ignored by Git:

- `output/data/` contains the verified scenario identity, native ETM source
  values, and grouped capacities/supply;
- `output/profiles/demand_hourly.csv` contains grouped hourly carrier demand;
- `output/regionalisation/` contains nodal electricity demand and capacity;
- `output/tulipa/` contains the final standalone cases.

## Regionalisation sources

Every versioned file under `source_data/regionalisation/` is active:

- `Regionalisering vraag SR2025.xlsx` is read for every build and supplies the
  municipality-to-node mapping and CBS industry weights;
- `municipality_node_overrides.csv` is read for every build and completes
  reviewed post-merger municipality mappings;
- one `Scenario <name> <year>.xlsx` workbook is read for the selected scenario
  and reference year. The 2025 case uses the 2030 workbook.

Across all 17 enabled scenario-years, all 16 scenario workbooks are selected at
least once. A single build reads only its selected scenario workbook, plus the
master workbook and overrides. See `docs/regionalisation_methodology.md` for
the allocation rules.

## Optional TYNDP integration

The adjacent `TYNDP-26-to-Tulipa` repository owns the European source-data
conversion. After building the corresponding SR2025 cases, merge fixed-capacity
TYNDP dispatch folders with:

```powershell
.\.venv\Scripts\sr2025-tyndp-integrate.exe --all `
    --tyndp-root ..\TYNDP-26-to-Tulipa
```

The merger removes TYNDP's parallel Dutch system, retains surrounding countries
and approved cross-border links, and inserts the SR2025 Dutch system. See
`docs/pipeline_architecture.md` for the interface contract.

## Tests

Tests are development safeguards, not runtime pipeline stages:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```
