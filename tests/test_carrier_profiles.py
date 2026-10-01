import pytest

from sr2025_to_tulipa.carrier_profiles import (
    CanonicalProfileRow,
    CurveCsvResponse,
    HOURS_PER_YEAR,
    _classify_electricity_participant,
    _electricity_target_sector,
    _electricity_participant_values,
    _participant_included,
    apply_demand_grouping,
    normalize_sector_profile,
)
from sr2025_to_tulipa.config import (
    DemandAggregationRule,
    ModelOptions,
    NaturalGasProfileParticipant,
)


def _options(**changes: object) -> ModelOptions:
    """Create model options with final-energy heat defaults."""
    values: dict[str, object] = {
        "flexible_heat_mode": "final_energy_demand",
        "industrial_heat_mode": "final_energy_demand",
        "agriculture_heat_mode": "final_energy_demand",
        "dsr_mode": "final_electricity_demand",
        "add_network_losses_to_demand": True,
        "add_power_sector_own_use_to_demand": True,
    }
    values.update(changes)
    return ModelOptions(**values)  # type: ignore[arg-type]


def test_profile_is_normalized_and_small_gap_is_accepted() -> None:
    """A sub-0.1 TWh source difference is accepted but still normalized."""
    hourly = [100.0] * HOURS_PER_YEAR
    raw_pj = sum(hourly) * 3.6e-6
    rows, reconciliation = normalize_sector_profile(
        "example", 2030, "hydrogen", "transport", hourly, raw_pj - 0.18
    )

    assert reconciliation.status == "pass"
    assert abs(sum(row.normalized_mw for row in rows) * 3.6e-6 - (raw_pj - 0.18)) < 1e-10


def test_material_profile_gap_is_marked_normalized() -> None:
    """A difference of at least 0.1 TWh remains visible as normalized."""
    hourly = [100.0] * HOURS_PER_YEAR
    raw_pj = sum(hourly) * 3.6e-6
    _, reconciliation = normalize_sector_profile(
        "example", 2030, "hydrogen", "industry", hourly, raw_pj - 0.36
    )

    assert reconciliation.status == "normalized"
    assert reconciliation.difference_twh == pytest.approx(0.1)


def test_nonzero_annual_demand_can_use_explicit_flat_proxy() -> None:
    rows, reconciliation = normalize_sector_profile(
        "example",
        2030,
        "methane",
        "bunkers",
        [0.0] * HOURS_PER_YEAR,
        3.6,
        flat_if_missing=True,
    )

    assert len(rows) == HOURS_PER_YEAR
    assert len({row.normalized_mw for row in rows}) == 1
    assert rows[0].normalized_mw == pytest.approx(1_000_000 / HOURS_PER_YEAR)
    assert reconciliation.status == "flat_proxy"


def test_cng_shape_and_lng_residual_are_not_combined() -> None:
    """A CNG curve must not be scaled to unsupported LNG demand."""
    cng_rows, cng = normalize_sector_profile(
        "example", 2030, "methane", "transport", [100.0] * HOURS_PER_YEAR, 3.1536
    )
    lng_rows, lng = normalize_sector_profile(
        "example",
        2030,
        "methane",
        "transport_lng",
        [0.0] * HOURS_PER_YEAR,
        7.2,
        flat_if_missing=True,
    )

    assert cng.status == "pass"
    assert cng_rows
    assert lng.status == "flat_proxy"
    assert sum(row.normalized_mw for row in lng_rows) == pytest.approx(2_000_000.0)


def test_heat_participant_follows_its_selector() -> None:
    """Industrial heat is included only on the final-energy boundary."""
    rule = NaturalGasProfileParticipant(
        participant="industry_burner.input (MW)",
        sector="industry",
        category="heat",
        inclusion_mode="industrial_heat",
        reason="Industrial heat",
    )

    assert _participant_included(rule, _options()) is True
    assert _participant_included(
        rule, _options(industrial_heat_mode="heat_demand")
    ) is False


def test_network_losses_follow_their_selector() -> None:
    """Methane losses are included only when the loss option is enabled."""
    rule = NaturalGasProfileParticipant(
        participant="energy_network_gas_loss.input (MW)",
        sector="energy",
        category="losses",
        inclusion_mode="network_losses",
        reason="Explicit methane demand",
    )

    assert _participant_included(rule, _options()) is True
    assert _participant_included(
        rule, _options(add_network_losses_to_demand=False)
    ) is False


def test_demand_grouping_sums_sources_and_excludes_disabled_rules() -> None:
    """Hourly profiles are summed by approved group after exclusions."""
    profiles = [
        CanonicalProfileRow("example", 2030, "methane", "industry", 1, 2.0, 3.0, 1.5),
        CanonicalProfileRow("example", 2030, "methane", "losses", 1, 1.0, 1.0, 1.0),
        CanonicalProfileRow("example", 2030, "methane", "energy", 1, 10.0, 10.0, 1.0),
    ]
    rules = [
        DemandAggregationRule("methane", "industry", "productive_demand", True, "national"),
        DemandAggregationRule("methane", "losses", "productive_demand", True, "national"),
        DemandAggregationRule("methane", "energy", "energy_sector", False, "national"),
    ]

    grouped = apply_demand_grouping(profiles, rules)

    assert len(grouped) == 1
    assert grouped[0].carrier == "methane"
    assert grouped[0].demand_group == "productive_demand"
    assert grouped[0].raw_mw == pytest.approx(3.0)
    assert grouped[0].normalized_mw == pytest.approx(4.0)


def test_electricity_participants_have_explicit_boundaries() -> None:
    """Electricity consumers distinguish final demand from system activity."""
    household = _classify_electricity_participant(
        "households_final_demand_for_appliances_electricity.input (MW)"
    )
    battery = _classify_electricity_participant(
        "energy_flexibility_mv_batteries_electricity.input (MW)"
    )
    losses = _classify_electricity_participant(
        "energy_power_hv_network_loss.input (MW)"
    )

    assert household.sector == "households"
    assert household.inclusion_mode == "always"
    assert battery.inclusion_mode == "never"
    assert losses.inclusion_mode == "network_losses"


def test_electricity_dsr_can_leave_fixed_industry_demand() -> None:
    """Selected DSR is routed to a separate non-fixed profile."""
    rule = _classify_electricity_participant(
        "industry_final_demand_for_other_dsr_load_shifting_electricity.input (MW)"
    )

    assert _electricity_target_sector(rule, _options()) == "industry"
    assert _electricity_target_sector(rule, _options(dsr_mode="dsr")) == "dsr"


def test_local_battery_exchange_is_net_demand() -> None:
    """Local P2P discharge offsets charging in fixed electricity demand."""
    participant = "households_flexibility_p2p_electricity.input (MW)"
    output = "households_flexibility_p2p_electricity.output (MW)"
    curve = CurveCsvResponse(
        fieldnames=(participant, output),
        rows=({participant: "5", output: "3"},),
        retrieved_at="now",
        response_checksum="checksum",
    )

    assert _electricity_participant_values(curve, participant) == [2.0]


def test_dsr_exchange_is_net_demand() -> None:
    """DSR output offsets shifted electricity input."""
    participant = "industry_final_demand_for_other_dsr_load_shifting_electricity.input (MW)"
    output = "industry_final_demand_for_other_dsr_load_shifting_electricity.output (MW)"
    curve = CurveCsvResponse(
        fieldnames=(participant, output),
        rows=({participant: "5", output: "5"},),
        retrieved_at="now",
        response_checksum="checksum",
    )

    assert _electricity_participant_values(curve, participant) == [0.0]