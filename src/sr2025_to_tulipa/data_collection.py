from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.config import (
    CapacityAggregationRule,
    MethaneSupplyAggregationRule,
    load_electricity_capacity_aggregation,
    load_hydrogen_capacity_aggregation,
    load_methane_supply_aggregation,
)
from sr2025_to_tulipa.csv_io import write_rows
from sr2025_to_tulipa.etm_client import EtmClient, GQueryResponse
from sr2025_to_tulipa.profile_collection import HOURS_PER_YEAR
from sr2025_to_tulipa.source_validation import (
    ScenarioInventoryRow,
    SourceValidationError,
)

PJ_PER_TWH = 3.6
WH_PER_TWH = 3.6e9


@dataclass(frozen=True)
class ElectricityCapacityRow:
    scenario_key: str
    year: int
    scenario_id: int
    query_key: str
    value: float
    unit: str


@dataclass(frozen=True)
class HydrogenCapacityRow:
    scenario_key: str
    year: int
    scenario_id: int
    query_key: str
    capacity_mw_hydrogen: float
    production_twh: float
    operating_cost_eur_per_mwh: float


@dataclass(frozen=True)
class MethaneSupplySourceRow:
    scenario_key: str
    year: int
    scenario_id: int
    route: str
    annual_query_key: str
    annual_supply_twh: float
    curve_query_key: str
    observed_peak_mw: float
    boundary: str
    installed_output_capacity_mw: float


def collect_source_data(
    inventory: list[ScenarioInventoryRow],
    electricity_rules: list[CapacityAggregationRule] | None = None,
    hydrogen_rules: list[CapacityAggregationRule] | None = None,
    methane_rules: list[MethaneSupplyAggregationRule] | None = None,
) -> tuple[
    list[ElectricityCapacityRow],
    list[HydrogenCapacityRow],
    list[MethaneSupplySourceRow],
]:
    """Collect the ETM source values required to construct Tulipa assets."""
    electricity_rules = electricity_rules or load_electricity_capacity_aggregation()
    hydrogen_rules = hydrogen_rules or load_hydrogen_capacity_aggregation()
    methane_rules = methane_rules or load_methane_supply_aggregation()
    electricity: list[ElectricityCapacityRow] = []
    hydrogen: list[HydrogenCapacityRow] = []
    methane: list[MethaneSupplySourceRow] = []

    for scenario in inventory:
        with EtmClient(scenario.engine_base_url) as client:
            electricity_response = client.query_scenario(
                scenario.scenario_id,
                [rule.query_key for rule in electricity_rules],
            )
            hydrogen_response = client.query_scenario(
                scenario.scenario_id,
                [rule.query_key for rule in hydrogen_rules],
            )
            methane_response = client.query_scenario(
                scenario.scenario_id,
                sorted(
                    {rule.annual_query_key for rule in methane_rules}
                    | {
                        query_key
                        for rule in methane_rules
                        for query_key in rule.curve_query_keys
                    }
                ),
            )
        electricity.extend(
            electricity_capacity_rows(
                scenario, electricity_rules, electricity_response
            )
        )
        hydrogen.extend(
            hydrogen_capacity_rows(scenario, hydrogen_rules, hydrogen_response)
        )
        methane.extend(methane_supply_rows(scenario, methane_rules, methane_response))

    return electricity, hydrogen, methane


def electricity_capacity_rows(
    scenario: ScenarioInventoryRow,
    rules: list[CapacityAggregationRule],
    response: GQueryResponse,
) -> list[ElectricityCapacityRow]:
    """Convert nonzero ETM electricity-capacity values to source rows."""
    rows: list[ElectricityCapacityRow] = []
    for rule in rules:
        value = _scalar_value(response, rule.query_key, "MW")
        if value <= 0.0:
            continue
        rows.append(
            ElectricityCapacityRow(
                scenario.scenario_key,
                scenario.year,
                scenario.scenario_id,
                rule.query_key,
                value,
                "MW",
            )
        )
    return rows


def hydrogen_capacity_rows(
    scenario: ScenarioInventoryRow,
    rules: list[CapacityAggregationRule],
    response: GQueryResponse,
) -> list[HydrogenCapacityRow]:
    """Convert ETM hydrogen chart values to capacity, energy, and cost rows."""
    rows: list[HydrogenCapacityRow] = []
    for rule in rules:
        value = response.values.get(rule.query_key)
        future = value.get("future") if isinstance(value, dict) else None
        if not isinstance(future, dict):
            raise SourceValidationError(
                f"{rule.query_key} did not return a hydrogen chart object."
            )
        try:
            capacity = float(future["capacity"])
            production = float(future["production"]) / WH_PER_TWH
            operating_cost = float(future["operating_costs"])
        except (KeyError, TypeError, ValueError) as error:
            raise SourceValidationError(
                f"{rule.query_key} returned an invalid hydrogen chart."
            ) from error
        rows.append(
            HydrogenCapacityRow(
                scenario.scenario_key,
                scenario.year,
                scenario.scenario_id,
                rule.query_key,
                capacity,
                production,
                operating_cost,
            )
        )
    return rows


def methane_supply_rows(
    scenario: ScenarioInventoryRow,
    rules: list[MethaneSupplyAggregationRule],
    response: GQueryResponse,
) -> list[MethaneSupplySourceRow]:
    """Convert ETM methane route values and curves to source rows."""
    rows: list[MethaneSupplySourceRow] = []
    for rule in rules:
        hourly_components = [
            _curve_values(response, query_key) for query_key in rule.curve_query_keys
        ]
        hourly = [sum(values) for values in zip(*hourly_components, strict=True)]
        observed_peak = max(hourly)
        if rule.boundary == "storage_withdrawal":
            annual_supply = 0.0
            installed_capacity = _scalar_value(
                response, rule.annual_query_key, "MW"
            )
        else:
            annual_supply = _scalar_value(
                response, rule.annual_query_key, "PJ"
            ) / PJ_PER_TWH
            installed_capacity = 0.0
        rows.append(
            MethaneSupplySourceRow(
                scenario.scenario_key,
                scenario.year,
                scenario.scenario_id,
                rule.route,
                rule.annual_query_key,
                annual_supply,
                "+".join(rule.curve_query_keys),
                observed_peak,
                rule.boundary,
                installed_capacity,
            )
        )
    return rows


def write_source_data(
    electricity: list[ElectricityCapacityRow],
    hydrogen: list[HydrogenCapacityRow],
    methane: list[MethaneSupplySourceRow],
    output_dir: Path,
) -> None:
    """Write collected source values used by downstream grouping stages."""
    write_rows(electricity, output_dir / "electricity_capacity.csv")
    write_rows(hydrogen, output_dir / "hydrogen_capacity.csv")
    write_rows(methane, output_dir / "methane_supply.csv")


def _scalar_value(
    response: GQueryResponse, query_key: str, expected_unit: str
) -> float:
    value = response.values.get(query_key)
    if not isinstance(value, dict) or value.get("unit") != expected_unit:
        raise SourceValidationError(
            f"{query_key} did not return a {expected_unit} scalar."
        )
    try:
        return float(value["future"])
    except (KeyError, TypeError, ValueError) as error:
        raise SourceValidationError(
            f"{query_key} did not return a numeric future value."
        ) from error


def _curve_values(response: GQueryResponse, query_key: str) -> list[float]:
    value = response.values.get(query_key)
    future = value.get("future") if isinstance(value, dict) else None
    if not isinstance(future, list) or len(future) != HOURS_PER_YEAR:
        length = len(future) if isinstance(future, list) else "non-list"
        raise SourceValidationError(
            f"{query_key} returned {length} hours, expected {HOURS_PER_YEAR}."
        )
    try:
        return [float(item) for item in future]
    except (TypeError, ValueError) as error:
        raise SourceValidationError(f"{query_key} returned a nonnumeric curve.") from error
