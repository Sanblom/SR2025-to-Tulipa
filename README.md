# SR2025 to Tulipa

This project converts Netbeheer Nederland Scenariorapport 2025 data to
dispatch-only Tulipa inputs. ETM provides national scenario totals. Later
stages can keep these totals national or distribute electricity demand over
Dutch HV nodes.

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

The SR2025 source workbooks under `source_data/` use Git LFS. After cloning,
run `git lfs pull` before executing the data pipeline.

## Electricity regionalisation

Generate the municipality weights, nodal demand, and nodal generation
capacities with:

```powershell
.\.venv\Scripts\sr2025-electricity-regionalise.exe
```

The stage recalculates distributions from the SR2025 municipality and province
workbooks. Historical precomputed regionalisation factors are not runtime
inputs. See `docs/regionalisation_methodology.md` for the spatial decisions,
audits, and remaining offshore-topology review.

## ETM scenario inventory

The featured NBNL scenarios and their stable engine data are public. No ETM
token is required.

```powershell
.\.venv\Scripts\sr2025-inventory.exe
```

The command resolves all enabled entries in `config/scenarios.csv`, checks
their title, year, area, and ETM version, and writes the ignored file
`output/audit/scenario_inventory.csv`. A metadata mismatch stops the run.
Featured saved-scenario IDs are resolved through their public My ETM pages.
Their underlying scenario data remains pinned to the `2025-01` stable engine.

## Demand query audit

```powershell
.\.venv\Scripts\sr2025-demand-audit.exe
```

This collects candidate national electricity, hydrogen, and methane final-demand
queries for all scenarios. See `docs/demand_query_audit.md` before choosing how
to aggregate them into Tulipa demand.

## Profile query audit

```powershell
.\.venv\Scripts\python.exe -m sr2025_to_tulipa.profile_audit
```

This collects the currently identified hydrogen component curves, assembles
sector curves, and compares each 8,760-hour integral with audited annual final
demand. The output remains an audit artifact; reconciliation findings must be
resolved before curves become canonical Tulipa profiles. See
`docs/profile_query_audit.md` for the current findings and blocked carriers.

Canonical national hydrogen and natural-gas profiles are built with:

```powershell
.\.venv\Scripts\sr2025-carrier-profiles.exe
```

The command applies the configured heat boundaries, preserves raw gas
participant curves, normalizes available sector shapes to annual demand, and
reports missing shapes without fabricating them.

## Tulipa assets and converters

Generate the dispatch-only asset, flow, storage, profile, and electricity
transport tables for one scenario-year with:

```powershell
.\.venv\Scripts\sr2025-tulipa-assets.exe koersvaste_middenweg 2030
```

Electricity capacities retain their assigned `E-xxx` nodes. Fuel-to-power
assets connect the national hydrogen or methane bus to the local electricity
bus, while electrolysers and reformers connect in the opposite direction.
National and distributed capacities are reconciled in each case output.

ETM efficiencies are applied to gas and hydrogen power plants and reformers;
CHP uses a capacity-weighted electrical efficiency and excludes useful heat.
Electrolysis efficiency is derived from matched ETM electricity-input and
hydrogen-output capacities. `technology-parameters.csv` records the resulting
values and their provenance for every case.

Methane extraction, import, and renewable-production routes feed the national
methane bus. Their ETM route peaks limit power and their annual ETM supplies
limit energy. Commodity prices are charged on these upstream flows. Gas and
hydrogen generator costs contain only the non-fuel remainder of ETM merit-order
costs, preventing fuel-price double counting. The ETM hydrogen production-cost
chart is deliberately excluded because it also includes input energy and fixed
costs.

Electricity demand is attached to the regional `E-xxx` consumer assets;
hydrogen and methane demand remain attached to national carrier consumers.
Where ETM provides annual bunker or transport-LNG demand without an hourly
curve, the annual quantity is distributed uniformly over all 8,760 hours and
marked `flat_proxy` in the reconciliation output.

Imported ammonia, liquid hydrogen, and LOHC routes are separate direct hydrogen
producers. Their ETM MW-H2 capacities are retained without efficiency scaling,
and imported-carrier prices are converted to EUR/MWh-H2 using their conversion
yields. Other fuel-fired electricity technologies remain direct MW-electric
producers, so their efficiencies are not required at the current model boundary.

Reformer economics are loaded from
`source_data/i_elgas/I-ELGAS_Technology_Data.csv`. SR2025's
dispatchable ATR and must-run ATR-CCS tranches are both modelled as `atr_ccs`;
`SMR` and `SMR_CCS` map directly. Their
non-fuel marginal costs are combined with residual emissions valued at each
scenario's ETM CO2 price. Methane remains priced upstream, so fuel and CO2 are
each counted once. See `source_data/i_elgas/README.md` for units, provenance,
and the reformer mapping decision.

## Pipeline run dashboard

```powershell
.\.venv\Scripts\sr2025-dashboard.exe
```

This terminal command shows enabled scenario-years, selected preparatory
pipeline stages, model-boundary options, and fixed choices. It writes a
reviewable `output/run_manifest.json` without running extraction commands.
It does not yet constitute a clean-room full build: the capacity scan inputs,
electricity regionalisation, per-case Tulipa export, and optional TYNDP merge
remain separate commands. The reproducibility gaps and intended product
boundaries are documented in `docs/pipeline_architecture.md`.

Prompt for every choice:

```powershell
.\.venv\Scripts\sr2025-dashboard.exe --interactive
```

Configure and execute selected stages directly:

```powershell
.\.venv\Scripts\sr2025-dashboard.exe `
	--flexible-heat heat_demand `
	--industrial-heat final_energy_demand `
	--network-losses include `
	--steps inventory demand profiles `
	--execute
```

Choices are validated and persisted in `config/model_options.csv`. Omit
`--execute` to review the resulting run configuration without running the
pipeline.

## Optional TYNDP 2026 integration

The TYNDP dispatch pipeline remains a separate adjacent repository. Do not copy
its scripts or source data into this project. Build its dispatch folders there,
then merge them with the standalone SR2025 outputs:

```powershell
Push-Location ..\TYNDP-26-to-Tulipa
$env:TULIPA_MODEL_MODE = "dispatch"
foreach ($year in 2030, 2035, 2040, 2050) {
	$env:TYNDP_YEAR = "$year"
	& .\.venv\Scripts\python.exe .\run.py
}
Remove-Item Env:TULIPA_MODEL_MODE, Env:TYNDP_YEAR
Pop-Location

.\.venv\Scripts\sr2025-tyndp-integrate.exe --all `
	--tyndp-root ..\TYNDP-26-to-Tulipa
```

The year-to-folder mapping is configured in
`config/tyndp_integration_years.csv`; electricity boundary aliases are in
`config/tyndp_electricity_boundary_nodes.csv`. Integrated products are written
to `output/integrated`. See `docs/pipeline_architecture.md` for the exact
repository contract and the handoff prompt for the TYNDP Copilot session.

For any single fixed-capacity TYNDP dispatch folder, bypass the configured
folder names and select it directly. First rebuild the SR case with Dutch VRE
profiles from that same folder, then merge it:

```powershell
.\.venv\Scripts\sr2025-tulipa-assets.exe koersvaste_middenweg 2040 `
		--renewable-profile-input `
			..\TYNDP-26-to-Tulipa\tulipa_input_north_sea_2026_dispatch `
		--output-root output\joint-sr-source

.\.venv\Scripts\sr2025-tyndp-integrate.exe koersvaste_middenweg 2040 `
		--sr-root output\joint-sr-source `
		--tyndp-case-dir `
			..\TYNDP-26-to-Tulipa\tulipa_input_north_sea_2026_dispatch `
		--tyndp-source-year 2040 `
		--output-root output\joint-tyndp-dispatch
```

The command rejects TYNDP inputs containing investable assets. Its integration
manifest records the selected folder and a content fingerprint, so different
dispatch runs remain traceable and cannot share a stale optional-table cache.