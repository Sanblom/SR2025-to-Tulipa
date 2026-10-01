import json

from sr2025_to_tulipa.config import load_model_options
from sr2025_to_tulipa.dashboard import (
    build_parser,
    format_overview,
    option_updates,
    write_manifest,
)


def test_dashboard_prints_run_scope_and_boundary_choices() -> None:
    """The terminal dashboard gives a complete overview of one run."""
    overview = format_overview(load_model_options(), ["inventory", "profiles"])

    assert "SR2025 PREPARATORY PIPELINE RUN" in overview
    assert "Tulipa export" in overview
    assert "Koersvaste Middenweg" in overview
    assert "inventory" in overview
    assert "Flexible and central heat" in overview
    assert "Household and EV batteries" in overview
    assert "System batteries" in overview


def test_command_line_choices_override_current_options() -> None:
    """Explicit command choices translate to persisted model options."""
    args = build_parser().parse_args(
        ["--flexible-heat", "heat_demand", "--network-losses", "exclude"]
    )
    updates = option_updates(args, load_model_options())

    assert updates["flexible_heat_mode"] == "heat_demand"
    assert updates["add_network_losses_to_demand"] == "false"
    assert updates["add_power_sector_own_use_to_demand"] == "true"


def test_run_manifest_records_choices_without_executing(tmp_path) -> None:
    """A preview manifest records options, stages, and fixed boundaries."""
    path = tmp_path / "run_manifest.json"
    write_manifest(path, load_model_options(), ["demand"], executed=False)

    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["executed"] is False
    assert "Preparatory stages only" in manifest["scope"]
    assert manifest["steps"] == ["demand"]
    assert manifest["fixed_choices"]["household_ev_battery_capacity"] == "excluded"
    assert len(manifest["scenarios"]) == 17