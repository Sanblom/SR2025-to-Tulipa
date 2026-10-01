# I-ELGAS Technology Data

`I-ELGAS_Technology_Data.csv` contains techno-economic parameters used by the
I-ELGAS model. The source is a semicolon-delimited export from the I-ELGAS
Microsoft Access database and uses decimal commas.

## Units

- `EMISSIONS`: kg/GJ of fuel input
- `Marginal Costs`: EUR/MWh of technology output, excluding fuel and CO2 cost
- `RAMP RATE`: percentage, stored as a decimal fraction (`1` = 100%)
- `Efficiency`: percentage, stored as a decimal fraction (`0,82` = 82%)
- `Min load`: percentage, stored as a decimal fraction (`0,4` = 40%)

The currency year is not recorded in the exported table and must be established
from the source database documentation before costs from different price years
are compared.

## Reformer Mapping

The Tulipa exporter uses ETM capacities and ETM-derived conversion efficiencies.
It imports only marginal costs and residual emissions from this file:

| I-ELGAS row | ETM/Tulipa group | Interpretation |
| --- | --- | --- |
| `ATR` | `atr_ccs` | Blue ATR with 5.68 kg/GJ residual emissions, approximately 90% capture relative to 56.8 kg/GJ natural gas |
| `SMR` | `smr` | Grey hydrogen without carbon capture |
| `SMR_CCS` | `smr_ccs` | SMR with 31.24 kg/GJ residual emissions, approximately 45% capture relative to 56.8 kg/GJ natural gas |

SR2025 reports separate dispatchable ATR and must-run ATR-CCS capacity tranches,
but both belong to its blue-hydrogen category. The exporter merges both into
`atr_ccs`, preserving their operating modes and total capacity. It does not
create a standalone unabated ATR technology.

## Cost Boundary

Methane is priced on the upstream methane-supply flow. Reformer operational cost
therefore contains only:

1. I-ELGAS non-fuel marginal cost; and
2. residual direct CO2 emissions valued at the ETM scenario CO2 price.

For conversion efficiency $\eta$, emissions factor $e$ in kg/GJ fuel input, and
CO2 price $p$ in EUR/tCO2, the output-based carbon cost is:

$$
c_{CO2} = \frac{e \times 3.6}{1000 \times \eta} p
$$

Fuel cost is not added again, and ETM hydrogen production-cost chart values are
not used. This prevents double counting both methane and CO2.

## Provenance and Redistribution

The original Access database name/version, extraction date, table or query name,
and redistribution permission are not encoded in this CSV. Add those details
here when available. Confirm that the source data may be redistributed before
publishing the repository outside its current authorized audience.
