# Demand query audit

The first audit uses ETM `final_demand` queries for electricity, hydrogen, and
natural gas and derivatives. Each carrier has one control total and eight
mutually exclusive sector components:

- households
- buildings
- industry
- transport
- agriculture
- energy
- other
- bunkers

All configured queries return PJ. The audit stores both ETM `present` and
`future`; Scenariorapport output will use `future` only after review.

## Important boundary

Final demand is energy delivered to final-demand nodes. It is not automatically
the complete load on a Tulipa carrier balance. Internal conversion demand,
storage charging, network losses, exports, and curtailment are separate model
flows and must not be added or omitted without an explicit balance design.

## Review decisions

The current policy is stored in `config/demand_aggregation.csv`:

- households, buildings, industry, transport, agriculture, and other remain
   separate through municipality distribution and are aggregated only per
   sector and destination node;
- bunkers are included as a separate national demand group;
- the energy sector is retained in audit accounting but excluded from model
   demand because conversion use and storage charging are endogenous Tulipa
   flows;
- natural gas and derivatives is mapped to the canonical `methane` carrier.

Electricity bunkers remain national in country mode. HV-node export requires a
separate authoritative facility-to-node mapping; municipal domestic-demand
shares may not be used as a fallback. `config/model_options.csv` exposes
`add_network_losses_to_demand` and currently sets it to `true`, together with
power-sector own use. The dashboard can review or change both choices without
editing transformation code.

Run the audit with:

```powershell
.\.venv\Scripts\sr2025-demand-audit.exe
```

Review `output/audit/demand_gqueries.csv` for raw values and
`output/audit/demand_reconciliation.csv` for total-versus-sector checks.
`output/audit/demand_sector_summary.csv` shows the minimum and maximum PJ and
carrier share of each sector across all scenarios and years.
`output/audit/demand_aggregation_preview.csv` applies the approved grouping and
keeps excluded demand visible.