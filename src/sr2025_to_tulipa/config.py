from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENARIO_REGISTRY = PROJECT_ROOT / "config" / "scenarios.csv"
DEFAULT_GQUERY_CATALOGUE = PROJECT_ROOT / "config" / "gqueries.csv"
DEFAULT_DEMAND_AGGREGATION = PROJECT_ROOT / "config" / "demand_aggregation.csv"
DEFAULT_MODEL_OPTIONS = PROJECT_ROOT / "config" / "model_options.csv"
DEFAULT_PROFILE_QUERIES = PROJECT_ROOT / "config" / "profile_queries.csv"
DEFAULT_ELECTRICITY_CAPACITY_AGGREGATION = (
    PROJECT_ROOT / "config" / "electricity_capacity_aggregation.csv"
)
DEFAULT_HYDROGEN_CAPACITY_AGGREGATION = (
    PROJECT_ROOT / "config" / "hydrogen_capacity_aggregation.csv"
)
DEFAULT_METHANE_SUPPLY_AGGREGATION = (
    PROJECT_ROOT / "config" / "methane_supply_aggregation.csv"
)
DEFAULT_NATURAL_GAS_PROFILE_PARTICIPANTS = (
    PROJECT_ROOT / "config" / "natural_gas_profile_participants.csv"
)


@dataclass(frozen=True)
class ScenarioSeed:
    """Describe one featured NBNL scenario and year."""

    scenario_key: str
    scenario_name: str
    year: int
    saved_scenario_id: int
    model_version: str
    engine_base_url: str
    enabled: bool


@dataclass(frozen=True)
class GQuerySpec:
    """Describe one ETM query included in the source audit."""

    query_key: str
    carrier: str
    sector: str
    role: str
    expected_unit: str
    review_status: str


@dataclass(frozen=True)
class DemandAggregationRule:
    """Define how one ETM sector enters canonical demand."""

    carrier: str
    sector: str
    demand_group: str
    included: bool
    spatial_scope: str


@dataclass(frozen=True)
class ModelOptions:
    """Hold user-selectable model construction choices."""

    flexible_heat_mode: str
    industrial_heat_mode: str
    agriculture_heat_mode: str
    dsr_mode: str
    add_network_losses_to_demand: bool
    add_power_sector_own_use_to_demand: bool


@dataclass(frozen=True)
class ProfileQuerySpec:
    """Describe one ETM curve used to build a sector profile."""

    carrier: str
    sector: str
    component: str
    query_key: str
    expected_unit: str
    expected_hours: int
    boundary_type: str
    annual_query_key: str
    review_status: str


@dataclass(frozen=True)
class CapacityAggregationRule:
    """Map one native capacity query to a canonical asset group."""

    query_key: str
    asset_group: str
    fuel: str
    technology: str
    chp: bool
    ccs: bool
    operating_mode: str
    included: bool
    reason: str
    minimum_capacity_mw: float


@dataclass(frozen=True)
class MethaneSupplyAggregationRule:
    """Map one ETM methane supply route to a canonical asset group."""

    route: str
    supply_group: str
    resource: str
    technology: str
    included: bool
    reason: str
    annual_query_key: str
    curve_query_keys: tuple[str, ...]
    boundary: str


@dataclass(frozen=True)
class NaturalGasProfileParticipant:
    """Classify one ETM network-gas input participant for profile assembly."""

    participant: str
    sector: str
    category: str
    inclusion_mode: str
    reason: str


def load_scenario_registry(path: Path = DEFAULT_SCENARIO_REGISTRY) -> list[ScenarioSeed]:
    """Load and validate the featured scenario registry."""
    with path.open(encoding="utf-8", newline="") as registry_file:
        rows = list(csv.DictReader(registry_file))

    scenarios = [_parse_scenario(row) for row in rows]
    _validate_scenarios(scenarios)
    return scenarios


def load_gquery_catalogue(
    path: Path = DEFAULT_GQUERY_CATALOGUE,
) -> list[GQuerySpec]:
    """Load and validate the configured ETM query catalogue."""
    with path.open(encoding="utf-8", newline="") as catalogue_file:
        rows = list(csv.DictReader(catalogue_file))

    queries = [GQuerySpec(**row) for row in rows]
    keys = {query.query_key for query in queries}
    if not queries:
        raise ValueError("The gquery catalogue is empty.")
    if len(keys) != len(queries):
        raise ValueError("Gquery keys must be unique.")
    return queries


def load_demand_aggregation(
    path: Path = DEFAULT_DEMAND_AGGREGATION,
) -> list[DemandAggregationRule]:
    """Load the approved sector aggregation and inclusion rules."""
    with path.open(encoding="utf-8", newline="") as policy_file:
        rows = list(csv.DictReader(policy_file))

    rules = [
        DemandAggregationRule(
            carrier=row["carrier"],
            sector=row["sector"],
            demand_group=row["demand_group"],
            included=row["included"].strip().lower() == "true",
            spatial_scope=row["spatial_scope"],
        )
        for row in rows
    ]
    keys = {(rule.carrier, rule.sector) for rule in rules}
    if len(keys) != len(rules):
        raise ValueError("Demand aggregation carrier and sector keys must be unique.")
    required = {
        (carrier, sector)
        for carrier in {"electricity", "hydrogen", "methane"}
        for sector in {
            "households",
            "buildings",
            "industry",
            "transport",
            "agriculture",
            "other",
            "bunkers",
            "energy",
        }
    }
    missing = required - keys
    if missing:
        raise ValueError(f"Demand aggregation is missing required rules: {sorted(missing)}")
    return rules


def load_model_options(path: Path = DEFAULT_MODEL_OPTIONS) -> ModelOptions:
    """Load validated user-selectable model options."""
    with path.open(encoding="utf-8", newline="") as options_file:
        rows = list(csv.DictReader(options_file))

    values = {row["option"]: row["value"].strip().lower() for row in rows}
    allowed = {
        row["option"]: set(row["allowed_values"].strip().lower().split("|"))
        for row in rows
    }
    expected = set(ModelOptions.__dataclass_fields__)
    if set(values) != expected:
        raise ValueError("Model options must match the ModelOptions fields exactly.")
    for option, value in values.items():
        if value not in allowed[option]:
            raise ValueError(f"Invalid value for {option}: {value}.")
    return ModelOptions(
        flexible_heat_mode=values["flexible_heat_mode"],
        industrial_heat_mode=values["industrial_heat_mode"],
        agriculture_heat_mode=values["agriculture_heat_mode"],
        dsr_mode=values["dsr_mode"],
        add_network_losses_to_demand=_option_bool(
            values, "add_network_losses_to_demand"
        ),
        add_power_sector_own_use_to_demand=_option_bool(
            values, "add_power_sector_own_use_to_demand"
        ),
    )


def update_model_options(
    updates: dict[str, str], path: Path = DEFAULT_MODEL_OPTIONS
) -> ModelOptions:
    """Validate and persist selected model options while preserving metadata."""
    with path.open(encoding="utf-8", newline="") as options_file:
        reader = csv.DictReader(options_file)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if fieldnames is None:
        raise ValueError("The model options file has no header.")

    known = {row["option"] for row in rows}
    if set(updates) != known:
        raise ValueError("Updates must provide every configured model option.")
    for row in rows:
        value = updates[row["option"]].strip().lower()
        allowed = set(row["allowed_values"].strip().lower().split("|"))
        if value not in allowed:
            raise ValueError(f"Invalid value for {row['option']}: {value}.")
        row["value"] = value

    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as options_file:
        writer = csv.DictWriter(options_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)
    return load_model_options(path)


def _option_bool(values: dict[str, str], option: str) -> bool:
    """Convert one validated model option to a boolean."""
    return values[option] == "true"


def load_profile_queries(
    path: Path = DEFAULT_PROFILE_QUERIES,
) -> list[ProfileQuerySpec]:
    """Load and validate candidate ETM profile queries."""
    with path.open(encoding="utf-8", newline="") as profile_file:
        rows = list(csv.DictReader(profile_file))

    queries = [
        ProfileQuerySpec(
            carrier=row["carrier"],
            sector=row["sector"],
            component=row["component"],
            query_key=row["query_key"],
            expected_unit=row["expected_unit"],
            expected_hours=int(row["expected_hours"]),
            boundary_type=row["boundary_type"],
            annual_query_key=row["annual_query_key"],
            review_status=row["review_status"],
        )
        for row in rows
    ]
    keys = {query.query_key for query in queries}
    if not queries:
        raise ValueError("The profile query catalogue is empty.")
    if len(keys) != len(queries):
        raise ValueError("Profile query keys must be unique.")
    if any(query.expected_hours != 8760 for query in queries):
        raise ValueError("Profile queries must contain exactly 8760 hours.")
    return queries


def load_electricity_capacity_aggregation(
    path: Path = DEFAULT_ELECTRICITY_CAPACITY_AGGREGATION,
) -> list[CapacityAggregationRule]:
    """Load approved mappings from ETM electricity capacities to asset groups."""
    return _load_capacity_aggregation(path, "electricity")


def load_hydrogen_capacity_aggregation(
    path: Path = DEFAULT_HYDROGEN_CAPACITY_AGGREGATION,
) -> list[CapacityAggregationRule]:
    """Load approved mappings from ETM hydrogen capacities to asset groups."""
    return _load_capacity_aggregation(path, "hydrogen")


def _load_capacity_aggregation(
    path: Path, carrier: str
) -> list[CapacityAggregationRule]:
    """Load and validate one carrier's capacity aggregation rules."""
    with path.open(encoding="utf-8", newline="") as policy_file:
        rows = list(csv.DictReader(policy_file))

    rules = [
        CapacityAggregationRule(
            query_key=row["query_key"],
            asset_group=row["asset_group"],
            fuel=row["fuel"],
            technology=row["technology"],
            chp=row["chp"].strip().lower() == "true",
            ccs=row["ccs"].strip().lower() == "true",
            operating_mode=row["operating_mode"],
            included=row["included"].strip().lower() == "true",
            reason=row["reason"],
            minimum_capacity_mw=float(row.get("minimum_capacity_mw") or 0.0),
        )
        for row in rows
    ]
    keys = {rule.query_key for rule in rules}
    if not rules:
        raise ValueError(f"The {carrier} capacity aggregation is empty.")
    if len(keys) != len(rules):
        raise ValueError(f"{carrier.title()} capacity query keys must be unique.")
    return rules


def load_methane_supply_aggregation(
    path: Path = DEFAULT_METHANE_SUPPLY_AGGREGATION,
) -> list[MethaneSupplyAggregationRule]:
    """Load approved mappings from ETM methane routes to supply groups."""
    with path.open(encoding="utf-8", newline="") as policy_file:
        rows = list(csv.DictReader(policy_file))
    rules = [
        MethaneSupplyAggregationRule(
            route=row["route"],
            supply_group=row["supply_group"],
            resource=row["resource"],
            technology=row["technology"],
            included=row["included"].strip().lower() == "true",
            reason=row["reason"],
            annual_query_key=row["annual_query_key"],
            curve_query_keys=tuple(row["curve_query_key"].split("+")),
            boundary=row["boundary"],
        )
        for row in rows
    ]
    if not rules:
        raise ValueError("The methane supply aggregation is empty.")
    if len({rule.route for rule in rules}) != len(rules):
        raise ValueError("Methane supply routes must be unique.")
    allowed_boundaries = {"supply_route", "storage_withdrawal"}
    invalid = {rule.boundary for rule in rules} - allowed_boundaries
    if invalid:
        raise ValueError(f"Unknown methane supply boundaries: {sorted(invalid)}")
    return rules


def load_natural_gas_profile_participants(
    path: Path = DEFAULT_NATURAL_GAS_PROFILE_PARTICIPANTS,
) -> list[NaturalGasProfileParticipant]:
    """Load the strict participant-level natural-gas profile policy."""
    with path.open(encoding="utf-8", newline="") as policy_file:
        rows = list(csv.DictReader(policy_file))
    rules = [NaturalGasProfileParticipant(**row) for row in rows]
    if not rules:
        raise ValueError("The natural-gas profile participant policy is empty.")
    if len({rule.participant for rule in rules}) != len(rules):
        raise ValueError("Natural-gas profile participants must be unique.")
    allowed_modes = {
        "always",
        "never",
        "flexible_heat",
        "industrial_heat",
        "agriculture_heat",
        "network_losses",
    }
    invalid = {rule.inclusion_mode for rule in rules} - allowed_modes
    if invalid:
        raise ValueError(f"Unknown natural-gas inclusion modes: {sorted(invalid)}")
    return rules


def _parse_scenario(row: dict[str, str]) -> ScenarioSeed:
    """Convert one CSV row to a typed scenario record."""
    return ScenarioSeed(
        scenario_key=row["scenario_key"],
        scenario_name=row["scenario_name"],
        year=int(row["year"]),
        saved_scenario_id=int(row["saved_scenario_id"]),
        model_version=row["model_version"],
        engine_base_url=row["engine_base_url"].rstrip("/"),
        enabled=row["enabled"].strip().lower() == "true",
    )


def _validate_scenarios(scenarios: list[ScenarioSeed]) -> None:
    """Reject duplicate or incomplete scenario registry entries."""
    natural_keys = {(item.scenario_key, item.year) for item in scenarios}
    saved_ids = {item.saved_scenario_id for item in scenarios}
    if len(natural_keys) != len(scenarios):
        raise ValueError("Scenario and year combinations must be unique.")
    if len(saved_ids) != len(scenarios):
        raise ValueError("Saved scenario IDs must be unique.")
    if not scenarios:
        raise ValueError("The scenario registry is empty.")
