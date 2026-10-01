from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sr2025_to_tulipa.config import (
    DEFAULT_MODEL_OPTIONS,
    ModelOptions,
    load_model_options,
    load_scenario_registry,
    update_model_options,
)

PIPELINE_STEPS = {
    "inventory": "sr2025_to_tulipa.source_validation",
    "demand": "sr2025_to_tulipa.demand_audit",
    "profiles": "sr2025_to_tulipa.carrier_profiles",
    "capacities": "sr2025_to_tulipa.capacity_aggregation",
    "methane-supply": "sr2025_to_tulipa.methane_supply_aggregation",
}
DEFAULT_STEPS = list(PIPELINE_STEPS)
PIPELINE_SCOPE = (
    "Preparatory stages only; regionalisation, Tulipa export, and TYNDP "
    "integration run through their dedicated CLIs."
)
CHOICES = {
    "flexible_heat_mode": ["final_energy_demand", "heat_demand"],
    "industrial_heat_mode": ["final_energy_demand", "heat_demand"],
    "agriculture_heat_mode": ["final_energy_demand", "heat_demand"],
    "dsr_mode": ["final_electricity_demand", "dsr"],
    "add_network_losses_to_demand": ["true", "false"],
    "add_power_sector_own_use_to_demand": ["true", "false"],
}
LABELS = {
    "flexible_heat_mode": "Flexible and central heat",
    "industrial_heat_mode": "Industrial heat",
    "agriculture_heat_mode": "Agriculture heat",
    "dsr_mode": "Demand-side response",
    "add_network_losses_to_demand": "Network losses in demand",
    "add_power_sector_own_use_to_demand": "Power-sector own use in demand",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the preparatory pipeline run interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Configure, review, and optionally run the SR2025 preparatory stages. "
            + PIPELINE_SCOPE
        )
    )
    parser.add_argument("--interactive", action="store_true", help="Prompt for choices.")
    parser.add_argument("--execute", action="store_true", help="Execute selected stages.")
    parser.add_argument(
        "--steps", nargs="+", choices=PIPELINE_STEPS, default=DEFAULT_STEPS
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("output/run_manifest.json")
    )
    parser.add_argument(
        "--flexible-heat", choices=CHOICES["flexible_heat_mode"]
    )
    parser.add_argument(
        "--industrial-heat", choices=CHOICES["industrial_heat_mode"]
    )
    parser.add_argument(
        "--agriculture-heat", choices=CHOICES["agriculture_heat_mode"]
    )
    parser.add_argument("--dsr", choices=CHOICES["dsr_mode"])
    parser.add_argument("--network-losses", choices=["include", "exclude"])
    parser.add_argument("--power-sector-own-use", choices=["include", "exclude"])
    parser.add_argument("--options", type=Path, default=DEFAULT_MODEL_OPTIONS)
    return parser


def format_overview(options: ModelOptions, steps: list[str]) -> str:
    """Format one readable overview of the configured pipeline run."""
    scenarios = [scenario for scenario in load_scenario_registry() if scenario.enabled]
    scenario_years: dict[str, list[int]] = {}
    for scenario in scenarios:
        scenario_years.setdefault(scenario.scenario_name, []).append(scenario.year)

    lines = [
        "SR2025 PREPARATORY PIPELINE RUN",
        "=" * 60,
        PIPELINE_SCOPE,
        "",
        "Scenarios",
    ]
    lines.extend(
        f"  {name:<28} {', '.join(str(year) for year in sorted(years))}"
        for name, years in scenario_years.items()
    )
    lines.extend(["", "Pipeline stages"])
    lines.extend(f"  {index}. {step}" for index, step in enumerate(steps, start=1))
    lines.extend(["", "Demand boundary"])
    values = _option_values(options)
    lines.extend(f"  {LABELS[name]:<34} {values[name]}" for name in LABELS)
    lines.extend(
        [
            "",
            "Fixed choices",
            "  Residential heat                   final energy demand",
            "  Household and EV batteries         net exchange in demand",
            "  Household and EV battery capacity  excluded",
            "  System batteries                    endogenous assets",
            "  Energy-sector demand                excluded",
        ]
    )
    return "\n".join(lines)


def option_updates(args: argparse.Namespace, current: ModelOptions) -> dict[str, str]:
    """Merge command-line choices with the currently persisted options."""
    values = _option_values(current)
    argument_fields = {
        "flexible_heat": "flexible_heat_mode",
        "industrial_heat": "industrial_heat_mode",
        "agriculture_heat": "agriculture_heat_mode",
        "dsr": "dsr_mode",
    }
    for argument, field in argument_fields.items():
        value = getattr(args, argument)
        if value is not None:
            values[field] = value
    for argument, field in [
        ("network_losses", "add_network_losses_to_demand"),
        ("power_sector_own_use", "add_power_sector_own_use_to_demand"),
    ]:
        value = getattr(args, argument)
        if value is not None:
            values[field] = "true" if value == "include" else "false"
    return values


def interactive_choices(
    options: ModelOptions, steps: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Prompt for model options and pipeline stages in the terminal."""
    values = _option_values(options)
    print("Press Enter to retain the value shown in brackets.\n")
    for name, allowed in CHOICES.items():
        choices = "/".join(allowed)
        answer = input(f"{LABELS[name]} ({choices}) [{values[name]}]: ").strip()
        if answer:
            if answer not in allowed:
                raise ValueError(f"Invalid value for {LABELS[name]}: {answer}")
            values[name] = answer
    answer = input(
        f"Pipeline stages, comma-separated [{','.join(steps)}]: "
    ).strip()
    if answer:
        selected = [value.strip() for value in answer.split(",")]
        unknown = set(selected) - set(PIPELINE_STEPS)
        if unknown:
            raise ValueError(f"Unknown pipeline stages: {', '.join(sorted(unknown))}")
        steps = selected
    return values, steps


def write_manifest(
    path: Path, options: ModelOptions, steps: list[str], executed: bool
) -> None:
    """Write a machine-readable record of one configured run."""
    scenarios = [
        asdict(scenario) for scenario in load_scenario_registry() if scenario.enabled
    ]
    document = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "executed": executed,
        "scope": PIPELINE_SCOPE,
        "steps": steps,
        "model_options": asdict(options),
        "fixed_choices": {
            "residential_heat": "final_energy_demand",
            "household_ev_battery_operation": "net_exchange_in_demand",
            "household_ev_battery_capacity": "excluded",
            "system_batteries": "endogenous_assets",
            "energy_sector_demand": "excluded",
        },
        "scenarios": scenarios,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def execute_steps(steps: list[str]) -> None:
    """Run selected pipeline modules in their configured order."""
    for step in steps:
        print(f"\nRunning {step}...")
        subprocess.run([sys.executable, "-m", PIPELINE_STEPS[step]], check=True)


def _option_values(options: ModelOptions) -> dict[str, str]:
    """Convert typed model options to their persisted string values."""
    return {
        "flexible_heat_mode": options.flexible_heat_mode,
        "industrial_heat_mode": options.industrial_heat_mode,
        "agriculture_heat_mode": options.agriculture_heat_mode,
        "dsr_mode": options.dsr_mode,
        "add_network_losses_to_demand": str(
            options.add_network_losses_to_demand
        ).lower(),
        "add_power_sector_own_use_to_demand": str(
            options.add_power_sector_own_use_to_demand
        ).lower(),
    }


def main() -> None:
    """Configure, review, and optionally execute one pipeline run."""
    args = build_parser().parse_args()
    current = load_model_options(args.options)
    steps = list(args.steps)
    if args.interactive:
        updates, steps = interactive_choices(current, steps)
    else:
        updates = option_updates(args, current)
    options = update_model_options(updates, args.options)

    print(format_overview(options, steps))
    write_manifest(args.manifest, options, steps, executed=args.execute)
    print(f"\nRun manifest: {args.manifest}")
    if args.execute:
        execute_steps(steps)


if __name__ == "__main__":
    main()