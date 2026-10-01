from dataclasses import replace

import pytest

from sr2025_to_tulipa.config import load_scenario_registry
from sr2025_to_tulipa.etm_client import SavedScenario
from sr2025_to_tulipa.source_validation import (
    SourceValidationError,
    validate_saved_scenario,
    write_scenario_inventory,
)


def _saved_scenario() -> SavedScenario:
    """Create matching metadata for the first 2030 scenario."""
    return SavedScenario(
        saved_scenario_id=19916,
        scenario_id=123456,
        scenario_id_history=(),
        title="NBNL scenarios 2025 Koersvaste Middenweg",
        area_code="nl",
        end_year=2030,
        model_version="2025-01",
        saved_updated_at="2025-02-01T10:00:00Z",
        scenario_updated_at="2025-01-31T10:00:00Z",
    )


def test_matching_saved_scenario_is_accepted() -> None:
    """Reviewed metadata passes when the featured scenario has not drifted."""
    seed = next(item for item in load_scenario_registry() if item.saved_scenario_id == 19916)

    validate_saved_scenario(seed, _saved_scenario())


def test_changed_year_stops_inventory() -> None:
    """A changed featured-scenario year is a fatal source error."""
    seed = next(item for item in load_scenario_registry() if item.saved_scenario_id == 19916)
    changed = replace(_saved_scenario(), end_year=2035)

    with pytest.raises(SourceValidationError, match="year"):
        validate_saved_scenario(seed, changed)


def test_empty_inventory_is_not_written(tmp_path) -> None:
    """An empty source result cannot look like a successful audit."""
    with pytest.raises(SourceValidationError, match="empty"):
        write_scenario_inventory([], tmp_path / "inventory.csv")