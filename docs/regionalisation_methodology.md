# Regionalisation methodology

## Plain-language summary

National ETM capacity and demand are the amount to place. The SR2025
municipality and province workbooks provide the location clues. The
municipality-to-grid key then collects municipality values at `E-xxx` buses.
Every distribution is normalized and reconciled back to the national total.

The removed `Regionalisation_*.xlsx` files were generated outputs, not source
evidence. They are no longer used as model inputs.

## Decisions

- Ordinary electricity generation uses technology-specific municipality
  capacity columns.
- Provincial industrial CHP is distributed within each province using CBS
  municipal industrial-power weights.
- When one Tulipa asset group contains both municipal and industrial components,
  their absolute capacities are added per municipality before normalization.
  Natural-gas CHP therefore combines municipal methane CHP, agricultural CHP,
  and province-level industrial methane CHP.
- Electricity demand retains separate spatial drivers for each Tulipa demand
  group.
- Battery, IDES, and MDES storage retain separate municipality drivers.
- A nonzero national value without a matching spatial driver stops the build.
- Generic capacity factors and silent copied proxies are not allowed.
- The municipality-to-node workbook is the versioned mapping authority.
- Reviewed post-merger municipality mappings are recorded separately in
  `source_data/regionalisation/municipality_node_overrides.csv`.
- Source rows resolving to the same municipality or node are added; one row
  must never overwrite another.
- Historical regionalisation workbooks are not runtime inputs or fallbacks.

## Offshore wind

SR2025 offshore capacity is currently attached to six coastal municipalities
and therefore to their mapped landing `E-xxx` buses. This is transparent and
does not depend on the removed historical factors.

Before treating those assignments as physically validated, compare the six
municipality-to-node mappings with a reviewed offshore cable and landing-point
topology. The dispatch model will continue to represent offshore generation at
landing `E-xxx` buses rather than adding dedicated offshore buses.

## Validation

Spatial weights are normalized per scenario, year, and demand or capacity
group. Missing mappings, empty nonzero drivers, unknown technologies, and
incomplete hourly profiles stop the build. Production output contains only the
nodal demand and capacity tables consumed by the Tulipa exporter; conservation
and source-boundary behavior are covered by the test suite.