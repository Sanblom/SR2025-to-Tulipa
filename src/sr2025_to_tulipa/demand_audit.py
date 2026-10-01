from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from sr2025_to_tulipa.config import (
    DemandAggregationRule,
    GQuerySpec,
    load_demand_aggregation,
    load_gquery_catalogue,
    load_scenario_registry,
)
from sr2025_to_tulipa.etm_client import EtmClient, GQueryResponse
from sr2025_to_tulipa.source_validation import (
    ScenarioInventoryRow,
    SourceValidationError,
    build_scenario_inventory,
)


@dataclass(frozen=True)
class DemandAuditRow:
    """Record one raw ETM demand query value with provenance."""

    scenario_key: str
    scenario_name: str
    year: int
    saved_scenario_id: int
    scenario_id: int
    carrier: str
    sector: str
    role: str
    query_key: str
    present_pj: float
    future_pj: float
    source_unit: str
    review_status: str
    engine_base_url: str
    retrieved_at: str
    response_checksum: str


@dataclass(frozen=True)
class ReconciliationRow:
    """Compare one carrier total with the sum of its sector values."""

    scenario_key: str
    year: int
    carrier: str
    control_total_pj: float
    sector_sum_pj: float
    difference_pj: float
    relative_difference: float
    status: str


@dataclass(frozen=True)
class SectorSummaryRow:
    """Summarize one sector's range across scenarios and years."""

    carrier: str
    sector: str
    minimum_pj: float
    maximum_pj: float
    minimum_share: float
    maximum_share: float
    always_zero: bool


@dataclass(frozen=True)
class AggregationPreviewRow:
    """Show one proposed canonical demand group before transformation."""

    scenario_key: str
    year: int
    carrier: str
    demand_group: str
    included: bool
    spatial_scope: str
    future_pj: float
    share_of_control_total: float


def collect_demand_audit(
    inventory: list[ScenarioInventoryRow], queries: list[GQuerySpec]
) -> list[DemandAuditRow]:
    """Collect every configured demand query for every scenario."""
    rows: list[DemandAuditRow] = []
    query_keys = [query.query_key for query in queries]

    for scenario in inventory:
        with EtmClient(scenario.engine_base_url) as client:
            response = client.query_scenario(scenario.scenario_id, query_keys)
        rows.extend(_audit_rows(scenario, queries, response))

    return rows


def reconcile_demand(rows: list[DemandAuditRow]) -> list[ReconciliationRow]:
    """Check that sector demand adds up to each carrier control total."""
    groups: dict[tuple[str, int, str], list[DemandAuditRow]] = {}
    for row in rows:
        groups.setdefault((row.scenario_key, row.year, row.carrier), []).append(row)

    reconciliations: list[ReconciliationRow] = []
    for (scenario_key, year, carrier), group in sorted(groups.items()):
        controls = [row.future_pj for row in group if row.role == "control_total"]
        if len(controls) != 1:
            raise SourceValidationError(
                f"Expected one {carrier} control total for {scenario_key} {year}."
            )
        control = controls[0]
        sector_sum = sum(
            row.future_pj for row in group if row.role == "sector_component"
        )
        difference = sector_sum - control
        relative = difference / control if control else difference
        reconciliations.append(
            ReconciliationRow(
                scenario_key=scenario_key,
                year=year,
                carrier=carrier,
                control_total_pj=control,
                sector_sum_pj=sector_sum,
                difference_pj=difference,
                relative_difference=relative,
                status="pass" if abs(difference) <= 1e-9 else "review",
            )
        )
    return reconciliations


def summarize_sector_shares(rows: list[DemandAuditRow]) -> list[SectorSummaryRow]:
    """Show how large each sector is relative to its carrier total."""
    totals = {
        (row.scenario_key, row.year, row.carrier): row.future_pj
        for row in rows
        if row.role == "control_total"
    }
    groups: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        if row.role != "sector_component":
            continue
        total = totals[(row.scenario_key, row.year, row.carrier)]
        share = row.future_pj / total if total else 0.0
        groups.setdefault((row.carrier, row.sector), []).append(
            (row.future_pj, share)
        )

    summaries: list[SectorSummaryRow] = []
    for (carrier, sector), values in sorted(groups.items()):
        demand = [value for value, _ in values]
        shares = [share for _, share in values]
        summaries.append(
            SectorSummaryRow(
                carrier=carrier,
                sector=sector,
                minimum_pj=min(demand),
                maximum_pj=max(demand),
                minimum_share=min(shares),
                maximum_share=max(shares),
                always_zero=all(value == 0.0 for value in demand),
            )
        )
    return summaries


def preview_aggregation(
    rows: list[DemandAuditRow], rules: list[DemandAggregationRule]
) -> list[AggregationPreviewRow]:
    """Apply approved grouping rules without creating model demand."""
    rule_map = {(rule.carrier, rule.sector): rule for rule in rules}
    totals = {
        (row.scenario_key, row.year, row.carrier): row.future_pj
        for row in rows
        if row.role == "control_total"
    }
    groups: dict[tuple[str, int, str, str, bool, str], float] = {}
    for row in rows:
        if row.role != "sector_component":
            continue
        rule = rule_map.get((row.carrier, row.sector))
        if rule is None:
            raise SourceValidationError(
                f"No demand aggregation rule for {row.carrier} {row.sector}."
            )
        key = (
            row.scenario_key,
            row.year,
            row.carrier,
            rule.demand_group,
            rule.included,
            rule.spatial_scope,
        )
        groups[key] = groups.get(key, 0.0) + row.future_pj

    preview: list[AggregationPreviewRow] = []
    for key, value in sorted(groups.items()):
        scenario_key, year, carrier, demand_group, included, spatial_scope = key
        control = totals[(scenario_key, year, carrier)]
        preview.append(
            AggregationPreviewRow(
                scenario_key=scenario_key,
                year=year,
                carrier=carrier,
                demand_group=demand_group,
                included=included,
                spatial_scope=spatial_scope,
                future_pj=value,
                share_of_control_total=value / control if control else 0.0,
            )
        )
    return preview


def write_rows(rows: list[object], output_path: Path) -> None:
    """Write dataclass records to a CSV file."""
    if not rows:
        raise SourceValidationError(f"Cannot write empty audit file {output_path}.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first_row = asdict(rows[0])  # type: ignore[arg-type]
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(first_row))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)  # type: ignore[arg-type]


def _audit_rows(
    scenario: ScenarioInventoryRow,
    queries: list[GQuerySpec],
    response: GQueryResponse,
) -> list[DemandAuditRow]:
    """Convert one raw ETM response to reviewable demand rows."""
    rows: list[DemandAuditRow] = []
    for query in queries:
        value = response.values.get(query.query_key)
        if not isinstance(value, dict):
            raise SourceValidationError(f"ETM did not return {query.query_key}.")
        unit = str(value.get("unit"))
        if unit != query.expected_unit:
            raise SourceValidationError(
                f"{query.query_key} returned {unit}, expected {query.expected_unit}."
            )
        rows.append(
            DemandAuditRow(
                scenario_key=scenario.scenario_key,
                scenario_name=scenario.scenario_name,
                year=scenario.year,
                saved_scenario_id=scenario.saved_scenario_id,
                scenario_id=scenario.scenario_id,
                carrier=query.carrier,
                sector=query.sector,
                role=query.role,
                query_key=query.query_key,
                present_pj=float(value["present"]),
                future_pj=float(value["future"]),
                source_unit=unit,
                review_status=query.review_status,
                engine_base_url=scenario.engine_base_url,
                retrieved_at=response.retrieved_at,
                response_checksum=response.response_checksum,
            )
        )
    return rows


def main() -> None:
    """Collect national demand queries and write review files."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/audit"))
    args = parser.parse_args()

    inventory = build_scenario_inventory(load_scenario_registry())
    rows = collect_demand_audit(inventory, load_gquery_catalogue())
    reconciliation = reconcile_demand(rows)
    sector_summary = summarize_sector_shares(rows)
    aggregation_preview = preview_aggregation(rows, load_demand_aggregation())
    write_rows(rows, args.output_dir / "demand_gqueries.csv")
    write_rows(reconciliation, args.output_dir / "demand_reconciliation.csv")
    write_rows(sector_summary, args.output_dir / "demand_sector_summary.csv")
    write_rows(
        aggregation_preview,
        args.output_dir / "demand_aggregation_preview.csv",
    )
    print(
        f"Collected {len(rows)} values, {len(reconciliation)} reconciliations, "
        f"{len(sector_summary)} sector summaries, and "
        f"{len(aggregation_preview)} preview rows: {args.output_dir}"
    )


if __name__ == "__main__":
    main()