from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from sr2025_to_tulipa.config import ScenarioSeed, load_scenario_registry
from sr2025_to_tulipa.etm_client import (
    EtmClient,
    SavedScenario,
    resolve_featured_scenario,
)

class SourceValidationError(ValueError):
    """Report source metadata that differs from the registry."""

@dataclass(frozen=True)
class ScenarioInventoryRow:
    """Record the verified identity of one ETM scenario."""

    scenario_key: str
    scenario_name: str
    year: int
    saved_scenario_id: int
    scenario_id: int
    area_code: str
    model_version: str
    engine_base_url: str
    saved_updated_at: str
    scenario_updated_at: str

def validate_saved_scenario(seed: ScenarioSeed, actual: SavedScenario) -> None:
    """Check that ETM metadata still matches the reviewed registry seed."""
    expected_title = f"NBNL scenarios 2025 {seed.scenario_name}"
    mismatches: list[str] = []

    if actual.saved_scenario_id != seed.saved_scenario_id:
        mismatches.append("saved scenario ID")
    if actual.title.casefold() != expected_title.casefold():
        mismatches.append("title")
    if actual.end_year != seed.year:
        mismatches.append("year")
    if _normalise_version(actual.model_version) != _normalise_version(seed.model_version):
        mismatches.append("model version")
    if not actual.area_code.casefold().startswith("nl"):
        mismatches.append("area")

    if mismatches:
        fields = ", ".join(mismatches)
        raise SourceValidationError(
            f"Saved scenario {seed.saved_scenario_id} differs in: {fields}."
        )

def build_scenario_inventory(seeds: list[ScenarioSeed]) -> list[ScenarioInventoryRow]:
    """Resolve and validate every enabled scenario registry entry."""
    inventory: list[ScenarioInventoryRow] = []
    for seed in seeds:
        if not seed.enabled:
            continue
        link = resolve_featured_scenario(seed.saved_scenario_id)
        if link.engine_base_url != seed.engine_base_url:
            raise SourceValidationError(
                f"Saved scenario {seed.saved_scenario_id} points to "
                f"{link.engine_base_url}, expected {seed.engine_base_url}."
            )
        with EtmClient(link.engine_base_url) as client:
            scenario = client.get_scenario(link.scenario_id)
        actual = _saved_scenario_from_public_data(seed, link.scenario_id, scenario)
        validate_saved_scenario(seed, actual)
        inventory.append(_inventory_row(seed, actual))

    return inventory

def _saved_scenario_from_public_data(
    seed: ScenarioSeed, scenario_id: int, scenario: dict[str, object]
) -> SavedScenario:
    """Combine public page identity with stable engine metadata."""
    return SavedScenario(
        saved_scenario_id=seed.saved_scenario_id,
        scenario_id=scenario_id,
        scenario_id_history=(),
        title=f"NBNL scenarios 2025 {seed.scenario_name}",
        area_code=str(scenario["area_code"]),
        end_year=int(scenario["end_year"]),
        model_version=seed.model_version,
        saved_updated_at="not_exposed_by_public_page",
        scenario_updated_at=str(scenario["updated_at"]),
    )

def write_scenario_inventory(
    rows: list[ScenarioInventoryRow], output_path: Path
) -> None:
    """Write verified scenario identities to a CSV file."""
    if not rows:
        raise SourceValidationError("Cannot write an empty scenario inventory.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)

def _inventory_row(
    seed: ScenarioSeed, actual: SavedScenario
) -> ScenarioInventoryRow:
    """Combine reviewed registry values with resolved ETM identifiers."""
    return ScenarioInventoryRow(
        scenario_key=seed.scenario_key,
        scenario_name=seed.scenario_name,
        year=seed.year,
        saved_scenario_id=seed.saved_scenario_id,
        scenario_id=actual.scenario_id,
        area_code=actual.area_code,
        model_version=actual.model_version,
        engine_base_url=seed.engine_base_url,
        saved_updated_at=actual.saved_updated_at,
        scenario_updated_at=actual.scenario_updated_at,
    )

def _normalise_version(version: str) -> str:
    """Make dotted and dashed ETM version tags comparable."""
    return version.strip().removeprefix("#").replace("-", ".")
