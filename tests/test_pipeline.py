import pytest

from sr2025_to_tulipa.config import ScenarioSeed
from sr2025_to_tulipa.pipeline import build_parser, select_scenario
from sr2025_to_tulipa.source_validation import SourceValidationError


def _scenario(enabled: bool = True) -> ScenarioSeed:
    return ScenarioSeed(
        "example",
        "Example",
        2040,
        1,
        "2025.01",
        "https://example.test/api/v3",
        enabled,
    )


def test_select_scenario_requires_one_enabled_match() -> None:
    assert select_scenario([_scenario()], "example", 2040).saved_scenario_id == 1

    with pytest.raises(SourceValidationError, match="No enabled scenario"):
        select_scenario([_scenario(False)], "example", 2040)


def test_build_cli_supports_collect_only() -> None:
    args = build_parser().parse_args(["example", "2040", "--collect-only"])

    assert args.scenario_key == "example"
    assert args.year == 2040
    assert args.collect_only is True
