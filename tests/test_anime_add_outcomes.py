"""Exercise the FastMCP tool pipeline against a fake Medusa HTTP API."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client

from mcp_medusa import server


@pytest.fixture
def call_tool(monkeypatch):
    monkeypatch.setenv("MEDUSA_URL", "http://medusa.test")
    monkeypatch.delenv("MEDUSA_WEB_ROOT", raising=False)
    monkeypatch.setenv("MEDUSA_API_KEY", "test-key")
    async_client = httpx.AsyncClient

    def call(tool: str, args: dict[str, Any], handler: Callable[[httpx.Request], httpx.Response]):
        def route(req: httpx.Request) -> httpx.Response:
            assert req.url.host == "medusa.test"
            assert req.headers["X-Api-Key"] == "test-key"
            return handler(req)

        async def run():
            async with Client(server.mcp) as client:
                result = await client.call_tool(tool, args)
                return result.data

        monkeypatch.setattr(server.httpx, "AsyncClient", lambda **kw: async_client(
            transport=httpx.MockTransport(route), **kw,
        ))
        return asyncio.run(run())

    return call


def item(anime_id, outcome, **fields):
    return {
        "animeId": anime_id,
        "outcome": outcome,
        "action": {"ready_to_add": "would_add", "queued": "added"}.get(outcome, outcome),
        "success": outcome in {"ready_to_add", "queued", "already_queued"},
        "matched": outcome == "already_exists",
        **fields,
    }


def bulk(*results, version=1):
    return httpx.Response(200, json={"data": {"identityVersion": version, "results": results}})


def body(req):
    assert (req.method, req.url.path) == ("POST", "/api/v2/anime/bulk-add")
    return json.loads(req.content)


@pytest.mark.parametrize("tool,args", [
    ("resolve_and_add_anime", {"mal_id": 123}),
    ("bulk_add_anime", {"items": [{"animeId": 123}, {"malId": 124}]}),
])
@pytest.mark.parametrize("version", [None, 2, True, "1"])
def test_never_writes_to_legacy_or_incompatible_backend(call_tool, tool, args, version):
    requests = []

    def handler(req):
        payload = body(req)
        requests.append(payload)
        assert payload["dry_run"] is True
        response = {"results": [{"animeId": i["anime_id"], "action": "would_add", "success": True}
                                for i in payload["items"]]}
        if version is not None:
            response["identityVersion"] = version
        return httpx.Response(200, json=response)

    value = call_tool(tool, {**args, "execute": True}, handler)
    assert len(requests) == 1
    assert requests[0]["identity_version"] == 1
    results = [value] if tool == "resolve_and_add_anime" else value["results"]
    assert len(results) == (1 if tool == "resolve_and_add_anime" else 2)
    for result in results:
        assert result["outcome"] == "error"
        assert result["reason"] == "unsupported_identity_version"
        assert result["success"] is False


@pytest.mark.parametrize("candidates,expected,reason", [
    ([], "not_found", "catalog_title_not_found"),
    ([{"animeId": 1, "displayTitle": "Example One"}, {"animeId": 2, "displayTitle": "Example Two"}],
     "needs_review", "ambiguous_catalog_title"),
])
def test_unresolved_catalog_title_never_reaches_add(call_tool, candidates, expected, reason):
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert req.method == "GET"
        assert req.url.params["q"] == "Example"
        return httpx.Response(200, json={"items": candidates})

    result = call_tool("resolve_and_add_anime", {"title": "Example", "execute": True}, handler)
    assert paths == ["/api/v2/anime/search"]
    assert result["outcome"] == expected
    assert result["reason"] == reason
    assert [c["animeId"] for c in result["candidates"]] == [c["animeId"] for c in candidates]


def test_dry_run_ignores_fuzzy_search_presence(call_tool):
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.method == "GET":
            assert req.url.path == "/api/v2/anime/search"
            return httpx.Response(200, json={"items": [{
                "animeId": 123, "displayTitle": "Black Clover", "matched": True,
                "match": {"slug": "tvdb888", "title": "Black Summoner"},
            }]})
        assert body(req)["dry_run"] is True
        return bulk(item(123, "ready_to_add", tvdbId=456))

    result = call_tool("resolve_and_add_anime", {"title": "Black Clover"}, handler)
    assert paths == ["/api/v2/anime/search", "/api/v2/anime/bulk-add"]
    assert result["outcome"] == result["result"]["outcome"] == "ready_to_add"
    assert result["tvdbId"] == 456
    assert result["matched"] is result["match"]["matched"] is False
    assert "slug" not in result["match"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_single_execute_verifies_only_exact_tvdb_identity(call_tool, confirmed):
    posts = []
    lookups = []

    def handler(req):
        if req.method == "POST":
            payload = body(req)
            posts.append(payload)
            if payload["dry_run"]:
                return bulk(item(123, "ready_to_add", tvdbId=456))
            return bulk(item(123, "queued", tvdbId=456))
        lookups.append(req.url.path)
        if len(lookups) == 1:
            return httpx.Response(200, json={"data": {"id": {"slug": "tvdb888"}, "title": "Similar"}})
        if confirmed:
            return httpx.Response(200, json={"data": {"id": {"slug": "tvdb456"}, "title": "Correct"}})
        return httpx.Response(404, json={"error": "Series not found"})

    result = call_tool("resolve_and_add_anime", {
        "mal_id": 123, "execute": True, "directory_name": "Custom", "root_dir": "/shows",
        "initial_release_group": "ExampleSubs", "release_group_fallback_days": 4,
        "fallback_release_groups": ["Backup"], "verify_attempts": 2, "verify_delay_seconds": 0,
    }, handler)
    assert [req["dry_run"] for req in posts] == [True, False]
    assert posts[0]["items"] == posts[1]["items"] == [{"anime_id": 123, "directory_name": "Custom"}]
    assert posts[0]["defaults"] == posts[1]["defaults"] == {
        "source": "myanimelist", "root_dir": "/shows", "anime": True, "scene": False, "status": "wanted",
        "initial_release_group": "ExampleSubs", "release_group_fallback_days": 4,
        "fallback_release_groups": ["Backup"],
    }
    assert lookups == ["/api/v2/series/tvdb456"] * 2
    assert result["outcome"] == "queued"
    assert result["success"] is True
    assert result["verified"] is result["matched"] is confirmed
    if confirmed:
        assert result["match"]["slug"] == "tvdb456"
    else:
        assert "pending" in result["verificationHint"]
        assert "error" not in result


def test_bulk_preflights_once_and_rechecks_only_ready_items(call_tool):
    posts = []

    def handler(req):
        payload = body(req)
        posts.append(payload)
        if payload["dry_run"]:
            return bulk(
                item(101, "ready_to_add", tvdbId=501),
                item(102, "already_exists", match={"slug": "tvdb502", "title": "Existing Series"}),
                item(103, "needs_review", reason="ambiguous_mapping", candidates=[{"tvdbId": 503}]),
                item(104, "mapping_failed"),
                item(105, "ready_to_add", tvdbId=505),
            )
        return bulk(item(101, "queued", tvdbId=501), item(105, "already_queued", tvdbId=505))

    result = call_tool("bulk_add_anime", {
        "items": [
            {"animeId": 100, "filterDecision": "skip", "aiDecision": "add"},
            {"animeId": 101, "directoryName": "Custom", "aiDecision": "ADD"},
            {"animeId": "102", "aiDecision": "add"},
            {"animeId": 102, "aiDecision": "add"},
            {"malId": 103, "aiDecision": "add"},
            {"animeId": 104, "aiDecision": "add"},
            {"animeId": 105, "aiDecision": "add"},
            {"animeId": 106, "aiDecision": "maybe"},
            {"animeId": 107, "aiDecision": "add"},
        ],
        "decision_field": "aiDecision", "max_items": 6, "execute": True,
    }, handler)
    assert [p["dry_run"] for p in posts] == [True, False]
    assert [p["identity_version"] for p in posts] == [1, 1]
    assert [p["verify"] for p in posts] == [False, False]
    assert [[i["anime_id"] for i in p["items"]] for p in posts] == [[101, 102, 103, 104, 105], [101, 105]]
    assert posts[0]["items"][0] == posts[1]["items"][0] == {"anime_id": 101, "directory_name": "Custom"}
    assert [i["outcome"] for i in result["results"]] == [
        "queued", "invalid", "already_exists", "needs_review", "mapping_failed", "already_queued",
    ]
    assert result["count"] == 6
    assert result["requested"] == 5
    assert result["successes"] == 2
    assert result["failures"] == 4
    assert result["results"][2]["match"] == {"slug": "tvdb502", "title": "Existing Series"}
    assert result["results"][3]["candidates"] == [{"tvdbId": 503}]


def test_bulk_dry_run_preserves_ready_and_not_found_outcomes(call_tool):
    posts = []

    def handler(req):
        posts.append(body(req))
        return bulk(item(123, "ready_to_add", tvdbId=456), item(124, "not_found"))

    result = call_tool("bulk_add_anime", {"items": [{"animeId": 123}, {"animeId": 124}]}, handler)
    assert [p["dry_run"] for p in posts] == [True]
    assert result["dryRun"] is True
    assert result["results"] == [item(123, "ready_to_add", tvdbId=456), item(124, "not_found")]


@pytest.mark.parametrize("review", [False, True])
def test_single_nonready_never_writes(call_tool, review):
    posts = []
    if review:
        outcome = item(123, "needs_review", reason="fuzzy_match", candidates=[{"tvdbId": 456}])
    else:
        outcome = item(123, "already_exists", match={"slug": "tvdb456", "title": "Existing"})

    def handler(req):
        posts.append(body(req))
        return bulk(outcome)

    result = call_tool("resolve_and_add_anime", {"mal_id": 123, "execute": True}, handler)
    assert [p["dry_run"] for p in posts] == [True]
    assert result["outcome"] == outcome["outcome"]
    assert "verified" not in result
    if review:
        assert result["candidates"] == outcome["candidates"]
        assert result["reason"] == outcome["reason"]
    else:
        assert result["match"]["slug"] == "tvdb456"
        assert result["match"]["title"] == "Existing"
        assert result["matched"] is True


def test_malformed_versioned_preflight_never_writes(call_tool):
    posts = []

    def handler(req):
        posts.append(body(req))
        return bulk(item(123, "ready_to_add", tvdbId=456), item(999, "error"))

    result = call_tool("bulk_add_anime", {"items": [{"animeId": 123}, {"animeId": 124}], "execute": True}, handler)
    assert [p["dry_run"] for p in posts] == [True]
    assert [i["reason"] for i in result["results"]] == ["invalid_identity_response"] * 2


def test_missing_bulk_endpoint_never_writes(call_tool):
    posts = []

    def handler(req):
        posts.append(body(req))
        return httpx.Response(404, json={"error": "Not found"})

    result = call_tool("resolve_and_add_anime", {"mal_id": 123, "execute": True}, handler)
    assert [p["dry_run"] for p in posts] == [True]
    assert result["outcome"] == "error"
    assert result["reason"] == "identity_preflight_failed"


def test_execute_result_replaces_preflight_identity(call_tool):
    posts = []

    def handler(req):
        payload = body(req)
        posts.append(payload)
        if payload["dry_run"]:
            return bulk(item(123, "ready_to_add", tvdbId=456))
        return bulk(item(123, "already_exists", match={"slug": "tvdb456", "title": "Concurrent Add"}))

    result = call_tool("resolve_and_add_anime", {"mal_id": 123, "execute": True}, handler)
    assert [p["dry_run"] for p in posts] == [True, False]
    assert result["outcome"] == "already_exists"
    assert result["matched"] is True
    assert "verified" not in result


def test_lost_execute_response_is_not_retried(call_tool):
    posts = []

    def handler(req):
        payload = body(req)
        posts.append(payload)
        if payload["dry_run"]:
            return bulk(item(123, "ready_to_add", tvdbId=456))
        raise httpx.ReadTimeout("request timed out", request=req)

    result = call_tool("resolve_and_add_anime", {"mal_id": 123, "execute": True}, handler)
    assert [p["dry_run"] for p in posts] == [True, False]
    assert result["outcome"] == "error"
    assert result["reason"] == "write_unconfirmed"
