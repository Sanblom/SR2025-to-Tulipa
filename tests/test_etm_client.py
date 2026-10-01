import httpx
import pytest

from sr2025_to_tulipa.etm_client import (
    EtmApiError,
    EtmAuthenticationError,
    EtmClient,
    resolve_featured_scenario,
)

BASE_URL = "https://2025-01.engine.energytransitionmodel.com/api/v3"


def test_saved_scenario_is_resolved_with_bearer_authentication() -> None:
    """The client sends its token and returns typed saved-scenario metadata."""
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.url.path.endswith("/saved_scenarios/19916")
        return httpx.Response(
            200,
            json={
                "id": 19916,
                "scenario_id": 123456,
                "scenario_id_history": [123455],
                "title": "NBNL scenarios 2025 Koersvaste Middenweg",
                "area_code": "nl",
                "end_year": 2030,
                "version": "2025.01",
                "updated_at": "2025-02-01T10:00:00Z",
                "scenario": {"updated_at": "2025-01-31T10:00:00Z"},
            },
        )

    transport = httpx.MockTransport(handle_request)
    with EtmClient(BASE_URL, token="test-token", transport=transport) as client:
        scenario = client.get_saved_scenario(19916)

    assert scenario.saved_scenario_id == 19916
    assert scenario.scenario_id == 123456
    assert scenario.scenario_id_history == (123455,)
    assert scenario.end_year == 2030


def test_http_errors_are_reported_as_etm_errors() -> None:
    """HTTP failures become clear pipeline errors."""
    transport = httpx.MockTransport(lambda request: httpx.Response(401, request=request))

    with EtmClient(BASE_URL, transport=transport) as client:
        with pytest.raises(EtmAuthenticationError, match="rejected ETM_API_TOKEN"):
            client.get_saved_scenario(19916)


def test_placeholder_token_is_rejected_before_a_request(monkeypatch) -> None:
    """The README placeholder cannot be mistaken for a real token."""
    monkeypatch.setenv("ETM_API_TOKEN", "your-token")

    with pytest.raises(EtmAuthenticationError, match="real personal access token"):
        EtmClient.from_environment(BASE_URL)


def test_api_error_details_are_reported() -> None:
    """A safe ETM error body explains a rejected request."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            400,
            request=request,
            json={"errors": ["Example ETM validation message"]},
        )
    )

    with EtmClient(BASE_URL, token="test-token", transport=transport) as client:
        with pytest.raises(EtmApiError, match="Example ETM validation message"):
            client.get_saved_scenario(19916)


def test_featured_page_resolves_stable_engine_scenario() -> None:
    """A public featured page reveals its stable engine scenario ID."""
    html = """
    <a href="https://2025-01.energytransitionmodel.com/saved_scenarios/19912/load?current_user=false&amp;scenario_id=495&amp;title=NBNL_KM_2025">
      Open scenario
    </a>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, request=request, text=html)
    )

    link = resolve_featured_scenario(19912, transport=transport)

    assert link.scenario_id == 495
    assert link.engine_base_url == (
        "https://2025-01.engine.energytransitionmodel.com/api/v3"
    )


def test_scenario_queries_are_read_without_scenario_updates() -> None:
    """Query requests contain gqueries and no scenario mutation payload."""
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path.endswith("/scenarios/495")
        assert request.read() == b'{"gqueries":["final_demand_of_electricity"]}'
        return httpx.Response(
            200,
            json={
                "scenario": {"id": 495},
                "gqueries": {
                    "final_demand_of_electricity": {
                        "present": 427.1,
                        "future": 445.4,
                        "unit": "PJ",
                    }
                },
            },
        )

    transport = httpx.MockTransport(handle_request)
    with EtmClient(BASE_URL, transport=transport) as client:
        result = client.query_scenario(495, ["final_demand_of_electricity"])

    assert result.values["final_demand_of_electricity"]["future"] == 445.4
    assert len(result.response_checksum) == 64


def test_curve_csv_is_read_without_scenario_updates() -> None:
    """Hourly reconciliation tables are parsed from a read-only endpoint."""
    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/scenarios/495/curves/network_gas.csv")
        return httpx.Response(
            200,
            request=request,
            text="Time,households_cooker_network_gas.input (MW)\n2025-01-01 00:00,2.5\n",
        )

    transport = httpx.MockTransport(handle_request)
    with EtmClient(BASE_URL, transport=transport) as client:
        result = client.get_curve_csv(495, "network_gas")

    assert result.fieldnames[0] == "Time"
    assert result.rows[0]["households_cooker_network_gas.input (MW)"] == "2.5"
    assert len(result.response_checksum) == 64