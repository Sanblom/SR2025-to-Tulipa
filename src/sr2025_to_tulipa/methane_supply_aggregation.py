from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.config import (
    MethaneSupplyAggregationRule,
)
from sr2025_to_tulipa.source_validation import SourceValidationError

@dataclass(frozen=True)
class MethaneSupplyRow:
    """Hold one native methane supply-route result."""

    scenario_key: str
    year: int
    route: str
    annual_supply_twh: float
    observed_peak_mw: float
    installed_output_capacity_mw: float

@dataclass(frozen=True)
class MethaneSupplyGroupRow:
    """Hold one approved grouped methane supply result."""

    scenario_key: str
    year: int
    supply_group: str
    annual_supply_twh: float
    summed_route_peak_mw: float
    installed_output_capacity_mw: float
    source_count: int

@dataclass(frozen=True)
class MethaneSupplyExclusionRow:
    """Record one methane route excluded from canonical supply."""

    scenario_key: str
    year: int
    route: str
    annual_supply_twh: float
    reason: str

def read_methane_supply_scan(path: Path) -> list[MethaneSupplyRow]:
    """Read native methane supply routes from the all-scenario audit."""
    with path.open(encoding="utf-8", newline="") as input_file:
        return [
            MethaneSupplyRow(
                scenario_key=row["scenario_key"],
                year=int(row["year"]),
                route=row["route"],
                annual_supply_twh=float(row["annual_supply_twh"]),
                observed_peak_mw=float(row["observed_peak_mw"]),
                installed_output_capacity_mw=float(
                    row["installed_output_capacity_mw"]
                ),
            )
            for row in csv.DictReader(input_file)
        ]

def aggregate_methane_supply(
    rows: list[MethaneSupplyRow], rules: list[MethaneSupplyAggregationRule]
) -> tuple[list[MethaneSupplyGroupRow], list[MethaneSupplyExclusionRow]]:
    """Apply approved grouping and exclusion rules to methane routes."""
    rule_map = {rule.route: rule for rule in rules}
    groups: dict[tuple[str, int, str], list[MethaneSupplyRow]] = {}
    exclusions: list[MethaneSupplyExclusionRow] = []
    for row in rows:
        rule = rule_map.get(row.route)
        if rule is None:
            raise SourceValidationError(f"No methane supply mapping for {row.route}.")
        if not rule.included:
            exclusions.append(
                MethaneSupplyExclusionRow(
                    scenario_key=row.scenario_key,
                    year=row.year,
                    route=row.route,
                    annual_supply_twh=row.annual_supply_twh,
                    reason=rule.reason,
                )
            )
            continue
        groups.setdefault(
            (row.scenario_key, row.year, rule.supply_group), []
        ).append(row)

    grouped = [
        MethaneSupplyGroupRow(
            scenario_key=key[0],
            year=key[1],
            supply_group=key[2],
            annual_supply_twh=sum(row.annual_supply_twh for row in values),
            summed_route_peak_mw=sum(row.observed_peak_mw for row in values),
            installed_output_capacity_mw=sum(
                row.installed_output_capacity_mw for row in values
            ),
            source_count=len(values),
        )
        for key, values in sorted(groups.items())
    ]
    return grouped, exclusions
