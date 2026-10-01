from __future__ import annotations

from dataclasses import dataclass

from sr2025_to_tulipa.config import ProfileQuerySpec
from sr2025_to_tulipa.etm_client import EtmClient, GQueryResponse
from sr2025_to_tulipa.source_validation import (
    ScenarioInventoryRow,
    SourceValidationError,
)

HOURS_PER_YEAR = 8760
MWH_TO_PJ = 3.6e-6
ABSOLUTE_TOLERANCE_PJ = 1e-4
RELATIVE_TOLERANCE = 1e-4


@dataclass(frozen=True)
class ProfileValueRow:
    """Record one raw hourly ETM profile value with provenance."""

    scenario_key: str
    year: int
    saved_scenario_id: int
    scenario_id: int
    carrier: str
    sector: str
    component: str
    query_key: str
    hour: int
    future_mw: float
    source_unit: str
    review_status: str
    engine_base_url: str
    retrieved_at: str
    response_checksum: str


@dataclass(frozen=True)
class SectorProfileRow:
    """Record one hourly sector curve assembled from source components."""

    scenario_key: str
    year: int
    carrier: str
    sector: str
    hour: int
    future_mw: float


@dataclass(frozen=True)
class ProfileReconciliationRow:
    """Compare a sector profile integral with annual final demand."""

    scenario_key: str
    year: int
    carrier: str
    sector: str
    annual_query_key: str
    annual_demand_pj: float
    profile_integral_pj: float
    difference_pj: float
    relative_difference: float
    scale_factor_to_annual: float | None
    status: str


def collect_profile_data(
    inventory: list[ScenarioInventoryRow], queries: list[ProfileQuerySpec]
) -> tuple[list[ProfileValueRow], list[SectorProfileRow], list[ProfileReconciliationRow]]:
    """Collect, assemble, and validate configured ETM profile curves."""
    values: list[ProfileValueRow] = []
    sectors: list[SectorProfileRow] = []
    reconciliations: list[ProfileReconciliationRow] = []
    annual_keys = sorted({query.annual_query_key for query in queries})
    query_keys = [query.query_key for query in queries] + annual_keys

    for scenario in inventory:
        with EtmClient(scenario.engine_base_url) as client:
            response = client.query_scenario(scenario.scenario_id, query_keys)
        scenario_values = _profile_value_rows(scenario, queries, response)
        scenario_sectors = assemble_sector_profiles(scenario_values)
        values.extend(scenario_values)
        sectors.extend(scenario_sectors)
        reconciliations.extend(
            reconcile_profiles(scenario, queries, scenario_sectors, response)
        )

    return values, sectors, reconciliations


def assemble_sector_profiles(rows: list[ProfileValueRow]) -> list[SectorProfileRow]:
    """Add profile components while retaining carrier and sector detail."""
    totals: dict[tuple[str, int, str, str, int], float] = {}
    for row in rows:
        key = (row.scenario_key, row.year, row.carrier, row.sector, row.hour)
        totals[key] = totals.get(key, 0.0) + row.future_mw

    return [
        SectorProfileRow(*key, future_mw=value)
        for key, value in sorted(totals.items())
    ]


def reconcile_profiles(
    scenario: ScenarioInventoryRow,
    queries: list[ProfileQuerySpec],
    sector_rows: list[SectorProfileRow],
    response: GQueryResponse,
) -> list[ProfileReconciliationRow]:
    """Check each assembled curve against its annual final demand."""
    profile_totals: dict[tuple[str, str], float] = {}
    for row in sector_rows:
        key = (row.carrier, row.sector)
        profile_totals[key] = profile_totals.get(key, 0.0) + row.future_mw

    reconciliations: list[ProfileReconciliationRow] = []
    groups = sorted({(query.carrier, query.sector) for query in queries})
    for carrier, sector in groups:
        annual_keys = {
            query.annual_query_key
            for query in queries
            if query.carrier == carrier and query.sector == sector
        }
        if len(annual_keys) != 1:
            raise SourceValidationError(
                f"Expected one annual query for {carrier} {sector}."
            )
        annual_key = annual_keys.pop()
        annual_value = response.values.get(annual_key)
        if not isinstance(annual_value, dict) or annual_value.get("unit") != "PJ":
            raise SourceValidationError(f"{annual_key} did not return a PJ value.")
        annual_pj = float(annual_value["future"])
        profile_pj = profile_totals[(carrier, sector)] * MWH_TO_PJ
        difference = profile_pj - annual_pj
        relative = difference / annual_pj if annual_pj else difference
        scale_factor = annual_pj / profile_pj if profile_pj else None
        passed = abs(difference) <= ABSOLUTE_TOLERANCE_PJ or (
            annual_pj != 0.0 and abs(relative) <= RELATIVE_TOLERANCE
        )
        reconciliations.append(
            ProfileReconciliationRow(
                scenario_key=scenario.scenario_key,
                year=scenario.year,
                carrier=carrier,
                sector=sector,
                annual_query_key=annual_key,
                annual_demand_pj=annual_pj,
                profile_integral_pj=profile_pj,
                difference_pj=difference,
                relative_difference=relative,
                scale_factor_to_annual=scale_factor,
                status="pass" if passed else "review",
            )
        )
    return reconciliations


def _profile_value_rows(
    scenario: ScenarioInventoryRow,
    queries: list[ProfileQuerySpec],
    response: GQueryResponse,
) -> list[ProfileValueRow]:
    """Validate ETM curves and expand them to hourly rows."""
    rows: list[ProfileValueRow] = []
    for query in queries:
        value = response.values.get(query.query_key)
        if not isinstance(value, dict):
            raise SourceValidationError(f"ETM did not return {query.query_key}.")
        curve = value.get("future")
        source_unit = value.get("unit")
        inactive_zero = (
            source_unit is None
            and isinstance(curve, list)
            and len(curve) == HOURS_PER_YEAR
            and all(future_mw == 0 for future_mw in curve)
        )
        if source_unit != query.expected_unit and not inactive_zero:
            raise SourceValidationError(
                f"{query.query_key} returned {source_unit}, "
                f"expected {query.expected_unit}."
            )
        if not isinstance(curve, list) or len(curve) != query.expected_hours:
            length = len(curve) if isinstance(curve, list) else "non-list"
            raise SourceValidationError(
                f"{query.query_key} returned {length} hours, "
                f"expected {query.expected_hours}."
            )
        for hour, future_mw in enumerate(curve, start=1):
            rows.append(
                ProfileValueRow(
                    scenario_key=scenario.scenario_key,
                    year=scenario.year,
                    saved_scenario_id=scenario.saved_scenario_id,
                    scenario_id=scenario.scenario_id,
                    carrier=query.carrier,
                    sector=query.sector,
                    component=query.component,
                    query_key=query.query_key,
                    hour=hour,
                    future_mw=float(future_mw),
                    source_unit=(
                        "curve_inactive_zero" if inactive_zero else str(source_unit)
                    ),
                    review_status=query.review_status,
                    engine_base_url=scenario.engine_base_url,
                    retrieved_at=response.retrieved_at,
                    response_checksum=response.response_checksum,
                )
            )
    return rows
