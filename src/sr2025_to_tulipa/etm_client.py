from __future__ import annotations

import os
import hashlib
import json
import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


class EtmApiError(RuntimeError):
    """Report an ETM request that could not be completed."""


class EtmAuthenticationError(EtmApiError):
    """Report a missing or rejected ETM access token."""


@dataclass(frozen=True)
class SavedScenario:
    """Hold the metadata needed to identify one saved scenario."""

    saved_scenario_id: int
    scenario_id: int
    scenario_id_history: tuple[int, ...]
    title: str
    area_code: str
    end_year: int
    model_version: str
    saved_updated_at: str
    scenario_updated_at: str


@dataclass(frozen=True)
class FeaturedScenarioLink:
    """Identify the stable engine scenario behind a featured page."""

    scenario_id: int
    engine_base_url: str


@dataclass(frozen=True)
class GQueryResponse:
    """Hold raw gquery values and their retrieval provenance."""

    values: dict[str, dict[str, Any]]
    retrieved_at: str
    response_checksum: str


@dataclass(frozen=True)
class CurveCsvResponse:
    """Hold one ETM hourly curve table with retrieval provenance."""

    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]
    retrieved_at: str
    response_checksum: str


class EtmClient:
    """Read scenario metadata from one ETM engine."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create a client for one fixed API base URL."""
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            transport=transport,
        )

    @classmethod
    def from_environment(cls, base_url: str) -> EtmClient:
        """Create a client using the token stored in the environment."""
        token = os.getenv("ETM_API_TOKEN")
        if not token or token == "your-token":
            raise EtmAuthenticationError(
                "Set ETM_API_TOKEN to a real personal access token with "
                "scenario read access."
            )
        return cls(base_url, token=token)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> EtmClient:
        """Return this client for use in a context manager."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client when leaving a context manager."""
        self.close()

    def get_saved_scenario(self, saved_scenario_id: int) -> SavedScenario:
        """Resolve a saved scenario to its current engine scenario."""
        data = self._get_json(f"/saved_scenarios/{saved_scenario_id}")
        scenario = data["scenario"]
        return SavedScenario(
            saved_scenario_id=int(data["id"]),
            scenario_id=int(data["scenario_id"]),
            scenario_id_history=tuple(int(value) for value in data["scenario_id_history"]),
            title=str(data["title"]),
            area_code=str(data["area_code"]),
            end_year=int(data["end_year"]),
            model_version=str(data["version"]),
            saved_updated_at=str(data["updated_at"]),
            scenario_updated_at=str(scenario["updated_at"]),
        )

    def get_scenario(self, scenario_id: int) -> dict[str, Any]:
        """Fetch the full metadata for one engine scenario."""
        return self._get_json(f"/scenarios/{scenario_id}")

    def get_area(self, area_code: str) -> dict[str, Any]:
        """Fetch the source metadata for one ETM area."""
        return self._get_json(f"/areas/{area_code}")

    def query_scenario(
        self, scenario_id: int, query_keys: list[str]
    ) -> GQueryResponse:
        """Read configured graph-query values without changing the scenario."""
        data = self._request_json(
            "PUT",
            f"/scenarios/{scenario_id}",
            json_body={"gqueries": query_keys},
        )
        values = data.get("gqueries")
        if not isinstance(values, dict):
            raise EtmApiError("ETM returned no gquery result object.")

        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return GQueryResponse(
            values=values,
            retrieved_at=datetime.now(UTC).isoformat(),
            response_checksum=checksum,
        )

    def get_curve_csv(self, scenario_id: int, curve_name: str) -> CurveCsvResponse:
        """Read one supported hourly ETM curve export without mutation."""
        path = f"/scenarios/{scenario_id}/curves/{curve_name}.csv"
        try:
            response = self._client.get(path)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            details = _response_error_details(error.response)
            raise EtmApiError(
                f"ETM request failed for {path} ({error.response.status_code}): "
                f"{details}"
            ) from error
        except httpx.HTTPError as error:
            raise EtmApiError(f"ETM request failed for {path}: {error}") from error

        reader = csv.DictReader(io.StringIO(response.text))
        if reader.fieldnames is None:
            raise EtmApiError(f"ETM returned no CSV header for {path}.")
        rows = tuple(dict(row) for row in reader)
        checksum = hashlib.sha256(response.content).hexdigest()
        return CurveCsvResponse(
            fieldnames=tuple(reader.fieldnames),
            rows=rows,
            retrieved_at=datetime.now(UTC).isoformat(),
            response_checksum=checksum,
        )

    def _get_json(self, path: str) -> dict[str, Any]:
        """Send one GET request and return its JSON object."""
        return self._request_json("GET", path)

    def _request_json(
        self,
        method: str,
        path: str,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Send one ETM request and return its JSON object."""
        try:
            response = self._client.request(method, path, json=json_body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                raise EtmAuthenticationError(
                    "ETM rejected ETM_API_TOKEN. Create a valid personal access "
                    "token with scenario read access."
                ) from error
            details = _response_error_details(error.response)
            raise EtmApiError(
                f"ETM request failed for {path} ({error.response.status_code}): "
                f"{details}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise EtmApiError(f"ETM request failed for {path}: {error}") from error

        if not isinstance(data, dict):
            raise EtmApiError(f"ETM returned a non-object response for {path}.")
        return data


def resolve_featured_scenario(
    saved_scenario_id: int,
    transport: httpx.BaseTransport | None = None,
) -> FeaturedScenarioLink:
    """Resolve a public featured page to its stable engine scenario."""
    url = f"https://my.energytransitionmodel.com/saved_scenarios/{saved_scenario_id}"
    try:
        with httpx.Client(timeout=30.0, transport=transport) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise EtmApiError(
            f"Could not open featured scenario {saved_scenario_id}: {error}"
        ) from error

    parser = _FeaturedScenarioParser()
    parser.feed(response.text)
    if not parser.load_url:
        raise EtmApiError(
            f"Featured scenario {saved_scenario_id} has no stable-engine load link."
        )

    parsed_url = urlparse(parser.load_url)
    scenario_ids = parse_qs(parsed_url.query).get("scenario_id", [])
    if not scenario_ids:
        raise EtmApiError(
            f"Featured scenario {saved_scenario_id} has no engine scenario ID."
        )

    engine_host = parsed_url.netloc.replace(
        ".energytransitionmodel.com", ".engine.energytransitionmodel.com"
    )
    engine_base_url = f"{parsed_url.scheme}://{engine_host}/api/v3"
    return FeaturedScenarioLink(int(scenario_ids[0]), engine_base_url)


class _FeaturedScenarioParser(HTMLParser):
    """Find the stable-engine load link in a My ETM page."""

    def __init__(self) -> None:
        super().__init__()
        self.load_url: str | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        """Keep the first link that loads a saved scenario."""
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and "/saved_scenarios/" in href and "/load?" in href:
            self.load_url = href


def _response_error_details(response: httpx.Response) -> str:
    """Extract ETM's public error message without exposing request headers."""
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:500] or response.reason_phrase

    if isinstance(data, dict) and data.get("errors"):
        errors = data["errors"]
        if isinstance(errors, list):
            return "; ".join(str(message) for message in errors)
        return str(errors)
    return str(data)[:500]
