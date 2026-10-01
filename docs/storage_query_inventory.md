# ETM storage query inventory

Verified against the pinned ETM 2025.01 engine with Koersvaste Middenweg 2050
(scenario 506) on 2026-08-20. ETSource `main` may contain newer queries that are
not available in this engine version.

## Electricity storage

The following suffixes resolve for every standard family listed below:

| Suffix | Unit | Meaning |
|---|---:|---|
| `_input_capacity` | MW | Installed charging power |
| `_output_capacity` | MW | Installed discharging power |
| `_storage_volume` | MWh | Installed energy volume |
| `_efficiency` | % | ETM `electricity_output_conversion` |
| `_volume_to_capacity_ratio` | hours | Energy volume divided by typical input power |
| `_annual_input` | PJ | Realized annual charging energy |
| `_annual_output` | PJ | Realized annual discharged energy |
| `_annual_losses` | PJ | Input minus output, including conversion and other losses |

Append these suffixes to:

| SR2025 category | ETM query-family prefix | KM 2050 MW in/out | MWh | Efficiency |
|---|---|---:|---:|---:|
| `Battery_system` | `energy_flexibility_mv_batteries` | 27,000 | 216,000 | 85% |
| `IDES_storage` | `energy_flexibility_flow_batteries` | 6,000 | 108,000 | 70% |
| `MDES_storage` | `energy_flexibility_hv_opac` | 3,200 | 320,000 | 80% |
| Pumped storage | `energy_flexibility_pumped_storage` | 0 | 0 | 80% |
| `Battery_households` | `households_flexibility_p2p_electricity` | 8,403.945 | 33,615.780 | 90% |
| Passenger-car V2G | `transport_car_flexibility_p2p_electricity` | 4,513.752 | 36,110.017 | 87% |
| Bus V2G | `transport_bus_flexibility_p2p_electricity` | 0 | 0 | 87% |
| Truck V2G | `transport_truck_flexibility_p2p_electricity` | 0 | 0 | 87% |
| Van V2G | `transport_van_flexibility_p2p_electricity` | 0 | 0 | 87% |

For example, the complete MV-battery query set is:

```text
energy_flexibility_mv_batteries_input_capacity
energy_flexibility_mv_batteries_output_capacity
energy_flexibility_mv_batteries_storage_volume
energy_flexibility_mv_batteries_efficiency
energy_flexibility_mv_batteries_volume_to_capacity_ratio
energy_flexibility_mv_batteries_annual_input
energy_flexibility_mv_batteries_annual_output
energy_flexibility_mv_batteries_annual_losses
```

ETSource defines `_efficiency` as `electricity_output_conversion`. To reproduce
that convention directly in Tulipa, use charging efficiency 1.0 and discharging
efficiency `efficiency / 100`. Using the square root on both sides is a different,
symmetrical convention even though it has the same round-trip efficiency.

## Coupled renewable batteries

These merit-order queries are renewable generator capacities, not battery power:

```text
merit_order_battery_solar_pv_capacity_in_merit_order_table
merit_order_battery_wind_inland_capacity_in_merit_order_table
```

Battery power is set by saved-scenario percentages:

```text
battery_capacity_always_on_solar_pv_solar_radiation
battery_capacity_always_on_wind_turbine_inland
```

Both are 50% in KM 2050. Durations are saved as:

```text
volume_of_energy_flexibility_solar_batteries_electricity
volume_of_energy_flexibility_wind_batteries_electricity
```

They are 4 h and 8 h respectively in KM 2050. ETSource exposes hourly curves and
losses, but not the standard storage-specification capacity/volume query family:

```text
energy_flexibility_solar_batteries_electricity_input
energy_flexibility_solar_batteries_electricity_output
energy_flexibility_solar_batteries_electricity_losses
energy_flexibility_wind_batteries_electricity_input
energy_flexibility_wind_batteries_electricity_output
energy_flexibility_wind_batteries_electricity_losses
```

For this pipeline, multiply each coupled generator capacity by its saved battery
percentage, calculate MWh with its own duration, and add both MW and MWh to
`Battery_system`. ETSource applies the MV-battery efficiency input to the MV,
solar-coupled and wind-coupled battery nodes, so this aggregation preserves a
single valid efficiency.

## Hydrogen storage

Use separate assets for salt caverns and depleted gas fields.

| Quantity | Salt-cavern query | Depleted-field query | Unit |
|---|---|---|---:|
| Installed output power | `capacity_energy_hydrogen_storage_salt_cavern_for_h2_chart` | `capacity_energy_hydrogen_storage_depleted_gas_field_for_h2_chart` | MW |
| Total installed volume | `hydrogen_storage_volume_salt_cavern_in_mekko_of_storage_volume` | `hydrogen_storage_volume_depleted_gas_field_in_mekko_of_storage_volume` | TWh |
| Hourly charge | `energy_hydrogen_storage_salt_cavern_hydrogen_input_curve` | `energy_hydrogen_storage_depleted_gas_field_hydrogen_input_curve` | curve |
| Hourly discharge | `energy_hydrogen_storage_salt_cavern_hydrogen_output_curve` | `energy_hydrogen_storage_depleted_gas_field_hydrogen_output_curve` | curve |
| Annual charge | `energy_hydrogen_storage_salt_cavern_annual_input` | `energy_hydrogen_storage_depleted_gas_field_annual_input` | PJ |
| Annual discharge | `energy_hydrogen_storage_salt_cavern_annual_output` | `energy_hydrogen_storage_depleted_gas_field_annual_output` | PJ |
| Annual losses | `energy_hydrogen_storage_salt_cavern_annual_losses` | `energy_hydrogen_storage_depleted_gas_field_annual_losses` | PJ |
| Average production cost | `production_costs_per_mwh_hydrogen_by_storage_salt_cavern` | `production_costs_per_mwh_hydrogen_by_storage_depleted_gas_field` | EUR/MWh H2 |

KM 2050 has 12 GW/3 TWh salt-cavern and 48 GW/12 TWh depleted-field
storage. Avoid `*_storage_installed_volume_pj`: its ETSource expression omits
`number_of_units` and therefore returns per-unit volume. Also avoid using
`*_input_capacity` and `*_output_capacity` as nameplate parameters: these are
`MAX(hourly curve)` and can be realized operational peaks.

The pinned engine has no registered H2-storage `_efficiency` query. The ETSource
node definitions set `output.hydrogen = 1.0` and `storage.decay = 0.0` for both
technologies, so the Tulipa technical charging and discharging efficiencies are
1.0 with zero standing loss. Annual output divided by annual input is a realized
operational ratio and must not replace these technical parameters.

## Costs and dispatch

ETM documentation defines producer marginal cost as the cost of producing one
additional MWh. Electricity storage instead uses willingness-to-pay (charging),
willingness-to-accept (discharging), or forecast optimization. These bid settings
are dispatch policy, not technical variable O&M.

Current ETSource contains storage economic-performance OPEX/CAPEX queries, but
their names do not resolve in the pinned 2025.01 engine. Hydrogen
`production_costs_per_mwh_hydrogen_by_storage_*` divides CAPEX plus OPEX by
annual output; it is an average production cost and must not be entered as a
Tulipa marginal cost.

For Tulipa, use a verified variable O&M value when one is available. Otherwise,
use zero operational cost and let charging energy acquire its system marginal
cost endogenously. Do not use ETM WTP/WTA or average production costs as a
substitute without deliberately reproducing ETM's bidding behavior.

## Recommended aggregation boundary

Keep these storage groups separate because their duration and efficiency differ:

1. MV system batteries
2. Flow batteries (`IDES_storage` in the SR workbook)
3. HV OPAC (`MDES_storage` in the SR workbook)
4. Pumped storage
5. Household batteries
6. Transport batteries, optionally aggregated across vehicle classes after summing MW and MWh
7. Hydrogen salt caverns
8. Hydrogen depleted gas fields

Within a group, sum input MW, output MW and MWh independently. Derive aggregate
charging/discharging efficiencies using throughput-weighted losses, not a simple
capacity-weighted average.