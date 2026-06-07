"""MCP tools for Medusa anime automation."""

from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Any, Literal
from urllib.parse import urljoin

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

Season = Literal["WINTER", "SPRING", "SUMMER", "FALL"]
AnimeSource = Literal["myanimelist", "livechart"]
SeasonalSort = Literal["anime_num_list_users", "anime_score"]

DEFAULT_TIMEOUT = float(os.getenv("MEDUSA_TIMEOUT", "30"))
DEFAULT_MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
DEFAULT_MCP_PORT = int(os.getenv("MCP_PORT", "8000"))


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _transport_security() -> TransportSecuritySettings:
    enabled = os.getenv("MCP_DNS_REBINDING_PROTECTION", "false").strip().lower() in {"1", "true", "yes", "on"}
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=enabled,
        allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS"),
        allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS"),
    )


mcp = FastMCP(
    "mcp-medusa",
    host=DEFAULT_MCP_HOST,
    port=DEFAULT_MCP_PORT,
    transport_security=_transport_security(),
)


class MedusaError(RuntimeError):
    """Raised when Medusa returns an API error."""


def _settings() -> tuple[str, str | None]:
    base_url = os.getenv("MEDUSA_URL", "").strip()
    if not base_url:
        raise MedusaError("MEDUSA_URL is required")

    api_key = os.getenv("MEDUSA_API_KEY", "").strip() or None
    return base_url.rstrip("/") + "/", api_key


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    return headers


def _api_url(path: str) -> str:
    base_url, _ = _settings()
    web_root = os.getenv("MEDUSA_WEB_ROOT", "").strip().strip("/")
    prefix = f"{web_root}/api/v2/" if web_root else "api/v2/"
    return urljoin(base_url, prefix + path.lstrip("/"))


def _unwrap_response(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _filter_fields(item: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    if not fields:
        return item
    return {field: item.get(field) for field in fields if field in item}


def _filter_payload(payload: Any, fields: list[str] | None) -> Any:
    if not fields:
        return payload

    if isinstance(payload, list):
        return [_filter_fields(item, fields) if isinstance(item, dict) else item for item in payload]

    if isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            filtered = dict(payload)
            filtered["items"] = [
                _filter_fields(item, fields) if isinstance(item, dict) else item
                for item in payload["items"]
            ]
            return filtered
        return _filter_fields(payload, fields)

    return payload


def _current_anime_season(today: date | None = None) -> tuple[int, Season]:
    today = today or date.today()
    if today.month in (1, 2, 3):
        return today.year, "WINTER"
    if today.month in (4, 5, 6):
        return today.year, "SPRING"
    if today.month in (7, 8, 9):
        return today.year, "SUMMER"
    return today.year, "FALL"


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    _, api_key = _settings()
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers=_headers(api_key)) as client:
        response = await client.request(method, _api_url(path), **kwargs)

    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if response.status_code >= 400:
        message = payload
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message") or payload
        raise MedusaError(f"Medusa API error {response.status_code}: {message}")

    return _unwrap_response(payload)


@mcp.tool()
async def add_anime(
    anime_id: int,
    root_dir: str,
    source: AnimeSource = "myanimelist",
    anime: bool = True,
    scene: bool = False,
    status: str = "wanted",
    initial_release_group: str | None = None,
    fallback_release_groups: list[str] | None = None,
    release_group_fallback_days: int = 7,
    directory_name: str | None = None,
) -> Any:
    """Add an anime series to Medusa via /api/v2/anime/add."""
    body: dict[str, Any] = {
        "anime_id": anime_id,
        "source": source,
        "root_dir": root_dir,
        "anime": anime,
        "scene": scene,
        "status": status,
        "release_group_fallback_days": release_group_fallback_days,
    }

    optional_values = {
        "initial_release_group": initial_release_group,
        "fallback_release_groups": fallback_release_groups,
        "directory_name": directory_name,
    }
    body.update({key: value for key, value in optional_values.items() if value not in (None, "", [])})

    return await _request("POST", "anime/add", json=body)


@mcp.tool()
async def seasonal_anime(
    year: int | None = None,
    season: Season | None = None,
    source: AnimeSource = "myanimelist",
    source_sort: SeasonalSort = "anime_num_list_users",
    page: int = 1,
    limit: int = 10,
    fields: list[str] | None = None,
) -> Any:
    """Query a paginated seasonal anime page from Medusa, optionally returning only selected fields."""
    default_year, default_season = _current_anime_season()
    query_year = year or default_year
    query_season = season or default_season

    payload = await _request(
        "GET",
        "anime/seasonal",
        params={
            "year": query_year,
            "season": query_season,
            "source": source,
            "sourceSort": source_sort,
            "page": page,
            "limit": limit,
        },
    )
    return _filter_payload(payload, fields)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for Medusa")
    parser.add_argument("--transport", choices=("stdio", "sse"), default=os.getenv("MCP_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=DEFAULT_MCP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
