from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from sr2025_to_tulipa.config import PROJECT_ROOT, load_scenario_registry
from sr2025_to_tulipa.source_validation import SourceValidationError
from sr2025_to_tulipa.tulipa_assets import TABLE_COLUMNS, country_bus


CORE_TABLES = tuple(f"{name}.csv" for name in TABLE_COLUMNS)
ASSET_TABLES = {
    "asset.csv",
    "asset-both.csv",
    "asset-commission.csv",
    "asset-milestone.csv",
}
FLOW_TABLES = {
    "flow.csv",
    "flow-both.csv",
    "flow-commission.csv",
    "flow-milestone.csv",
}
OPTIONAL_TULIPA_TABLES = {
    "assets-profiles.csv",
    "assets-rep-periods-partitions.csv",
    "assets-timeframe-partitions.csv",
    "assets-timeframe-profiles.csv",
    "flows-profiles.csv",
    "flows-relationships.csv",
    "flows-rep-periods-partitions.csv",
    "investment-group-asset.csv",
    "investment-group-asset-membership.csv",
    "model-parameters.csv",
    "profiles-rep-periods.csv",
    "profiles-timeframe.csv",
    "rep-periods-data.csv",
    "rep-periods-mapping.csv",
    "stochastic-scenario.csv",
    "timeframe-data.csv",
}
KEY_COLUMNS = {
    "asset.csv": ("asset",),
    "asset-both.csv": ("asset", "commission_year", "milestone_year"),
    "asset-commission.csv": ("asset", "commission_year"),
    "asset-milestone.csv": ("asset", "milestone_year"),
    "flow.csv": ("from_asset", "to_asset"),
    "flow-both.csv": ("from_asset", "to_asset", "commission_year", "milestone_year"),
    "flow-commission.csv": ("from_asset", "to_asset", "commission_year"),
    "flow-milestone.csv": ("from_asset", "to_asset", "milestone_year"),
}
IDENTIFIER_COLUMNS = {
    "asset",
    "from_asset",
    "to_asset",
    "profile_name",
    "flow_1_from_asset",
    "flow_1_to_asset",
    "flow_2_from_asset",
    "flow_2_to_asset",
}
YEAR_COLUMNS = {"commission_year", "milestone_year", "discount_year"}


@dataclass(frozen=True)
class IntegrationYear:
    model_year: int
    source_year: int
    output_directory: str


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if reader.fieldnames is None:
            raise SourceValidationError(f"Missing CSV header: {path}")
        return list(reader.fieldnames), list(reader)


def write_csv_rows(
    path: Path, columns: list[str] | tuple[str, ...], rows: list[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_integration_years(path: Path) -> dict[int, IntegrationYear]:
    _, rows = read_csv_rows(path)
    return {
        int(row["model_year"]): IntegrationYear(
            int(row["model_year"]),
            int(row["source_year"]),
            row["output_directory"],
        )
        for row in rows
    }


def load_boundary_nodes(path: Path, year: int) -> dict[str, str]:
    _, rows = read_csv_rows(path)
    return {
        f"{row['sr_endpoint']}_E_Demand_{year}":
        f"{row['tyndp_node']}_E_Demand_{year}"
        for row in rows
    }


def validate_tyndp_dispatch_source(source_dir: Path) -> None:
    """Reject investment-enabled TYNDP inputs at the dispatch merge boundary."""
    path = source_dir / "asset-milestone.csv"
    if not path.exists():
        raise SourceValidationError(f"Missing TYNDP dispatch table: {path}")
    _, rows = read_csv_rows(path)
    investable = [
        row["asset"]
        for row in rows
        if row.get("investable", "").strip().lower() == "true"
    ]
    if investable:
        raise SourceValidationError(
            "TYNDP integration requires a fixed-capacity dispatch dataset; "
            f"investable assets found: {investable[:5]}"
        )


def _relabel_row(
    row: dict[str, str], source_year: int, model_year: int
) -> dict[str, str]:
    result = dict(row)
    for column, value in result.items():
        if column in YEAR_COLUMNS and value.strip() == str(source_year):
            result[column] = str(model_year)
        elif column in IDENTIFIER_COLUMNS:
            result[column] = value.replace(
                f"_{source_year}", f"_{model_year}"
            )
    return result


def _is_nl_asset(asset: str) -> bool:
    return asset.startswith("NL")


def _foreign_carrier_bus(asset: str, carrier: str, source_year: int) -> bool:
    suffix = {
        "hydrogen": f"_H_Demand_{source_year}",
        "methane": f"_M_Demand_{source_year}",
    }[carrier]
    return not _is_nl_asset(asset) and asset.endswith(suffix)


def _tyndp_flow_decisions(
    rows: list[dict[str, str]], source_year: int, model_year: int
) -> tuple[
    list[dict[str, str]],
    dict[tuple[str, str], tuple[str, str] | None],
    dict[str, int],
]:
    output: list[dict[str, str]] = []
    decisions: dict[tuple[str, str], tuple[str, str] | None] = {}
    audit = {
        "tyndp_non_nl_flows_kept": 0,
        "tyndp_nl_local_flows_removed": 0,
        "tyndp_nl_electricity_flows_removed": 0,
        "tyndp_nl_hydrogen_crossborder_kept": 0,
        "tyndp_nl_methane_crossborder_kept": 0,
    }
    for row in rows:
        pair = (row["from_asset"], row["to_asset"])
        nl_from = _is_nl_asset(pair[0])
        nl_to = _is_nl_asset(pair[1])
        if not nl_from and not nl_to:
            transformed = _relabel_row(row, source_year, model_year)
            new_pair = (transformed["from_asset"], transformed["to_asset"])
            decisions[pair] = new_pair
            output.append(transformed)
            audit["tyndp_non_nl_flows_kept"] += 1
            continue

        carrier = row.get("carrier", "").lower()
        is_transport = row.get("is_transport", "").lower() == "true"
        foreign = pair[1] if nl_from else pair[0]
        keep_boundary = (
            nl_from != nl_to
            and is_transport
            and carrier in {"hydrogen", "methane"}
            and _foreign_carrier_bus(foreign, carrier, source_year)
        )
        if not keep_boundary:
            decisions[pair] = None
            key = (
                "tyndp_nl_electricity_flows_removed"
                if is_transport and carrier == "electricity"
                else "tyndp_nl_local_flows_removed"
            )
            audit[key] += 1
            continue

        transformed = _relabel_row(row, source_year, model_year)
        nl_bus = country_bus(carrier, model_year)
        if nl_from:
            transformed["from_asset"] = nl_bus
        else:
            transformed["to_asset"] = nl_bus
        new_pair = (transformed["from_asset"], transformed["to_asset"])
        decisions[pair] = new_pair
        output.append(transformed)
        audit[f"tyndp_nl_{carrier}_crossborder_kept"] += 1
    return output, decisions, audit


def _transform_tyndp_flow_detail(
    rows: list[dict[str, str]],
    decisions: dict[tuple[str, str], tuple[str, str] | None],
    source_year: int,
    model_year: int,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        decision = decisions.get((row["from_asset"], row["to_asset"]))
        if decision is None:
            continue
        transformed = _relabel_row(row, source_year, model_year)
        transformed["from_asset"], transformed["to_asset"] = decision
        output.append(transformed)
    return output


def _transform_sr_rows(
    filename: str,
    rows: list[dict[str, str]],
    aliases: dict[str, str],
) -> tuple[list[dict[str, str]], int]:
    output: list[dict[str, str]] = []
    removed_alias_assets = 0
    for row in rows:
        transformed = dict(row)
        if filename in ASSET_TABLES:
            if transformed["asset"] in aliases:
                removed_alias_assets += 1
                continue
        elif filename in FLOW_TABLES:
            transformed["from_asset"] = aliases.get(
                transformed["from_asset"], transformed["from_asset"]
            )
            transformed["to_asset"] = aliases.get(
                transformed["to_asset"], transformed["to_asset"]
            )
        output.append(transformed)
    return output, removed_alias_assets


def _merge_unique(
    filename: str,
    tyndp_rows: list[dict[str, str]],
    sr_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    key_columns = KEY_COLUMNS[filename]
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for row in tyndp_rows + sr_rows:
        key = tuple(row[column] for column in key_columns)
        if key in merged:
            raise SourceValidationError(f"Duplicate merged {filename} key: {key}")
        merged[key] = row
    return list(merged.values())


def _source_fingerprint(source_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source_dir.glob("*.csv")):
        if path.name not in set(CORE_TABLES) | OPTIONAL_TULIPA_TABLES:
            continue
        digest.update(f"{path.name}\n".encode())
        with path.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _copy_optional_tyndp_tables(
    source_dir: Path,
    output_dir: Path,
    source_year: int,
    model_year: int,
) -> list[str]:
    copied: list[str] = []
    for filename in sorted(OPTIONAL_TULIPA_TABLES):
        source = source_dir / filename
        if not source.exists():
            continue
        target = output_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        sr_columns: list[str] | None = None
        sr_rows: list[dict[str, str]] = []
        if target.exists():
            sr_columns, sr_rows = read_csv_rows(target)
        with source.open(encoding="utf-8-sig", newline="") as input_file, target.open(
            "w", encoding="utf-8", newline=""
        ) as output_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames is None:
                raise SourceValidationError(f"Missing CSV header: {source}")
            if sr_columns is not None and sr_columns != reader.fieldnames:
                raise SourceValidationError(
                    f"Tulipa schema mismatch for optional table {filename}."
                )
            writer = csv.DictWriter(output_file, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                asset = row.get("asset", "")
                profile = row.get("profile_name", "")
                if _is_nl_asset(asset) or _is_nl_asset(profile):
                    continue
                writer.writerow(_relabel_row(row, source_year, model_year))
            writer.writerows(sr_rows)
        copied.append(filename)
    return copied


def _materialize_optional_tables(
    source_dir: Path,
    output_dir: Path,
    cache_dir: Path,
    source_year: int,
    model_year: int,
) -> list[str]:
    complete = cache_dir / ".complete"
    if not complete.exists():
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True)
        copied = _copy_optional_tyndp_tables(
            source_dir, cache_dir, source_year, model_year
        )
        complete.write_text("\n".join(copied) + "\n", encoding="utf-8")
    copied = [line for line in complete.read_text(encoding="utf-8").splitlines() if line]
    for filename in copied:
        source = cache_dir / filename
        target = output_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        sr_columns: list[str] | None = None
        sr_rows: list[dict[str, str]] = []
        if target.exists():
            sr_columns, sr_rows = read_csv_rows(target)
            target.unlink()
        shutil.copy2(source, target)
        if sr_rows:
            source_columns, _ = read_csv_rows(source)
            if sr_columns != source_columns:
                raise SourceValidationError(
                    f"Tulipa schema mismatch for optional table {filename}."
                )
            with target.open("a", encoding="utf-8", newline="") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=source_columns)
                writer.writerows(sr_rows)
    return copied


def validate_merged_tables(
    output_dir: Path, model_year: int, sr_asset_names: set[str]
) -> None:
    _, assets = read_csv_rows(output_dir / "asset.csv")
    asset_names = {row["asset"] for row in assets}
    forbidden = sorted(asset for asset in asset_names if _is_nl_asset(asset))
    allowed = sr_asset_names | {
        country_bus("hydrogen", model_year),
        country_bus("methane", model_year),
    }
    unexpected = [asset for asset in forbidden if asset not in allowed]
    if unexpected:
        raise SourceValidationError(
            f"TYNDP-style NL assets survived integration: {unexpected[:5]}"
        )
    _, flows = read_csv_rows(output_dir / "flow.csv")
    pairs = [(row["from_asset"], row["to_asset"]) for row in flows]
    if len(pairs) != len(set(pairs)):
        raise SourceValidationError("Duplicate integrated flow keys.")
    unknown = {
        endpoint
        for row in flows
        for endpoint in (row["from_asset"], row["to_asset"])
        if endpoint not in asset_names
    }
    if unknown:
        raise SourceValidationError(
            f"Integrated flows reference unknown assets: {sorted(unknown)[:5]}"
        )
    flow_pairs = set(pairs)
    for filename in FLOW_TABLES - {"flow.csv"}:
        _, rows = read_csv_rows(output_dir / filename)
        detail_pairs = {(row["from_asset"], row["to_asset"]) for row in rows}
        if not detail_pairs <= flow_pairs:
            raise SourceValidationError(
                f"{filename} contains flows absent from flow.csv."
            )


def merge_case(
    sr_dir: Path,
    tyndp_dir: Path,
    output_dir: Path,
    model_year: int,
    source_year: int,
    boundary_config: Path,
    optional_cache_dir: Path | None = None,
) -> dict[str, object]:
    validate_tyndp_dispatch_source(tyndp_dir)
    aliases = load_boundary_nodes(boundary_config, model_year)
    _, tyndp_flow_rows = read_csv_rows(tyndp_dir / "flow.csv")
    tyndp_flows, decisions, audit = _tyndp_flow_decisions(
        tyndp_flow_rows, source_year, model_year
    )
    removed_tyndp_assets = 0
    removed_sr_alias_rows = 0
    sr_asset_names: set[str] = set()
    for filename in CORE_TABLES:
        sr_columns, sr_rows = read_csv_rows(sr_dir / filename)
        tyndp_columns, tyndp_rows = read_csv_rows(tyndp_dir / filename)
        if sr_columns != tyndp_columns:
            raise SourceValidationError(
                f"Tulipa schema mismatch for {filename}: SR2025 and TYNDP columns differ."
            )
        if filename in ASSET_TABLES:
            retained = []
            for row in tyndp_rows:
                if _is_nl_asset(row["asset"]):
                    removed_tyndp_assets += 1
                    continue
                retained.append(_relabel_row(row, source_year, model_year))
            tyndp_rows = retained
        elif filename == "flow.csv":
            tyndp_rows = tyndp_flows
        else:
            tyndp_rows = _transform_tyndp_flow_detail(
                tyndp_rows, decisions, source_year, model_year
            )
        sr_rows, removed = _transform_sr_rows(filename, sr_rows, aliases)
        if filename == "asset.csv":
            sr_asset_names = {row["asset"] for row in sr_rows}
        removed_sr_alias_rows += removed
        merged = _merge_unique(filename, tyndp_rows, sr_rows)
        write_csv_rows(output_dir / filename, sr_columns, merged)

    for filename in OPTIONAL_TULIPA_TABLES:
        target = output_dir / filename
        if target.exists():
            target.unlink()
        sr_source = sr_dir / filename
        if sr_source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sr_source, target)

    optional_tables = (
        _materialize_optional_tables(
            tyndp_dir,
            output_dir,
            optional_cache_dir,
            source_year,
            model_year,
        )
        if optional_cache_dir is not None
        else _copy_optional_tyndp_tables(
            tyndp_dir, output_dir, source_year, model_year
        )
    )
    validate_merged_tables(output_dir, model_year, sr_asset_names)
    manifest = {
        "model_year": model_year,
        "tyndp_source_year": source_year,
        "tyndp_source_directory": str(tyndp_dir.resolve()),
        "tyndp_source_fingerprint": _source_fingerprint(tyndp_dir),
        "sr2025_source_directory": str(sr_dir.resolve()),
        "tulipa_schema_version": "0.22",
        "removed_tyndp_nl_asset_rows": removed_tyndp_assets,
        "removed_sr_boundary_alias_rows": removed_sr_alias_rows,
        "optional_tyndp_tables": optional_tables,
        **audit,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "integration-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def merge_registered_cases(
    sr_root: Path,
    tyndp_root: Path,
    output_root: Path,
    year_config: Path,
    boundary_config: Path,
) -> list[Path]:
    years = load_integration_years(year_config)
    outputs: list[Path] = []
    for scenario in load_scenario_registry():
        if not scenario.enabled:
            continue
        mapping = years.get(scenario.year)
        if mapping is None:
            raise SourceValidationError(
                f"No TYNDP integration mapping for {scenario.year}."
            )
        source_dir = tyndp_root / mapping.output_directory
        fingerprint = _source_fingerprint(source_dir)
        cache_dir = (
            output_root
            / "_tyndp_cache"
            / f"{mapping.output_directory}_as_{scenario.year}_{fingerprint[:12]}"
        )
        output = output_root / f"{scenario.scenario_key}_{scenario.year}"
        merge_case(
            sr_root / f"{scenario.scenario_key}_{scenario.year}",
            source_dir,
            output,
            scenario.year,
            mapping.source_year,
            boundary_config,
            cache_dir,
        )
        outputs.append(output)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge SR2025 NL and TYNDP European Tulipa v0.22 outputs."
    )
    parser.add_argument("scenario_key", nargs="?")
    parser.add_argument("year", type=int, nargs="?")
    parser.add_argument(
        "--all", action="store_true", help="Merge all enabled SR2025 cases."
    )
    parser.add_argument(
        "--sr-root", type=Path, default=Path("output/tulipa")
    )
    parser.add_argument(
        "--tyndp-root", type=Path, default=Path("../TYNDP-26-to-Tulipa")
    )
    parser.add_argument(
        "--tyndp-case-dir",
        type=Path,
        help="Direct TYNDP dispatch input folder for a single scenario-year.",
    )
    parser.add_argument(
        "--tyndp-source-year",
        type=int,
        help="Year encoded in --tyndp-case-dir; defaults to the model year.",
    )
    parser.add_argument(
        "--year-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "tyndp_integration_years.csv",
    )
    parser.add_argument(
        "--boundary-config",
        type=Path,
        default=PROJECT_ROOT / "config" / "tyndp_electricity_boundary_nodes.csv",
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("output/integrated")
    )
    args = parser.parse_args()
    if args.all:
        if args.tyndp_case_dir is not None or args.tyndp_source_year is not None:
            parser.error("--tyndp-case-dir/--tyndp-source-year cannot be used with --all")
        outputs = merge_registered_cases(
            args.sr_root,
            args.tyndp_root,
            args.output_root,
            args.year_config,
            args.boundary_config,
        )
        print(f"Created {len(outputs)} integrated Tulipa input folders.")
        return
    if args.scenario_key is None or args.year is None:
        parser.error("scenario_key and year are required unless --all is used")
    if args.tyndp_case_dir is None:
        years = load_integration_years(args.year_config)
        if args.year not in years:
            raise SystemExit(f"No TYNDP integration mapping for {args.year}.")
        mapping = years[args.year]
        source_dir = args.tyndp_root / mapping.output_directory
        source_year = mapping.source_year
    else:
        source_dir = args.tyndp_case_dir
        source_year = args.tyndp_source_year or args.year
    output = args.output_root / f"{args.scenario_key}_{args.year}"
    fingerprint = _source_fingerprint(source_dir)
    cache_dir = (
        args.output_root
        / "_tyndp_cache"
        / f"{source_dir.name}_as_{args.year}_{fingerprint[:12]}"
    )
    manifest = merge_case(
        args.sr_root / f"{args.scenario_key}_{args.year}",
        source_dir,
        output,
        args.year,
        source_year,
        args.boundary_config,
        cache_dir,
    )
    print(
        f"Created integrated Tulipa inputs in {output} "
        f"(TYNDP {manifest['tyndp_source_year']})."
    )


if __name__ == "__main__":
    main()