# Profile query audit

Hourly demand profiles must describe the same exogenous final-demand boundary
as the annual demand queries. A curve is not approved merely because ETM
returns 8,760 values.

## Audit outputs

`profile_source_values.csv` contains every source component and its ETM
provenance. `profile_sector_values.csv` adds components within each sector.
`profile_reconciliation.csv` compares each sector integral with its configured
annual query and records `scale_factor_to_annual` without applying it. ETM
curve values are MW, so the annual conversion is:

`profile_integral_pj = sum(hourly_mw) * 3.6e-6`

The current hydrogen run contains 102 scenario-sector checks: 69 pass and 33
remain for review. No curves are rescaled by the audit.

## Hydrogen review cases

The 33 cases fall into two groups:

| Sector | Cases | Curve difference | Diagnosis |
| --- | ---: | ---: | --- |
| Industry | 16 | +0.190% to +1.214% | Dynamic backup-burner curves exceed annual graph demand |
| Agriculture | 3 | +0.991% to +1.473% | Dynamic backup-burner curves exceed annual graph demand |
| Transport | 8 | -0.028% to -0.205% | Rounded gquery arrays |
| Bunkers | 6 | -0.027% to -0.178% | Rounded gquery arrays |

The largest case is Eigen Vermogen 2050 industry: 29.0237 TWh annual demand
versus a 29.3761 TWh curve, a difference of 0.3523 TWh or 1.214%.

The hydrogen reconciliation CSV exposes participant curves at greater numeric
precision than the gquery arrays. Its direct transport and bunker participants
reconcile with annual demand to within about 0.0005 TWh in every scenario.
These cases are therefore numerical precision differences, not different
demand boundaries.

Industry and agriculture remain genuinely different after reconstructing them
from reconciliation participants. Their hydrogen burners are subordinate
backup technologies for power-to-heat flexibility. ETEngine calculates each
backup curve as unmet heat demand after the flexible technology has run and
clamps negative values to zero. The engine source notes that this calculated
value can be too high; it does not subsequently force the curve integral to
equal the annual energy graph. No statically export-classified source nodes
were found, so export classification is not accepted as the explanation.

Raw participant and gquery curves remain unchanged for provenance. Direct
transport and bunker shapes may be scaled to their annual targets when final
profiles are produced. Industry and agriculture require a modelling decision:
if their heat conversion technologies are explicit Tulipa assets, import
useful-heat load instead of fixed hydrogen burner demand; otherwise scale the
ETM burner shape transparently to annual hydrogen demand. Distribution is a
later step and does not affect this decision.

## Known boundaries

- ETM returns the inactive fertilizer hydrogen component as an all-zero
  8,760-hour array without a unit. The audit accepts only this exact inactive
  case and labels its source unit `curve_inactive_zero`.
- ETM has no direct other-sector hydrogen curve. Annual other-sector hydrogen
  demand is zero in the current scenario set, but no profile is fabricated.
- Broad sector network-gas input curves do not match methane final demand.
  Representative 2025 differences include +100.55 PJ for industry and +39.85
  PJ for agriculture, so these curves are blocked.
- LV, MV, and HV electricity demand curves include endogenous conversion,
  storage, losses, exports, or flexibility. Electricity profiles must instead
  be composed from final end-use curves and reconciled sector by sector.

## Configurable model boundary

The current defaults keep flexible and central heat, industrial heat,
agriculture heat, DSR, electricity network losses, and power-sector own use in
demand. The terminal run dashboard may instead direct system power-to-heat
and central district heat together, industrial heat, or agriculture heat to
future useful-heat outputs. DSR may be directed to a future flexible-demand
output. Residential heat is always retained in carrier demand.

Household and EV battery charging and discharging are not endogenous assets.
Their net hourly electricity exchange is retained in the corresponding demand
profile, and their storage capacity is excluded from modelled battery capacity.
Only system-level battery charging, discharging, and capacity are endogenous.

Central heat means district-heating production in the ETM energy sector. Its
central boilers and heat pumps feeding HT, MT, or LT networks follow the
flexible-heat selector. Residential, industrial, and agriculture heat equipment
do not belong to this category.

## Canonical carrier profiles

`sr2025-carrier-profiles` creates national hydrogen and natural-gas profiles in
`output/profiles`. It preserves raw network-gas reconciliation tables in
`output/raw/network_gas`, records every participant filter decision, and scales
each available sector shape to audited annual demand. Differences below
0.1 TWh receive `pass`; material differences receive `normalized`. Both statuses
still retain the raw values and scale factor.

Hydrogen has reviewed sector curves plus selector-controlled central district
heat. Natural gas uses a strict 71-participant mapping which excludes CHP,
electricity and hydrogen production, storage, transformation, exports, gas
losses, and dynamic backup/conversion curves which duplicate sector heat
demand. Industrial and agriculture burner demand and central heat follow their
configured selectors. All available non-bunker natural-gas sector shapes then
reconcile within 0.1 TWh before normalization.

ETM bunker methane demand and part of transport methane demand are LNG rather
than network gas. The transport compressor curve is reconciled only to its
network-gas annual target. Residual `transport_lng` and bunker LNG have annual
values but no ETM hourly curve. Canonical profiles distribute those annual
quantities uniformly over 8,760 hours and label them `flat_proxy`; the audit
continues to retain the missing-source finding so the proxy remains explicit.