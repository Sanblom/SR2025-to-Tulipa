from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.config import (
    DemandAggregationRule,
    GQuerySpec,
    ModelOptions,
    NaturalGasProfileParticipant,
    load_gquery_catalogue,
    load_demand_aggregation,
    load_model_options,
    load_natural_gas_profile_participants,
    load_profile_queries,
    load_scenario_registry,
)
from sr2025_to_tulipa.demand_audit import write_rows
from sr2025_to_tulipa.etm_client import CurveCsvResponse, EtmClient
from sr2025_to_tulipa.profile_audit import (
    HOURS_PER_YEAR,
    MWH_TO_PJ,
    SectorProfileRow,
    collect_profile_audit,
)
from sr2025_to_tulipa.source_validation import (
    ScenarioInventoryRow,
    SourceValidationError,
    build_scenario_inventory,
)

MATERIALITY_TWH = 0.1
MATERIALITY_PJ = MATERIALITY_TWH * 3.6
NETWORK_GAS_TRANSPORT_QUERY = "final_demand_of_network_gas_in_transport_energetic"
END_USE_SECTORS = (
    "households",
    "buildings",
    "industry",
    "transport",
    "agriculture",
    "other",
    "bunkers",
)
HYDROGEN_CENTRAL_HEAT_PARTICIPANTS = (
    "energy_heat_burner_ht_hydrogen.input (MW)",
    "energy_heat_burner_lt_hydrogen.input (MW)",
    "energy_heat_burner_mt_hydrogen.input (MW)",
)


@dataclass(frozen=True)
class CanonicalProfileRow:
    """Record one normalized hourly carrier-demand value."""

    scenario_key: str
    year: int
    carrier: str
    sector: str
    hour: int
    raw_mw: float
    normalized_mw: float
    scale_factor: float


@dataclass(frozen=True)
class GroupedDemandProfileRow:
    """Record one hourly demand value after applying approved demand groups."""

    scenario_key: str
    year: int
    carrier: str
    demand_group: str
    hour: int
    raw_mw: float
    normalized_mw: float


@dataclass(frozen=True)
class CanonicalReconciliationRow:
    """Explain how one source shape was reconciled to annual demand."""

    scenario_key: str
    year: int
    carrier: str
    sector: str
    annual_demand_pj: float | None
    raw_integral_pj: float
    difference_pj: float | None
    difference_twh: float | None
    scale_factor: float | None
    status: str


@dataclass(frozen=True)
class ParticipantSummaryRow:
    """Record one raw natural-gas participant integral and filter decision."""

    scenario_key: str
    year: int
    scenario_id: int
    participant: str
    sector: str
    category: str
    inclusion_mode: str
    included: bool
    reason: str
    integral_pj: float
    retrieved_at: str
    response_checksum: str


@dataclass(frozen=True)
class ElectricityParticipantRule:
    """Classify one ETM merit-order electricity consumer."""

    sector: str
    category: str
    inclusion_mode: str
    reason: str


def normalize_sector_profile(
    scenario_key: str,
    year: int,
    carrier: str,
    sector: str,
    hourly_mw: list[float],
    annual_demand_pj: float | None,
    flat_if_missing: bool = False,
) -> tuple[list[CanonicalProfileRow], CanonicalReconciliationRow]:
    """Scale one hourly shape to its annual target and retain raw values."""
    if len(hourly_mw) != HOURS_PER_YEAR:
        raise SourceValidationError(
            f"{carrier} {sector} returned {len(hourly_mw)} hours."
        )
    raw_pj = sum(hourly_mw) * MWH_TO_PJ
    if annual_demand_pj is None:
        scale = 1.0
        difference = None
        status = "auxiliary_demand"
    elif raw_pj == 0.0:
        if annual_demand_pj > 0.0 and flat_if_missing:
            hourly_mw = [annual_demand_pj / MWH_TO_PJ / HOURS_PER_YEAR] * HOURS_PER_YEAR
            raw_pj = annual_demand_pj
            scale = 1.0
            difference = 0.0
            status = "flat_proxy"
        else:
            scale = None
            difference = -annual_demand_pj
            status = "pass" if annual_demand_pj == 0.0 else "missing_profile"
    else:
        scale = annual_demand_pj / raw_pj
        difference = raw_pj - annual_demand_pj
        material = abs(difference) > MATERIALITY_PJ or math.isclose(
            abs(difference), MATERIALITY_PJ, abs_tol=1e-12
        )
        status = "normalized" if material else "pass"

    rows = []
    if scale is not None:
        rows = [
            CanonicalProfileRow(
                scenario_key=scenario_key,
                year=year,
                carrier=carrier,
                sector=sector,
                hour=hour,
                raw_mw=value,
                normalized_mw=value * scale,
                scale_factor=scale,
            )
            for hour, value in enumerate(hourly_mw, start=1)
        ]
    reconciliation = CanonicalReconciliationRow(
        scenario_key=scenario_key,
        year=year,
        carrier=carrier,
        sector=sector,
        annual_demand_pj=annual_demand_pj,
        raw_integral_pj=raw_pj,
        difference_pj=difference,
        difference_twh=difference / 3.6 if difference is not None else None,
        scale_factor=scale,
        status=status,
    )
    return rows, reconciliation


def apply_demand_grouping(
    profiles: list[CanonicalProfileRow], rules: list[DemandAggregationRule]
) -> list[GroupedDemandProfileRow]:
    """Apply approved inclusion and grouping rules to hourly demand profiles."""
    rule_map = {(rule.carrier, rule.sector): rule for rule in rules}
    grouped: dict[tuple[str, int, str, str, int], tuple[float, float]] = {}
    for profile in profiles:
        rule = rule_map.get((profile.carrier, profile.sector))
        if rule is None:
            raise SourceValidationError(
                f"No demand aggregation rule for {profile.carrier} {profile.sector}."
            )
        if not rule.included:
            continue
        key = (
            profile.scenario_key,
            profile.year,
            profile.carrier,
            rule.demand_group,
            profile.hour,
        )
        raw_mw, normalized_mw = grouped.get(key, (0.0, 0.0))
        grouped[key] = (
            raw_mw + profile.raw_mw,
            normalized_mw + profile.normalized_mw,
        )

    return [
        GroupedDemandProfileRow(*key, raw_mw, normalized_mw)
        for key, (raw_mw, normalized_mw) in sorted(grouped.items())
    ]


def aggregate_hydrogen_profiles(
    inventory: list[ScenarioInventoryRow], options: ModelOptions
) -> tuple[list[CanonicalProfileRow], list[CanonicalReconciliationRow]]:
    """Normalize reviewed hydrogen sector curves to annual demand."""
    if options.industrial_heat_mode == "heat_demand" or options.agriculture_heat_mode == "heat_demand":
        raise SourceValidationError(
            "Hydrogen useful-heat export is selected but not implemented yet."
        )
    _, sector_rows, audit = collect_profile_audit(inventory, load_profile_queries())
    targets = {
        (row.scenario_key, row.year, row.sector): row.annual_demand_pj
        for row in audit
    }
    grouped: dict[tuple[str, int, str], list[float]] = {}
    for row in sector_rows:
        key = (row.scenario_key, row.year, row.sector)
        grouped.setdefault(key, [0.0] * HOURS_PER_YEAR)[row.hour - 1] = row.future_mw

    profiles: list[CanonicalProfileRow] = []
    reconciliations: list[CanonicalReconciliationRow] = []
    for key, hourly in sorted(grouped.items()):
        scenario_key, year, sector = key
        rows, reconciliation = normalize_sector_profile(
            scenario_key, year, "hydrogen", sector, hourly, targets[key]
        )
        profiles.extend(rows)
        reconciliations.append(reconciliation)

    if options.flexible_heat_mode == "final_energy_demand":
        for scenario in inventory:
            with EtmClient(scenario.engine_base_url) as client:
                curve = client.get_curve_csv(scenario.scenario_id, "hydrogen")
            missing = set(HYDROGEN_CENTRAL_HEAT_PARTICIPANTS) - set(curve.fieldnames)
            if missing or len(curve.rows) != HOURS_PER_YEAR:
                raise SourceValidationError(
                    f"Hydrogen central-heat curve mismatch; missing={sorted(missing)}, "
                    f"hours={len(curve.rows)}"
                )
            hourly = [
                sum(float(row[participant] or 0.0) for participant in HYDROGEN_CENTRAL_HEAT_PARTICIPANTS)
                for row in curve.rows
            ]
            rows, reconciliation = normalize_sector_profile(
                scenario.scenario_key,
                scenario.year,
                "hydrogen",
                "central_heat",
                hourly,
                None,
            )
            profiles.extend(rows)
            reconciliations.append(reconciliation)
    return profiles, reconciliations


def aggregate_electricity_profiles(
    inventory: list[ScenarioInventoryRow],
    options: ModelOptions,
    annual_queries: list[GQuerySpec],
) -> tuple[
    list[CanonicalProfileRow],
    list[CanonicalReconciliationRow],
    list[ParticipantSummaryRow],
]:
    """Compose sector electricity profiles from merit-order consumers."""
    if (
        options.industrial_heat_mode == "heat_demand"
        or options.agriculture_heat_mode == "heat_demand"
    ):
        raise SourceValidationError(
            "Electricity useful-heat export is selected but not implemented yet."
        )
    query_map = {
        query.sector: query.query_key
        for query in annual_queries
        if query.carrier == "electricity" and query.role == "sector_component"
    }
    if set(query_map) != set(END_USE_SECTORS) | {"energy"}:
        raise SourceValidationError("Electricity annual sector queries are incomplete.")

    profiles: list[CanonicalProfileRow] = []
    reconciliations: list[CanonicalReconciliationRow] = []
    summaries: list[ParticipantSummaryRow] = []
    for scenario in inventory:
        with EtmClient(scenario.engine_base_url) as client:
            curve = client.get_curve_csv(scenario.scenario_id, "merit_order")
            annual = client.query_scenario(
                scenario.scenario_id,
                [query_map[sector] for sector in END_USE_SECTORS],
            )
        if len(curve.rows) != HOURS_PER_YEAR:
            raise SourceValidationError(
                f"Electricity merit-order curve returned {len(curve.rows)} hours."
            )
        participants = [
            name for name in curve.fieldnames if name.endswith(".input (MW)")
        ]
        grouped = {sector: [0.0] * HOURS_PER_YEAR for sector in END_USE_SECTORS}
        for sector in {
            "central_heat",
            "losses",
            "power_sector_own_use",
            "transformation",
            "dsr",
            "household_storage_exchange",
            "transport_storage_exchange",
        }:
            grouped[sector] = [0.0] * HOURS_PER_YEAR

        for participant in participants:
            rule = _classify_electricity_participant(participant)
            included = _electricity_participant_included(rule, options)
            values = _electricity_participant_values(curve, participant)
            summaries.append(
                ParticipantSummaryRow(
                    scenario_key=scenario.scenario_key,
                    year=scenario.year,
                    scenario_id=scenario.scenario_id,
                    participant=participant,
                    sector=rule.sector,
                    category=rule.category,
                    inclusion_mode=rule.inclusion_mode,
                    included=included,
                    reason=rule.reason,
                    integral_pj=sum(values) * MWH_TO_PJ,
                    retrieved_at=curve.retrieved_at,
                    response_checksum=curve.response_checksum,
                )
            )
            if not included:
                continue
            target_sector = _electricity_target_sector(rule, options)
            grouped[target_sector] = [
                total + value
                for total, value in zip(
                    grouped[target_sector], values, strict=True
                )
            ]

        for sector, hourly in grouped.items():
            target = (
                _annual_value(annual.values, query_map[sector])
                if sector in END_USE_SECTORS
                else None
            )
            rows, reconciliation = normalize_sector_profile(
                scenario.scenario_key,
                scenario.year,
                "electricity",
                sector,
                hourly,
                target,
            )
            profiles.extend(rows)
            reconciliations.append(reconciliation)
    return profiles, reconciliations, summaries


def _electricity_participant_values(
    curve: CurveCsvResponse, participant: str
) -> list[float]:
    """Return electricity consumption, net of local P2P battery discharge."""
    values = [float(row[participant] or 0.0) for row in curve.rows]
    if not any(
        marker in participant
        for marker in ("_flexibility_p2p_", "_dsr_load_shifting_")
    ):
        return values
    output = participant.replace(".input (MW)", ".output (MW)")
    if output not in curve.fieldnames:
        raise SourceValidationError(
            f"Electricity P2P participant has no paired output: {participant}."
        )
    return [
        demand - float(row[output] or 0.0)
        for demand, row in zip(values, curve.rows, strict=True)
    ]


def _classify_electricity_participant(
    participant: str,
) -> ElectricityParticipantRule:
    """Assign one merit-order consumer to a strict model boundary."""
    if participant.startswith("households_"):
        if "_flexibility_p2p_" in participant:
            return ElectricityParticipantRule(
                "households",
                "storage_exchange",
                "always",
                "Net household battery exchange",
            )
        return ElectricityParticipantRule(
            "households", "final_demand", "always", "Household final demand"
        )
    if participant.startswith("buildings_"):
        return ElectricityParticipantRule(
            "buildings", "final_demand", "always", "Building final demand"
        )
    if participant.startswith("transport_"):
        if "_flexibility_p2p_" in participant:
            return ElectricityParticipantRule(
                "transport",
                "storage_exchange",
                "always",
                "Net vehicle battery exchange",
            )
        return ElectricityParticipantRule(
            "transport", "final_demand", "always", "Transport final demand"
        )
    if participant.startswith("bunkers_"):
        return ElectricityParticipantRule(
            "bunkers", "final_demand", "always", "Bunker final demand"
        )
    if participant.startswith("other_"):
        return ElectricityParticipantRule(
            "other", "final_demand", "always", "Other final demand"
        )
    if participant.startswith("agriculture_"):
        heat = any(
            marker in participant
            for marker in ("flexibility_p2h", "geothermal", "heatpump")
        )
        return ElectricityParticipantRule(
            "agriculture",
            "heat" if heat else "final_demand",
            "agriculture_heat" if heat else "always",
            "Agriculture heat" if heat else "Agriculture final demand",
        )
    if participant.startswith("industry_"):
        if "_dsr_load_shifting_" in participant:
            return ElectricityParticipantRule(
                "industry", "dsr", "dsr", "Industrial load shifting"
            )
        heat = any(
            marker in participant
            for marker in (
                "flexibility_p2h",
                "_heater_electricity",
                "_heatpump_",
                "steam_recompression",
                "heat_well_geothermal",
            )
        )
        return ElectricityParticipantRule(
            "industry",
            "heat" if heat else "final_demand",
            "industrial_heat" if heat else "always",
            "Industrial heat" if heat else "Industrial final demand",
        )
    if not participant.startswith("energy_"):
        raise SourceValidationError(
            f"Unknown electricity merit-order participant: {participant}."
        )
    if "network_loss" in participant:
        return ElectricityParticipantRule(
            "energy", "losses", "network_losses", "Electricity network losses"
        )
    if "sector_own_use" in participant:
        return ElectricityParticipantRule(
            "energy",
            "power_sector_own_use",
            "power_sector_own_use",
            "Power-sector own use",
        )
    if participant.startswith("energy_heat_"):
        return ElectricityParticipantRule(
            "energy", "central_heat", "flexible_heat", "Central heat production"
        )
    excluded_markers = (
        "curtailment",
        "curtailed",
        "batteries",
        "pumped_storage",
        "hydrogen_",
        "interconnector_",
    )
    if any(marker in participant for marker in excluded_markers):
        return ElectricityParticipantRule(
            "energy", "endogenous", "never", "Endogenous asset or export"
        )
    return ElectricityParticipantRule(
        "energy",
        "transformation",
        "always",
        "Transformation output is not represented by a Tulipa conversion asset",
    )


def _electricity_participant_included(
    rule: ElectricityParticipantRule, options: ModelOptions
) -> bool:
    """Apply electricity participant options to one boundary rule."""
    if rule.inclusion_mode == "always":
        return True
    if rule.inclusion_mode == "never":
        return False
    if rule.inclusion_mode == "network_losses":
        return options.add_network_losses_to_demand
    if rule.inclusion_mode == "power_sector_own_use":
        return options.add_power_sector_own_use_to_demand
    option = {
        "flexible_heat": options.flexible_heat_mode,
        "industrial_heat": options.industrial_heat_mode,
        "agriculture_heat": options.agriculture_heat_mode,
        "dsr": options.dsr_mode,
    }.get(rule.inclusion_mode)
    if option is None:
        raise SourceValidationError(
            f"Unknown electricity inclusion mode: {rule.inclusion_mode}."
        )
    return option in {"final_energy_demand", "final_electricity_demand", "dsr"}


def _electricity_target_sector(
    rule: ElectricityParticipantRule, options: ModelOptions
) -> str:
    """Route an included electricity consumer to its canonical profile."""
    if rule.inclusion_mode == "flexible_heat":
        return "central_heat"
    if rule.inclusion_mode == "network_losses":
        return "losses"
    if rule.inclusion_mode == "power_sector_own_use":
        return "power_sector_own_use"
    if rule.inclusion_mode == "dsr" and options.dsr_mode == "dsr":
        return "dsr"
    if rule.category == "storage_exchange":
        return f"{rule.sector[:-1] if rule.sector == 'households' else rule.sector}_storage_exchange"
    if rule.category == "transformation":
        return "transformation"
    return rule.sector


def aggregate_natural_gas_profiles(
    inventory: list[ScenarioInventoryRow],
    options: ModelOptions,
    rules: list[NaturalGasProfileParticipant],
    annual_queries: list[GQuerySpec],
    raw_dir: Path | None = None,
) -> tuple[
    list[CanonicalProfileRow],
    list[CanonicalReconciliationRow],
    list[ParticipantSummaryRow],
]:
    """Filter network-gas participants and normalize sector demand shapes."""
    if options.industrial_heat_mode == "heat_demand" or options.agriculture_heat_mode == "heat_demand":
        raise SourceValidationError(
            "Natural-gas useful-heat export is selected but not implemented yet."
        )
    rule_map = {rule.participant: rule for rule in rules}
    query_map = {
        query.sector: query.query_key
        for query in annual_queries
        if query.carrier == "methane" and query.role == "sector_component"
    }
    if set(query_map) != set(END_USE_SECTORS) | {"energy"}:
        raise SourceValidationError("Natural-gas annual sector queries are incomplete.")

    profiles: list[CanonicalProfileRow] = []
    reconciliations: list[CanonicalReconciliationRow] = []
    summaries: list[ParticipantSummaryRow] = []
    for scenario in inventory:
        with EtmClient(scenario.engine_base_url) as client:
            curve = client.get_curve_csv(scenario.scenario_id, "network_gas")
            annual = client.query_scenario(
                scenario.scenario_id,
                [query_map[sector] for sector in END_USE_SECTORS]
                + [NETWORK_GAS_TRANSPORT_QUERY],
            )
        _validate_gas_schema(curve, rule_map)
        if raw_dir is not None:
            _write_raw_curve(curve, raw_dir / f"{scenario.scenario_key}_{scenario.year}.csv")

        grouped = {sector: [0.0] * HOURS_PER_YEAR for sector in END_USE_SECTORS}
        grouped["central_heat"] = [0.0] * HOURS_PER_YEAR
        grouped["losses"] = [0.0] * HOURS_PER_YEAR
        grouped["transformation"] = [0.0] * HOURS_PER_YEAR
        for participant, rule in rule_map.items():
            values = [float(row[participant] or 0.0) for row in curve.rows]
            included = _participant_included(rule, options)
            summaries.append(
                ParticipantSummaryRow(
                    scenario_key=scenario.scenario_key,
                    year=scenario.year,
                    scenario_id=scenario.scenario_id,
                    participant=participant,
                    sector=rule.sector,
                    category=rule.category,
                    inclusion_mode=rule.inclusion_mode,
                    included=included,
                    reason=rule.reason,
                    integral_pj=sum(values) * MWH_TO_PJ,
                    retrieved_at=curve.retrieved_at,
                    response_checksum=curve.response_checksum,
                )
            )
            if not included:
                continue
            if rule.inclusion_mode == "flexible_heat":
                target_sector = "central_heat"
            elif rule.inclusion_mode == "network_losses":
                target_sector = "losses"
            elif rule.category == "transformation":
                target_sector = "transformation"
            else:
                target_sector = rule.sector
            grouped[target_sector] = [
                total + value for total, value in zip(grouped[target_sector], values, strict=True)
            ]

        for sector, hourly in grouped.items():
            if sector in {"central_heat", "losses", "transformation"}:
                target = None
            elif sector == "transport":
                target = _annual_value(annual.values, NETWORK_GAS_TRANSPORT_QUERY)
            else:
                target = _annual_value(annual.values, query_map[sector])
            rows, reconciliation = normalize_sector_profile(
                scenario.scenario_key,
                scenario.year,
                "methane",
                sector,
                hourly,
                target,
                flat_if_missing=sector == "bunkers",
            )
            profiles.extend(rows)
            reconciliations.append(reconciliation)

        total_transport = _annual_value(annual.values, query_map["transport"])
        network_gas_transport = _annual_value(
            annual.values, NETWORK_GAS_TRANSPORT_QUERY
        )
        lng_rows, lng_reconciliation = normalize_sector_profile(
            scenario.scenario_key,
            scenario.year,
            "methane",
            "transport_lng",
            [0.0] * HOURS_PER_YEAR,
            max(0.0, total_transport - network_gas_transport),
            flat_if_missing=True,
        )
        profiles.extend(lng_rows)
        reconciliations.append(lng_reconciliation)
    return profiles, reconciliations, summaries


def _participant_included(
    rule: NaturalGasProfileParticipant, options: ModelOptions
) -> bool:
    """Apply one configured participant boundary to current model options."""
    if rule.inclusion_mode == "always":
        return True
    if rule.inclusion_mode == "never":
        return False
    option = {
        "flexible_heat": options.flexible_heat_mode,
        "industrial_heat": options.industrial_heat_mode,
        "agriculture_heat": options.agriculture_heat_mode,
    }.get(rule.inclusion_mode)
    if rule.inclusion_mode == "network_losses":
        return options.add_network_losses_to_demand
    if option is None:
        raise SourceValidationError(
            f"Unknown natural-gas participant inclusion mode: {rule.inclusion_mode}."
        )
    return option == "final_energy_demand"


def _validate_gas_schema(
    curve: CurveCsvResponse, rules: dict[str, NaturalGasProfileParticipant]
) -> None:
    """Reject added or missing ETM gas inputs before aggregation."""
    inputs = {name for name in curve.fieldnames if name.endswith(".input (MW)")}
    missing = set(rules) - inputs
    unmapped = inputs - set(rules)
    if missing or unmapped:
        raise SourceValidationError(
            f"Natural-gas participant mapping mismatch; missing={sorted(missing)}, "
            f"unmapped={sorted(unmapped)}"
        )
    if len(curve.rows) != HOURS_PER_YEAR:
        raise SourceValidationError(
            f"Network-gas curve returned {len(curve.rows)} hours."
        )


def _annual_value(values: dict[str, dict[str, object]], query_key: str) -> float:
    """Read one validated annual PJ target from a gquery response."""
    value = values.get(query_key)
    if not isinstance(value, dict) or value.get("unit") != "PJ":
        raise SourceValidationError(f"{query_key} did not return a PJ value.")
    return float(value["future"])


def _write_raw_curve(curve: CurveCsvResponse, path: Path) -> None:
    """Persist one untouched wide ETM curve table for source review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=curve.fieldnames)
        writer.writeheader()
        writer.writerows(curve.rows)


def main() -> None:
    """Build canonical and grouped national carrier-demand profiles."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/profiles"))
    parser.add_argument("--raw-dir", type=Path, default=Path("output/raw/network_gas"))
    args = parser.parse_args()

    inventory = build_scenario_inventory(load_scenario_registry())
    options = load_model_options()
    electricity, electricity_reconciliation, electricity_participants = (
        aggregate_electricity_profiles(
            inventory,
            options,
            load_gquery_catalogue(),
        )
    )
    hydrogen, hydrogen_reconciliation = aggregate_hydrogen_profiles(inventory, options)
    methane, methane_reconciliation, participants = aggregate_natural_gas_profiles(
        inventory,
        options,
        load_natural_gas_profile_participants(),
        load_gquery_catalogue(),
        args.raw_dir,
    )
    grouped_demand = apply_demand_grouping(
        electricity + hydrogen + methane, load_demand_aggregation()
    )
    write_rows(electricity, args.output_dir / "electricity_hourly.csv")
    write_rows(
        electricity_reconciliation,
        args.output_dir / "electricity_reconciliation.csv",
    )
    write_rows(
        electricity_participants,
        args.output_dir / "electricity_participants.csv",
    )
    write_rows(hydrogen, args.output_dir / "hydrogen_hourly.csv")
    write_rows(hydrogen_reconciliation, args.output_dir / "hydrogen_reconciliation.csv")
    write_rows(methane, args.output_dir / "methane_hourly.csv")
    write_rows(methane_reconciliation, args.output_dir / "methane_reconciliation.csv")
    write_rows(participants, args.output_dir / "methane_participants.csv")
    write_rows(grouped_demand, args.output_dir / "demand_hourly.csv")
    print(
        f"Built {len(electricity)} electricity, {len(hydrogen)} hydrogen, "
        f"and {len(methane)} methane canonical rows and "
        f"{len(grouped_demand)} grouped demand rows: {args.output_dir}"
    )


if __name__ == "__main__":
    main()