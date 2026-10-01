import csv

from sr2025_to_tulipa.config import (
    load_demand_aggregation,
    load_electricity_capacity_aggregation,
    load_gquery_catalogue,
    load_hydrogen_capacity_aggregation,
    load_methane_supply_aggregation,
    load_model_options,
    load_natural_gas_profile_participants,
    load_profile_queries,
    load_scenario_registry,
    update_model_options,
)


def test_registry_contains_the_17_featured_entries() -> None:
    """The seed registry contains the agreed four pathways and years."""
    scenarios = load_scenario_registry()

    assert len(scenarios) == 17
    assert {item.scenario_key for item in scenarios} == {
        "koersvaste_middenweg",
        "eigen_vermogen",
        "gezamenlijke_balans",
        "horizon_aanvoer",
    }
    assert {item.year for item in scenarios} == {2025, 2030, 2035, 2040, 2050}
    assert all(item.enabled for item in scenarios)


def test_only_koersvaste_middenweg_has_a_2025_entry() -> None:
    """The 2025 base entry belongs only to Koersvaste Middenweg."""
    scenarios = load_scenario_registry()

    entries_2025 = [item for item in scenarios if item.year == 2025]
    assert [item.scenario_key for item in entries_2025] == ["koersvaste_middenweg"]


def test_demand_catalogue_has_totals_and_sector_components() -> None:
    """Each audited carrier has one total and eight sector queries."""
    queries = load_gquery_catalogue()

    assert len(queries) == 27
    for carrier in {"electricity", "hydrogen", "methane"}:
        carrier_queries = [query for query in queries if query.carrier == carrier]
        assert sum(query.role == "control_total" for query in carrier_queries) == 1
        assert sum(query.role == "sector_component" for query in carrier_queries) == 8
        assert {query.expected_unit for query in carrier_queries} == {"PJ"}


def test_demand_policy_preserves_sectors_and_keeps_bunkers_explicit() -> None:
    """Every final-demand sector stays distinct through distribution."""
    rules = load_demand_aggregation()

    assert len(rules) == 36
    assert all(not rule.included for rule in rules if rule.sector == "energy")
    assert all(
        rule.demand_group == rule.sector
        for rule in rules
        if rule.included
        and rule.sector
        not in {
            "bunkers",
            "household_storage_exchange",
            "transport_storage_exchange",
        }
    )
    assert {
        (rule.sector, rule.demand_group)
        for rule in rules
        if rule.sector.endswith("storage_exchange")
    } == {
        ("household_storage_exchange", "households"),
        ("transport_storage_exchange", "transport"),
    }
    assert all(
        rule.included and rule.spatial_scope == "national"
        for rule in rules
        if rule.sector == "bunkers" and rule.carrier != "electricity"
    )
    electricity_bunkers = next(
        rule
        for rule in rules
        if rule.carrier == "electricity" and rule.sector == "bunkers"
    )
    assert electricity_bunkers.spatial_scope == "facility_mapping_required"
    transport_lng = next(
        rule
        for rule in rules
        if rule.carrier == "methane" and rule.sector == "transport_lng"
    )
    assert transport_lng.included is True
    assert transport_lng.demand_group == "transport_lng"
    assert transport_lng.spatial_scope == "national"


def test_requested_demand_boundary_options_are_enabled_by_default() -> None:
    """Default options retain heat inputs, losses, and own use in demand."""
    options = load_model_options()

    assert options.flexible_heat_mode == "final_energy_demand"
    assert options.industrial_heat_mode == "final_energy_demand"
    assert options.agriculture_heat_mode == "final_energy_demand"
    assert options.dsr_mode == "final_electricity_demand"
    assert options.add_network_losses_to_demand is True
    assert options.add_power_sector_own_use_to_demand is True


def test_model_options_can_be_validated_and_updated(tmp_path) -> None:
    """Dashboard updates preserve option metadata and validate all values."""
    path = tmp_path / "model_options.csv"
    rows = [
        ("flexible_heat_mode", "final_energy_demand", "final_energy_demand|heat_demand"),
        ("industrial_heat_mode", "final_energy_demand", "final_energy_demand|heat_demand"),
        ("agriculture_heat_mode", "final_energy_demand", "final_energy_demand|heat_demand"),
        ("dsr_mode", "final_electricity_demand", "final_electricity_demand|dsr"),
        ("add_network_losses_to_demand", "true", "true|false"),
        ("add_power_sector_own_use_to_demand", "true", "true|false"),
    ]
    with path.open("w", encoding="utf-8", newline="") as options_file:
        writer = csv.writer(options_file)
        writer.writerow(["option", "value", "allowed_values", "description"])
        writer.writerows((*row, f"Description for {row[0]}") for row in rows)

    updates = {option: value for option, value, _ in rows}
    updates["industrial_heat_mode"] = "heat_demand"
    options = update_model_options(updates, path)

    assert options.industrial_heat_mode == "heat_demand"
    with path.open(encoding="utf-8", newline="") as options_file:
        persisted = list(csv.DictReader(options_file))
    industry = next(row for row in persisted if row["option"] == "industrial_heat_mode")
    assert industry["description"] == "Description for industrial_heat_mode"


def test_profile_catalogue_preserves_hydrogen_industry_components() -> None:
    """Hydrogen industry curves remain separate until profile assembly."""
    queries = load_profile_queries()

    industry = [query for query in queries if query.sector == "industry"]
    assert len(queries) == 8
    assert len(industry) == 3
    assert {query.expected_unit for query in queries} == {"curve"}
    assert {query.expected_hours for query in queries} == {8760}
    assert {query.boundary_type for query in queries} == {"final_demand"}


def test_electricity_capacity_policy_encodes_approved_groups() -> None:
    """Electricity capacity mappings preserve the approved distinctions."""
    rules = load_electricity_capacity_aggregation()
    groups = {rule.asset_group for rule in rules if rule.included}

    assert "solar_pv" in groups
    assert "wind_onshore" in groups
    assert "wind_offshore" in groups
    assert "hydrogen_ccgt" in groups
    assert "hydrogen_ocgt" in groups
    assert "natural_gas_chp" in groups
    assert "biomass_chp_must_run" in groups
    assert "biomass_steam_dispatchable" in groups
    assert "waste_chp" in groups
    assert "waste_chp_ccs" in groups
    assert all(
        not rule.included
        for rule in rules
        if rule.asset_group in {"interconnector", "must_run_aggregate"}
    )
    diesel = next(rule for rule in rules if rule.asset_group == "diesel_engine")
    assert diesel.included
    assert diesel.minimum_capacity_mw == 50.0
    assert {"natural_gas_ccgt", "natural_gas_ocgt", "nuclear_smr"} <= groups


def test_hydrogen_capacity_policy_encodes_approved_groups() -> None:
    """Hydrogen mappings group electrolysis and preserve other distinctions."""
    rules = load_hydrogen_capacity_aggregation()
    groups = {rule.asset_group for rule in rules}
    electrolysis = [rule for rule in rules if rule.asset_group == "electrolysis"]

    assert len(rules) == 20
    assert len(electrolysis) == 4
    assert {rule.operating_mode for rule in electrolysis} == {"mixed"}
    assert {"atr_ccs", "ammonia_reformer", "lohc_reformer"} <= groups
    assert "atr" not in groups
    assert {"hydrogen_import_baseload", "hydrogen_import_backup"} <= groups
    assert {
        "hydrogen_storage_depleted_gas_field",
        "hydrogen_storage_salt_cavern",
    } <= groups


def test_methane_policy_groups_renewables_and_excludes_transformation() -> None:
    """Methane mappings implement the approved supply-route decisions."""
    rules = load_methane_supply_aggregation()
    renewable = [rule for rule in rules if rule.supply_group == "renewable_methane"]
    transformation = next(rule for rule in rules if rule.route == "industry_transformation")

    assert len(rules) == 9
    assert len(renewable) == 3
    assert transformation.included is False
    assert {
        rule.supply_group
        for rule in rules
        if rule.route in {"natural_gas_extraction", "natural_gas_import"}
    } == {"natural_gas_extraction", "natural_gas_import"}


def test_natural_gas_profile_policy_maps_every_known_input() -> None:
    """Every network-gas input participant has one explicit boundary rule."""
    rules = load_natural_gas_profile_participants()

    assert len(rules) == 71
    assert len({rule.participant for rule in rules}) == 71
    assert {rule.inclusion_mode for rule in rules} == {
        "always",
        "never",
        "flexible_heat",
        "industrial_heat",
        "agriculture_heat",
        "network_losses",
    }