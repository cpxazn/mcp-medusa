"""MCP tools for Medusa anime automation."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import argparse
import difflib
import os
import re
import textwrap
import unicodedata
from datetime import date
from typing import Any, Literal, cast
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
DEFAULT_ROOT_DIR = os.getenv("MEDUSA_DEFAULT_ANIME_ROOT_DIR", "/media/videos/Anime")
DEFAULT_INITIAL_RELEASE_GROUP = os.getenv("MEDUSA_DEFAULT_INITIAL_RELEASE_GROUP", "SubsPlease")
DEFAULT_RELEASE_GROUP_FALLBACK_DAYS = int(
    os.getenv("MEDUSA_DEFAULT_RELEASE_GROUP_FALLBACK_DAYS", "7")
)

CHIBI_SPINOFF_TITLE_KEYWORDS = [" wan!", " chibi", " mini", " petit", " puchi"]
CHIBI_SPINOFF_SYNOPSIS_KEYWORDS = ["chibi", "spin-off", "spinoff", "gag"]
KIDS_GENRES = {"kids"}
BOYS_LOVE_GENRES = {"boys love", "shounen ai"}
MUSIC_STORY_ALLOWLIST_TITLE_KEYWORDS = ["oshi no ko", "k-on", "bocchi the rock", "beck"]
IDOL_MUSIC_FRANCHISE_TITLE_KEYWORDS = [
    "22/7",
    "aikatsu",
    "bang dream",
    "d4dj",
    "ensemble stars",
    "hypnosis mic",
    "idolish7",
    "idolmaster",
    "idolm@ster",
    "love live",
    "pretty rhythm",
    "pripara",
    "project sekai",
    "selection project",
    "uta no prince",
    "utapri",
    "wake up, girls",
]
NOT_FIRST_SEASON_SYNOPSIS_PATTERNS = [
    re.compile(r"^(second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|final) season\b"),
    re.compile(r"^sequel to\b"),
    re.compile(r"^continuation of\b"),
    re.compile(r"^final part of\b"),
]
NOT_FIRST_SEASON_TITLE_PATTERNS = [
    re.compile(r"\bseason\s*[2-9]\b"),
    re.compile(r"\b[2-9](?:nd|rd|th)\s+season\b"),
    re.compile(r"\bpart\s*[2-9]\b"),
    re.compile(r"\b(?:ii|iii|iv|v|vi|vii|viii|ix|x)\b"),
    re.compile(r"\b[2-9]\s*$"),
]

REVIEW_INSTRUCTIONS = """Review these seasonal anime candidates for Daniel.

Be conservative: only mark `add` when the show is a strong fit from the available title, genres, popularity, and synopsis.
Use `maybe` for borderline or unclear shows. Use `skip` for shows that look like poor fits.
Do not override hard filter exclusions; this review is for already-filtered candidates.
Daniel can still add missed shows later through the single-anime add flow.

Return JSON shaped like:
[
  {
    "animeId": 12345,
    "aiDecision": "add|maybe|skip",
    "aiReason": "short reason"
  }
]
"""


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _transport_security() -> TransportSecuritySettings:
    enabled = os.getenv("MCP_DNS_REBINDING_PROTECTION", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
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
    return {field: item.get(field) for field in fields}


def _csv_param(values: list[str] | None) -> str | None:
    if not values:
        return None
    return ",".join(str(value).strip() for value in values if str(value).strip()) or None


def _filter_payload(payload: Any, fields: list[str] | None) -> Any:
    if not fields:
        return payload

    if isinstance(payload, list):
        return [
            _filter_fields(item, fields) if isinstance(item, dict) else item for item in payload
        ]

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


def _items_from_payload(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("results", "items", "anime"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise MedusaError(f"unexpected Medusa payload shape: {type(payload).__name__}")


def _current_anime_season(today: date | None = None) -> tuple[int, Season]:
    today = today or date.today()
    if today.month in (1, 2, 3):
        return today.year, "WINTER"
    if today.month in (4, 5, 6):
        return today.year, "SPRING"
    if today.month in (7, 8, 9):
        return today.year, "SUMMER"
    return today.year, "FALL"


def _normalize_season(season: str | None) -> Season | None:
    if season is None:
        return None

    normalized = season.strip().upper()
    if normalized in ("WINTER", "SPRING", "SUMMER", "FALL"):
        return cast(Season, normalized)

    raise MedusaError("season must be one of: WINTER, SPRING, SUMMER, FALL")


def _normalize_anime_source(source: str) -> AnimeSource:
    normalized = source.strip().lower()
    if normalized in ("myanimelist", "livechart"):
        return cast(AnimeSource, normalized)

    raise MedusaError("source must be one of: myanimelist, livechart")


def _normalize_seasonal_sort(source_sort: str) -> SeasonalSort:
    normalized = source_sort.strip().lower()
    if normalized in ("anime_num_list_users", "anime_score"):
        return cast(SeasonalSort, normalized)

    raise MedusaError("source_sort must be one of: anime_num_list_users, anime_score")


def _parse_status_name(status: str) -> int:
    """Convert a status string (e.g. 'wanted') to Medusa's numeric episode status."""
    try:
        status_id = int(status)
    except (TypeError, ValueError):
        status_id = None

    if status_id is not None:
        return status_id

    status_lower = status.strip().lower()
    status_map = {
        "wanted": 3,
        "skipped": 5,
        "ignored": 7,
        "downloaded": 4,
        "archived": 6,
        "snatched": 2,
        "unaired": 1,
        "failed": 11,
        "subtitled": 10,
        "unset": -1,
    }
    if status_lower in status_map:
        return status_map[status_lower]

    raise MedusaError(
        f"Unknown status: {status!r}. Valid: {', '.join(sorted(status_map.keys()))}"
    )


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



def _scheduler_issue_summary(schedulers: dict[str, Any]) -> str | None:
    """Return a human-readable summary of any scheduler issues, or None if healthy."""
    issues: list[str] = []
    for key, info in schedulers.items():
        name = info.get("name", key)
        if not info.get("isAlive"):
            issues.append(f"{name}: thread is DEAD — needs restart")
        elif info.get("isEnabled") and not info.get("isAlive"):
            issues.append(f"{name}: enabled but thread not alive")
    if issues:
        return " | ".join(issues)

    show_queue = schedulers.get("showQueue", {})
    if show_queue.get("isAlive") and show_queue.get("isEnabled") and show_queue.get("queueLength", 0) > 0:
        return f"ShowQueue has {show_queue['queueLength']} pending items (may be processing or stuck)"

    return None
def _normalize_title(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _title_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("displayTitle", "titleRomanji", "titleEnglish", "titleJapanese", "directoryName"):
        value = item.get(key)
        if value:
            values.append(str(value))

    synonyms = item.get("titleSynonyms")
    if isinstance(synonyms, list):
        values.extend(str(value) for value in synonyms if value)

    return values


def _candidate_score(query: str, item: dict[str, Any], rank: int) -> float:
    normalized_query = _normalize_title(query)
    if not normalized_query:
        return 0.0

    best = 0.0
    for title in _title_values(item):
        normalized_title = _normalize_title(title)
        if not normalized_title:
            continue
        if normalized_title == normalized_query:
            best = max(best, 100.0)
        elif normalized_query in normalized_title:
            best = max(best, 92.0)
        elif normalized_title in normalized_query:
            best = max(best, 88.0)
        else:
            best = max(
                best,
                difflib.SequenceMatcher(None, normalized_query, normalized_title).ratio() * 100,
            )

    rank_bonus = max(0.0, 8.0 - (rank * 2.0))
    return min(100.0, best + rank_bonus)


def _has_exact_title_match(query: str, item: dict[str, Any]) -> bool:
    normalized_query = _normalize_title(query)
    return bool(normalized_query) and any(
        _normalize_title(title) == normalized_query for title in _title_values(item)
    )


async def _anime_details(anime_id: int, source: str) -> dict[str, Any]:
    payload = await _request(
        "GET", "anime/details", params={"id": anime_id, "source": _normalize_anime_source(source)}
    )
    if not isinstance(payload, dict):
        raise MedusaError(f"unexpected anime details payload shape: {type(payload).__name__}")
    return payload


async def _search_anime(query: str, source: str, limit: int) -> list[dict[str, Any]]:
    query_source = _normalize_anime_source(source)
    payload = await _request(
        "GET",
        "anime/search",
        params={"q": query, "source": query_source, "includeDetails": "true", "limit": limit},
    )
    items = _items_from_payload(payload)

    candidates: list[dict[str, Any]] = []
    for rank, item in enumerate(items[:limit]):
        candidates.append(
            {
                **item,
                "resolutionScore": round(_candidate_score(query, item, rank), 2),
                "exactTitleMatch": _has_exact_title_match(query, item),
                "searchRank": rank + 1,
            }
        )
    return candidates


def _confident_match(
    candidates: list[dict[str, Any]], min_score: float, score_gap: float
) -> dict[str, Any] | None:
    if not candidates:
        return None

    first = candidates[0]
    first_score = float(first.get("resolutionScore") or 0)
    second_score = float(candidates[1].get("resolutionScore") or 0) if len(candidates) > 1 else 0

    if first.get("exactTitleMatch"):
        return first
    if first_score >= min_score and first_score - second_score >= score_gap:
        return first
    return None


def _compact_anime_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "animeId": item.get("animeId") or item.get("malId"),
        "displayTitle": item.get("displayTitle")
        or item.get("titleEnglish")
        or item.get("titleRomanji"),
        "titleRomanji": item.get("titleRomanji"),
        "titleEnglish": item.get("titleEnglish"),
        "animeType": item.get("animeType"),
        "year": item.get("year"),
        "status": item.get("status"),
        "matched": item.get("matched"),
        "url": item.get("url"),
    }


def _summarize_synopsis(value: Any, max_chars: int) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return textwrap.shorten(text, width=max_chars, placeholder="...")


def _details_summary(details: dict[str, Any], max_synopsis_chars: int) -> dict[str, Any]:
    url = details.get("url")
    return {
        "animeId": details.get("animeId") or details.get("malId"),
        "displayTitle": details.get("displayTitle"),
        "titleRomanji": details.get("titleRomanji"),
        "titleEnglish": details.get("titleEnglish"),
        "titleJapanese": details.get("titleJapanese"),
        "titleSynonyms": details.get("titleSynonyms") or [],
        "animeType": details.get("animeType"),
        "year": details.get("year"),
        "season": details.get("season"),
        "status": details.get("status"),
        "episodes": details.get("episodes") or details.get("episodeInfo"),
        "score": details.get("score"),
        "numListUsers": details.get("numListUsers"),
        "genres": details.get("genres") or [],
        "studios": details.get("studios") or [],
        "matched": details.get("matched"),
        "synopsis": _summarize_synopsis(details.get("synopsis"), max_synopsis_chars),
        "imageUrl": details.get("imageUrl"),
        "url": url,
        "malUrl": url,
    }


def _normalized_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_normalized_text(item) for item in value)
    return str(value).casefold()


def _title_text(item: dict[str, Any]) -> str:
    return " ".join(
        _normalized_text(item.get(key)) for key in ("displayTitle", "titleRomanji", "titleEnglish")
    )


def _genre_set(item: dict[str, Any]) -> set[str]:
    return {_normalized_text(genre) for genre in item.get("genres", [])}


def _hard_exclusion_reasons(item: dict[str, Any], min_num_list_users: int) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    genres = _genre_set(item)
    title = f" {_title_text(item)} "
    synopsis = _normalized_text(item.get("synopsis"))
    anime_type = _normalized_text(item.get("animeType"))
    num_list_users = item.get("numListUsers")

    if min_num_list_users > 0 and (num_list_users is None or num_list_users < min_num_list_users):
        reasons.append(
            {
                "ruleId": "exclude_low_num_list_users",
                "reason": f"numListUsers is below {min_num_list_users}.",
                "source": "source_data",
            }
        )
    if anime_type != "tv":
        reasons.append(
            {"ruleId": "exclude_non_tv", "reason": "Anime type is not TV.", "source": "source_data"}
        )
    if any(pattern.search(synopsis) for pattern in NOT_FIRST_SEASON_SYNOPSIS_PATTERNS) or any(
        pattern.search(title) for pattern in NOT_FIRST_SEASON_TITLE_PATTERNS
    ):
        reasons.append(
            {
                "ruleId": "exclude_not_first_season",
                "reason": "Title/synopsis indicates this is not the first season or part.",
                "source": "source_data",
            }
        )
    if genres & KIDS_GENRES:
        reasons.append(
            {
                "ruleId": "exclude_kids",
                "reason": "Genre indicates anime aimed at kids.",
                "source": "source_data",
            }
        )
    if genres & BOYS_LOVE_GENRES:
        reasons.append(
            {
                "ruleId": "exclude_boys_love",
                "reason": "Genre indicates boys love.",
                "source": "source_data",
            }
        )

    is_allowlisted_music_story = any(
        keyword in title for keyword in MUSIC_STORY_ALLOWLIST_TITLE_KEYWORDS
    )
    has_music_genre = "music" in genres
    has_idol_genre = any("idol" in genre for genre in genres)
    has_idol_music_franchise_title = any(
        keyword in title for keyword in IDOL_MUSIC_FRANCHISE_TITLE_KEYWORDS
    )
    if (
        not is_allowlisted_music_story
        and has_music_genre
        and (has_idol_genre or has_idol_music_franchise_title)
    ):
        reasons.append(
            {
                "ruleId": "exclude_idol_music_franchise",
                "reason": "Music/idol franchise pattern; excludes idol-adjacent media franchise entries, not all music anime.",
                "source": "heuristic",
            }
        )

    title_has_chibi_pattern = any(keyword in title for keyword in CHIBI_SPINOFF_TITLE_KEYWORDS)
    synopsis_has_chibi_pattern = any(
        keyword in synopsis for keyword in CHIBI_SPINOFF_SYNOPSIS_KEYWORDS
    )
    is_comedy = "comedy" in genres
    is_sequel_or_spinoff = "season" in synopsis or "spin-off" in synopsis or "spinoff" in synopsis
    if title_has_chibi_pattern or (
        synopsis_has_chibi_pattern and is_comedy and is_sequel_or_spinoff
    ):
        reasons.append(
            {
                "ruleId": "exclude_chibi_spinoff",
                "reason": "Title/synopsis pattern suggests a chibi or gag spin-off.",
                "source": "heuristic",
            }
        )

    return reasons


def _classify_seasonal_candidate(item: dict[str, Any], min_num_list_users: int) -> dict[str, Any]:
    reasons = _hard_exclusion_reasons(item, min_num_list_users)
    return {**item, "filterDecision": "skip" if reasons else "candidate", "filterReasons": reasons}


def _compact_review_item(item: dict[str, Any]) -> dict[str, Any]:
    synopsis = " ".join(str(item.get("synopsis") or "").split())
    if len(synopsis) > 700:
        synopsis = synopsis[:697].rstrip() + "..."
    return {
        "animeId": item.get("animeId") or item.get("malId"),
        "displayTitle": item.get("displayTitle"),
        "titleRomanji": item.get("titleRomanji"),
        "titleEnglish": item.get("titleEnglish"),
        "year": item.get("year"),
        "animeType": item.get("animeType"),
        "genres": item.get("genres") or [],
        "numListUsers": item.get("numListUsers"),
        "synopsis": synopsis,
        "url": item.get("url"),
    }


@mcp.tool()
async def diagnose_release_groups(
    series_slug: str,
    season: int = 1,
    episode: int = 1,
    max_retries: int = 3,
    retry_delay_seconds: float = 5.0,
) -> dict[str, Any]:
    """Diagnose release group issues for a series by comparing config vs available releases.

    Checks provider cache first; if empty, triggers a manual search and retries.
    Returns a structured diagnosis with recommendation.

    Args:
        series_slug: Series slug (e.g. "tvdb1234").
        season: Season number to check (default: 1).
        episode: Episode number to check (default: 1).
        max_retries: How many times to retry when a search is in progress (default: 3).
        retry_delay_seconds: Seconds to wait between retries (default: 5.0).
    """
    import asyncio

    result: dict[str, Any] = {}
    for attempt in range(max_retries + 1):
        result = await _request(
            "POST",
            f"series/{series_slug}/release/diagnose",
            json={"season": season, "episode": episode},
        )

        if not isinstance(result, dict):
            raise MedusaError(f"unexpected diagnose payload shape: {type(result).__name__}")

        search_triggered = result.get("searchTriggered", False)
        diagnosis = result.get("diagnosis", {})
        diagnosis_code = diagnosis.get("code") if isinstance(diagnosis, dict) else None

        # If search is still in progress and we have retries left, wait and retry
        if diagnosis_code == "search_in_progress" and search_triggered and attempt < max_retries:
            await asyncio.sleep(retry_delay_seconds)
            continue

        return result

    # Should not reach here, but if all retries exhausted:
    return {
        "config": result.get("config", {}),
        "episode": result.get("episode", {}),
        "availableGroups": [],
        "searchTriggered": True,
        "diagnosis": {
            "code": "search_timed_out",
            "summary": f"Search still in progress after {max_retries} retries. Try again later.",
            "recommendation": None,
        },
    }


@mcp.tool()
async def update_release_groups(
    series_slug: str,
    whitelist: list[str] | None = None,
    blacklist: list[str] | None = None,
    fallback_groups: list[str] | None = None,
    fallback_days: int | None = None,
) -> dict[str, Any]:
    """Update release group configuration for a series.

    Sends PATCH /api/v2/series/{slug} with the specified config.release.* fields.
    Only the provided fields are changed; omitted fields are left as-is.

    Args:
        series_slug: Series slug (e.g. "tvdb1234").
        whitelist: Whitelisted release groups.
        blacklist: Blacklisted release groups.
        fallback_groups: Anime release group fallback list (in priority order).
        fallback_days: Days before falling back to the next group.
    """
    release: dict[str, Any] = {}
    if whitelist is not None:
        release["whitelist"] = whitelist
    if blacklist is not None:
        release["blacklist"] = blacklist
    if fallback_groups is not None:
        release["fallbackGroups"] = fallback_groups
    if fallback_days is not None:
        release["fallbackDays"] = fallback_days

    if not release:
        raise MedusaError(
            "at least one of whitelist, blacklist, fallback_groups, or fallback_days must be provided"
        )

    return await _request("PATCH", f"series/{series_slug}", json={"config": {"release": release}})


@mcp.tool()
async def get_aliases(
    series_slug: str,
    season: int | None = None,
) -> list[dict[str, Any]]:
    """List scene exceptions (aliases) for a series.

    Scene exceptions are alternative episode/release titles that Medusa
    recognizes when searching. This tool reads them; use create_alias to add new ones.

    Args:
        series_slug: Series slug (e.g. "tvdb1234").
        season: Optional season number to filter by.
    """
    params: dict[str, Any] = {"series": series_slug}
    if season is not None:
        params["season"] = season

    result = await _request("GET", "alias", params=params)
    if isinstance(result, list):
        return result
    raise MedusaError(f"unexpected alias list shape: {type(result).__name__}")


@mcp.tool()
async def create_alias(
    series_slug: str,
    name: str,
    season: int | None = None,
) -> dict[str, Any]:
    """Create a local scene exception (alias) for a series.

    This enables Medusa to recognize an alternative release title when searching,
    allowing it to match releases that use a different naming scheme.
    Only local (user-managed) scene exceptions can be created via this tool.

    Args:
        series_slug: Series slug (e.g. "tvdb1234").
        name: The alternative title to add as a scene exception.
        season: Optional season number. If omitted, applies to all seasons.
    """
    body: dict[str, Any] = {
        "series": series_slug,
        "name": name,
        "type": "local",
    }
    if season is not None:
        body["season"] = season

    return await _request("POST", "alias", json=body)


@mcp.tool()
async def delete_alias(alias_id: int) -> None:
    """Delete a scene exception (alias) by ID.

    Args:
        alias_id: The alias ID to delete.
    """
    await _request("DELETE", f"alias/{alias_id}")


@mcp.tool()
async def scheduler_status() -> dict[str, Any]:
    """Return Medusa scheduler and queue status.

    Shows whether each scheduler thread is alive, enabled, and currently active.
    Useful for diagnosing stuck queues."""
    config = await _request("GET", "config")
    if not isinstance(config, dict):
        raise MedusaError(f"unexpected config shape: {type(config).__name__}")

    system = config.get("system", {})
    schedulers = system.get("schedulers", [])

    queue_info: dict[str, Any] = {}
    for s in schedulers:
        key = s.get("key", "unknown")
        queue_info[key] = {
            "name": s.get("name", key),
            "isAlive": s.get("isAlive"),
            "isEnabled": s.get("isEnabled"),
            "isActive": s.get("isActive"),
            "queueLength": s.get("queueLength", 0),
        }

    return {
        "schedulers": queue_info,
        "issueSummary": _scheduler_issue_summary(queue_info),
    }


@mcp.tool()
async def search_tvdb(
    query: str,
    language: str = "en",
) -> list[dict[str, Any]]:
    """Search TVDB for a show by name and return matching TVDB IDs.

    Use this when an anime add fails due to AniDB-to-TVDB mapping issues.
    Find the correct TVDB ID here, then add the show with add_series."""
    payload = await _request(
        "GET",
        "internal/searchIndexersForShowName",
        params={"query": query, "indexerId": "1", "language": language},
    )
    results: list[dict[str, Any]] = []
    raw_results = payload.get("results", []) if isinstance(payload, dict) else []
    for entry in raw_results:
        if isinstance(entry, list) and len(entry) >= 5:
            results.append({
                "indexer": entry[0],
                "indexerId": entry[1],
                "url": entry[2] + str(entry[3]) if isinstance(entry[2], str) else "",
                "tvdbId": entry[3],
                "title": entry[4],
                "firstAired": entry[5] if len(entry) > 5 else None,
                "network": entry[6] if len(entry) > 6 else None,
                "overview": entry[7] if len(entry) > 7 else None,
            })
    return results


@mcp.tool()
async def add_series(
    tvdb_id: int,
    root_dir: str,
    anime: bool = True,
    status: str = "wanted",
    language: str | None = None,
    show_dir: str | None = None,
    scene: bool = False,
    paused: bool = False,
) -> Any:
    """Add a series to Medusa directly by TVDB ID via POST /api/v2/series.

    Use this as a fallback when add_anime fails due to AniDB-to-TVDB mapping
    issues.  First find the correct TVDB ID with search_tvdb, then add it
    here.  This bypasses anime resolution entirely and adds via the standard
    series endpoint."""
    numeric_status = _parse_status_name(status)
    options: dict[str, Any] = {
        "status": numeric_status,
        "rootDir": root_dir,
        "anime": anime,
        "scene": scene,
        "paused": paused,
    }
    if language:
        options["language"] = language
    if show_dir:
        options["showDir"] = show_dir

    body: dict[str, Any] = {
        "id": {"tvdb": str(tvdb_id)},
        "options": options,
    }
    return await _request("POST", "series", json=body)


@mcp.tool()
async def set_episode_status(
    series_slug: str,
    episodes: list[str],
    status: str = "wanted",
) -> dict[str, Any]:
    """Set episode statuses for a series via POST /api/v2/internal/updateEpisodeStatus.

    Use when TVDB metadata is out of sync with actual episode status and
    Medusa is not downloading episodes that should be available.  Set affected
    episodes to "wanted" to force Medusa to search for them.

    Args:
        series_slug: Series slug (e.g. "tvdb370761").
        episodes: Episode slugs (e.g. ["s01e01", "s01e02"]).
        status: Target status string ("wanted", "skipped", "ignored").
    """
    numeric_status = _parse_status_name(status)
    body = {
        "status": numeric_status,
        "shows": [
            {
                "slug": series_slug,
                "episodes": episodes,
            }
        ],
    }
    return await _request("POST", "internal/updateEpisodeStatus", json=body)

@mcp.tool()
async def add_anime(
    anime_id: int,
    root_dir: str,
    source: str = "myanimelist",
    anime: bool = True,
    scene: bool = False,
    status: str = "wanted",
    initial_release_group: str | None = None,
    fallback_release_groups: list[str] | None = None,
    release_group_fallback_days: int = 7,
    directory_name: str | None = None,
    language: str | None = None,
) -> Any:
    """Add an anime series to Medusa via /api/v2/anime/add.

    Note: Some anime may fail with "no name on TVDBv2" due to
    AniDB→TVDB ID mapping issues. As a workaround, use search_tvdb to find
    the correct TVDB ID, then add_series to add it directly."""
    body: dict[str, Any] = {
        "anime_id": anime_id,
        "source": _normalize_anime_source(source),
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
        "language": language,
    }
    body.update(
        {key: value for key, value in optional_values.items() if value not in (None, "", [])}
    )

    return await _request("POST", "anime/add", json=body)


async def _bulk_add_request(
    items: list[dict[str, Any]],
    source: str,
    root_dir: str,
    initial_release_group: str | None,
    release_group_fallback_days: int,
    fallback_release_groups: list[str] | None,
    dry_run: bool,
    verify: bool,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "source": _normalize_anime_source(source),
        "root_dir": root_dir,
        "anime": True,
        "scene": False,
        "status": "wanted",
        "release_group_fallback_days": release_group_fallback_days,
    }
    optional_defaults = {
        "initial_release_group": initial_release_group,
        "fallback_release_groups": fallback_release_groups,
    }
    defaults.update(
        {key: value for key, value in optional_defaults.items() if value not in (None, "", [])}
    )

    payload = await _request(
        "POST",
        "anime/bulk-add",
        json={"defaults": defaults, "items": items, "dry_run": dry_run, "verify": verify},
    )
    if not isinstance(payload, dict):
        raise MedusaError(f"unexpected bulk add payload shape: {type(payload).__name__}")
    return payload


@mcp.tool()
async def seasonal_anime(
    year: int | None = None,
    season: str | None = None,
    source: str = "myanimelist",
    source_sort: str = "anime_num_list_users",
    page: int = 1,
    limit: int = 10,
    fields: list[str] | None = None,
) -> Any:
    """Query a paginated seasonal anime page from Medusa, optionally returning only selected fields.

    Season, source, and source_sort are case-insensitive.
    """
    default_year, default_season = _current_anime_season()
    query_year = year or default_year
    query_season = _normalize_season(season) or default_season
    query_source = _normalize_anime_source(source)
    query_source_sort = _normalize_seasonal_sort(source_sort)

    params: dict[str, Any] = {
        "year": query_year,
        "season": query_season,
        "source": query_source,
        "sourceSort": query_source_sort,
        "page": page,
        "limit": limit,
    }
    if fields:
        params["fields"] = _csv_param(fields)

    payload = await _request("GET", "anime/seasonal", params=params)
    return _filter_payload(payload, fields)


@mcp.tool()
async def resolve_anime_title(
    title: str,
    source: str = "myanimelist",
    limit: int = 10,
    min_score: float = 88.0,
    score_gap: float = 8.0,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve an anime title to MAL candidates with match/ambiguous/no_match decision."""
    candidates = await _search_anime(title, source, limit)
    if fields:
        fields_with_resolution = [*fields, "resolutionScore", "exactTitleMatch", "searchRank"]
        candidates = [_filter_fields(candidate, fields_with_resolution) for candidate in candidates]

    match = _confident_match(candidates, min_score, score_gap)
    return {
        "query": title,
        "source": _normalize_anime_source(source),
        "decision": "match" if match else "ambiguous" if candidates else "no_match",
        "match": match,
        "candidates": candidates,
    }


@mcp.tool()
async def anime_info(
    title: str | None = None,
    mal_id: int | None = None,
    source: str = "myanimelist",
    limit: int = 10,
    min_score: float = 88.0,
    score_gap: float = 8.0,
    max_synopsis_chars: int = 700,
) -> dict[str, Any]:
    """Return compact anime details and Medusa presence by title or MAL ID. Does not add anime."""
    query_source = _normalize_anime_source(source)
    anime_id: int | None = mal_id
    resolution: dict[str, Any] | None = None

    if anime_id is None:
        if not title:
            raise MedusaError("provide title or mal_id")
        resolved = await resolve_anime_title(title, query_source, limit, min_score, score_gap)
        if resolved["decision"] != "match":
            return resolved
        resolution = resolved
        resolved_match: dict[str, Any] = resolved.get("match") or {}
        anime_id = resolved_match.get("animeId") or resolved_match.get("malId")

    if not isinstance(anime_id, int):
        raise MedusaError("resolved match did not include an integer MAL ID")

    summary = _details_summary(await _anime_details(anime_id, query_source), max_synopsis_chars)
    if resolution is not None:
        summary["resolution"] = {
            "decision": resolution["decision"],
            "match": _compact_anime_summary(resolution.get("match") or {}),
        }
    return summary


@mcp.tool()
async def seasonal_candidates(
    year: int | None = None,
    season: str | None = None,
    source: str = "myanimelist",
    source_sort: str = "anime_num_list_users",
    limit: int = 25,
    max_pages: int = 3,
    min_num_list_users: int = 3000,
    include_skipped: bool = False,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch server-filtered seasonal anime candidates and apply residual preference heuristics."""
    default_year, default_season = _current_anime_season()
    query_year = year or default_year
    query_season = _normalize_season(season) or default_season
    query_source = _normalize_anime_source(source)
    query_source_sort = _normalize_seasonal_sort(source_sort)

    requested_fields = fields
    server_fields = set(fields or []) | {
        "animeId",
        "malId",
        "displayTitle",
        "titleRomanji",
        "titleEnglish",
        "animeType",
        "genres",
        "numListUsers",
        "synopsis",
        "url",
        "matched",
    }
    params: dict[str, Any] = {
        "year": query_year,
        "season": query_season,
        "source": query_source,
        "sourceSort": query_source_sort,
        "page": 1,
        "limit": limit,
        "animeType": "TV",
        "minNumListUsers": min_num_list_users,
        "excludeGenres": "Kids,Boys Love",
        "matched": "false",
        "firstSeasonOnly": "true",
        "fields": _csv_param(sorted(server_fields)),
    }

    all_items: list[dict[str, Any]] = []
    last_payload: dict[str, Any] | None = None
    for page in range(1, max_pages + 1):
        params["page"] = page
        payload = await _request("GET", "anime/seasonal", params=params)
        if isinstance(payload, dict):
            last_payload = payload
        items = _items_from_payload(payload)
        if not items:
            break
        all_items.extend(items)
        if isinstance(payload, dict) and payload.get("hasNextPage") is False:
            break
        if len(items) < limit:
            break

    # Medusa now handles the expensive/common filters server-side. Keep local
    # classification for residual OpenClaw-specific heuristics, such as chibi
    # spin-offs and idol-franchise exclusions.
    classified = [_classify_seasonal_candidate(item, min_num_list_users) for item in all_items]
    candidates = [item for item in classified if item["filterDecision"] == "candidate"]
    skipped = [item for item in classified if item["filterDecision"] == "skip"]
    output_items = classified if include_skipped else candidates
    output_items = cast(list[dict[str, Any]], _filter_payload(output_items, requested_fields))
    return {
        "year": query_year,
        "season": query_season,
        "source": query_source,
        "serverFiltered": True,
        "total": len(all_items),
        "serverTotal": last_payload.get("total") if last_payload else None,
        "candidateCount": len(candidates),
        "skippedCount": len(skipped),
        "items": output_items,
    }


@mcp.tool()
async def prepare_seasonal_review(
    items: list[dict[str, Any]], max_items: int | None = None
) -> dict[str, Any]:
    """Compact seasonal candidates into an AI review packet."""
    candidates = [
        _compact_review_item(item)
        for item in items
        if isinstance(item, dict) and item.get("filterDecision") != "skip"
    ]
    if max_items is not None:
        candidates = candidates[:max_items]
    return {
        "instructions": REVIEW_INSTRUCTIONS,
        "decisionField": "aiDecision",
        "allowedDecisions": ["add", "maybe", "skip"],
        "candidates": candidates,
    }


@mcp.tool()
async def resolve_and_add_anime(
    title: str | None = None,
    mal_id: int | None = None,
    source: str = "myanimelist",
    root_dir: str = DEFAULT_ROOT_DIR,
    initial_release_group: str | None = DEFAULT_INITIAL_RELEASE_GROUP,
    release_group_fallback_days: int = DEFAULT_RELEASE_GROUP_FALLBACK_DAYS,
    fallback_release_groups: list[str] | None = None,
    directory_name: str | None = None,
    limit: int = 10,
    min_score: float = 88.0,
    score_gap: float = 8.0,
    verify_attempts: int = 6,
    verify_delay_seconds: float = 5.0,
    execute: bool = False,
) -> dict[str, Any]:
    """Resolve and optionally add one anime. Dry-run unless execute is true."""
    query_source = _normalize_anime_source(source)
    match: dict[str, Any] | None
    candidates: list[dict[str, Any]] = []

    if mal_id is not None:
        match = await _anime_details(mal_id, query_source)
    else:
        if not title:
            raise MedusaError("provide title or mal_id")
        resolution = await resolve_anime_title(title, query_source, limit, min_score, score_gap)
        if resolution["decision"] != "match":
            return resolution
        match = resolution.get("match")
        candidates = resolution.get("candidates") or []

    anime_id = (match or {}).get("animeId") or (match or {}).get("malId")
    if not isinstance(anime_id, int):
        raise MedusaError("resolved match did not include an integer MAL ID")

    bulk_item: dict[str, Any] = {"anime_id": anime_id}
    if directory_name:
        bulk_item["directory_name"] = directory_name

    add_arguments: dict[str, Any] = {
        "anime_id": anime_id,
        "source": query_source,
        "root_dir": root_dir,
        "anime": True,
        "scene": False,
        "status": "wanted",
        "initial_release_group": initial_release_group,
        "release_group_fallback_days": release_group_fallback_days,
        "fallback_release_groups": fallback_release_groups,
        "directory_name": directory_name,
    }
    plan = {
        "decision": "execute" if execute else "dry_run",
        "match": _compact_anime_summary(match or {}),
        "addArguments": add_arguments,
    }
    if candidates:
        plan["candidates"] = candidates

    add_result = await _bulk_add_request(
        [bulk_item],
        query_source,
        root_dir,
        initial_release_group,
        release_group_fallback_days,
        fallback_release_groups,
        dry_run=not execute,
        verify=execute,
    )
    first_result = (add_result.get("results") or [{}])[0]

    response: dict[str, Any] = {
        **plan,
        "addResult": add_result,
        "result": first_result,
        "success": bool(first_result.get("success")),
    }

    # Post-add verification: poll anime_info to confirm the show actually landed.
    # The bulk-add returns success as soon as the item is queued, but the actual
    # add happens asynchronously in the show queue and can fail (e.g. TVDB issues).
    if execute and response["success"]:
        for attempt in range(1, verify_attempts + 1):
            await asyncio.sleep(verify_delay_seconds)
            try:
                info = await anime_info(mal_id=anime_id, source=query_source)
            except MedusaError:
                continue
            if isinstance(info, dict) and info.get("matched"):
                response["verified"] = True
                response["match"] = info
                break
        else:
            response["verified"] = False
            response["verificationHint"] = (
                "Add queued but not confirmed in Medusa after polling. "
                "The show queue likely failed (common cause: AniDB to TVDB mapping issue). "
                "Use scheduler_status to check queue health, then fall back to "
                "search_tvdb to find the TVDB ID, then add_series to add it directly."
            )

    return response


@mcp.tool()
async def bulk_add_anime(
    items: list[dict[str, Any]],
    source: str = "myanimelist",
    root_dir: str = DEFAULT_ROOT_DIR,
    initial_release_group: str | None = DEFAULT_INITIAL_RELEASE_GROUP,
    release_group_fallback_days: int = DEFAULT_RELEASE_GROUP_FALLBACK_DAYS,
    fallback_release_groups: list[str] | None = None,
    decision_field: str | None = None,
    allowed_decisions: list[str] | None = None,
    max_items: int | None = None,
    pause_seconds: float = 0.0,
    verify_attempts: int = 6,
    verify_delay_seconds: float = 5.0,
    execute: bool = False,
) -> dict[str, Any]:
    """Dry-run or add multiple anime items. Skips filterDecision=skip; execute must be true for writes."""
    # Kept for backward-compatible MCP schemas; Medusa bulk-add now owns pacing/verification.
    _ = (pause_seconds, verify_attempts, verify_delay_seconds)
    query_source = _normalize_anime_source(source)
    allowed = {value.casefold() for value in (allowed_decisions or ["add"])}

    def include(item: dict[str, Any]) -> bool:
        if item.get("filterDecision") == "skip":
            return False
        if not decision_field:
            return True
        return str(item.get(decision_field, "")).casefold() in allowed

    selected = [item for item in items if isinstance(item, dict) and include(item)]
    if max_items is not None:
        selected = selected[:max_items]

    request_items: list[dict[str, Any]] = []
    local_failures: list[dict[str, Any]] = []
    for item in selected:
        anime_id = item.get("animeId") or item.get("malId")
        title = (
            item.get("displayTitle")
            or item.get("titleEnglish")
            or item.get("titleRomanji")
            or anime_id
            or "(untitled)"
        )
        if not isinstance(anime_id, int):
            local_failures.append(
                {
                    "title": title,
                    "success": False,
                    "error": "Missing animeId/malId.",
                    "dryRun": not execute,
                }
            )
            continue

        request_item: dict[str, Any] = {"anime_id": anime_id}
        directory_name = item.get("directoryName") or item.get("directory_name")
        if directory_name:
            request_item["directory_name"] = directory_name
        request_items.append(request_item)

    if request_items:
        response = await _bulk_add_request(
            request_items,
            query_source,
            root_dir,
            initial_release_group,
            release_group_fallback_days,
            fallback_release_groups,
            dry_run=not execute,
            verify=execute,
        )
    else:
        response = {
            "dryRun": not execute,
            "requested": 0,
            "successes": 0,
            "failures": 0,
            "results": [],
        }

    results = [*(response.get("results") or []), *local_failures]
    return {
        **response,
        "execute": execute,
        "count": len(results),
        "successes": sum(1 for item in results if item.get("success")),
        "failures": sum(1 for item in results if item.get("success") is False),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP server for Medusa")
    parser.add_argument(
        "--transport", choices=("stdio", "sse"), default=os.getenv("MCP_TRANSPORT", "stdio")
    )
    parser.add_argument("--host", default=DEFAULT_MCP_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_MCP_PORT)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
