# mcp-medusa

MCP server that exposes Medusa anime automation tools.

## Tools

### `add_anime`

Adds an anime to Medusa using `POST /api/v2/anime/add`.

Common arguments:

```json
{
  "anime_id": 62076,
  "source": "myanimelist",
  "root_dir": "/media/videos/Anime",
  "anime": true,
  "scene": false,
  "status": "wanted",
  "initial_release_group": "SubsPlease",
  "fallback_release_groups": ["SubsPlease", "Erai-raws"],
  "release_group_fallback_days": 7
}
```

Arguments:

| Argument | Required | Default | Allowed values / notes |
| --- | --- | --- | --- |
| `anime_id` | yes | none | Source-specific anime ID. For `myanimelist`, this is the MAL anime ID. |
| `root_dir` | yes | none | Medusa root directory path for the new series. |
| `source` | no | `myanimelist` | Case-insensitive: `myanimelist`, `livechart` |
| `anime` | no | `true` | Usually keep `true`. |
| `scene` | no | `false` | Whether to enable scene numbering. |
| `status` | no | `wanted` | Medusa episode status string, commonly `wanted`, `skipped`, or `ignored`. |
| `initial_release_group` | no | none | First/active release group to whitelist, for example `SubsPlease`. |
| `fallback_release_groups` | no | none | Ordered list of release groups to rotate through. Include the initial group first for deterministic behavior. |
| `release_group_fallback_days` | no | `7` | Days after episode airdate before switching to the next fallback group. |
| `directory_name` | no | Medusa-generated | Optional series folder name override. |

### `seasonal_anime`

Queries Medusa seasonal anime using `GET /api/v2/anime/seasonal`.

```json
{
  "year": 2026,
  "season": "SPRING",
  "source": "myanimelist",
  "source_sort": "anime_num_list_users",
  "page": 1,
  "limit": 10,
  "fields": ["animeId", "displayTitle", "year", "animeType", "genres", "score", "imageUrl"]
}
```

Minimal call using current anime season defaults:

```json
{}
```

Arguments:

| Argument | Required | Default | Allowed values / notes |
| --- | --- | --- | --- |
| `year` | no | current year | If omitted, calculated from the MCP server's current date. |
| `season` | no | current anime season | Case-insensitive: `WINTER`, `SPRING`, `SUMMER`, `FALL` or values like `Summer`. If omitted, calculated from the MCP server's current month. |
| `source` | no | `myanimelist` | Case-insensitive: `myanimelist`, `livechart` |
| `source_sort` | no | `anime_num_list_users` | Case-insensitive: `anime_num_list_users`, `anime_score` |
| `page` | no | `1` | Medusa result page number. Must be `1` or greater. |
| `limit` | no | `10` | Results per page. Medusa allows `1` through `1000`; default is intentionally small to avoid MCP client truncation. |
| `fields` | no | `null` | Optional list of response fields to keep. If omitted/null, the full Medusa anime objects are returned. |

Current anime season defaults use this month mapping:

| Months | Season |
| --- | --- |
| January-March | `WINTER` |
| April-June | `SPRING` |
| July-September | `SUMMER` |
| October-December | `FALL` |

Useful `fields` values include:

```json
["animeId", "source", "displayTitle", "titleRomanji", "titleEnglish", "year", "season", "animeType", "status", "episodes", "episodeInfo", "genres", "studios", "score", "numListUsers", "imageUrl", "anidbId", "tvdbId", "malId", "url", "directoryName"]
```

### `resolve_anime_title`

Resolves a title to enriched Medusa/MyAnimeList candidates using `GET /api/v2/anime/search` with `source`, `includeDetails=true`, and `limit`.

```json
{
  "title": "Frieren",
  "source": "myanimelist",
  "limit": 10,
  "min_score": 88,
  "score_gap": 8
}
```

Returns `decision` as `match`, `ambiguous`, or `no_match`, plus scored candidates.

### `anime_info`

Returns compact anime details and Medusa presence by title or MAL ID without adding anything.

```json
{
  "title": "Frieren",
  "source": "myanimelist"
}
```

or:

```json
{
  "mal_id": 52991,
  "source": "myanimelist"
}
```

### `seasonal_candidates`

Fetches seasonal candidates using Medusa's server-side seasonal filters before applying residual OpenClaw-specific heuristics.

```json
{
  "year": 2026,
  "season": "SUMMER",
  "source": "myanimelist",
  "limit": 25,
  "min_num_list_users": 3000,
  "fields": ["animeId", "displayTitle", "animeType", "genres", "numListUsers", "synopsis"]
}
```

The MCP tool sends Medusa filters such as `animeType=TV`, `minNumListUsers`, `excludeGenres=Kids,Boys Love`, `matched=false`, `firstSeasonOnly=true`, and `fields` to reduce payload size.

### `prepare_seasonal_review`

Compacts seasonal candidate items into an AI review packet with conservative review instructions.

```json
{
  "items": [{"animeId": 62076, "displayTitle": "Example Title"}],
  "max_items": 25
}
```

### `resolve_and_add_anime`

Resolves and dry-runs or executes a single add through Medusa's bulk-add endpoint. Writes require `execute: true`.

```json
{
  "title": "Frieren",
  "root_dir": "/media/videos/Anime",
  "execute": false
}
```

### `scheduler_status`

Returns Medusa scheduler thread health and queue state. Use when anime adds appear stuck or unverified.

```json
{}
```

### `search_tvdb`

Searches TVDB by show name for a TVDB ID. Use as fallback when an anime add fails due to AniDB-to-TVDB mapping issues.

```json
{ "query": "Dorohedoro", "language": "en" }
```

### `add_series`

Adds a series directly by TVDB ID via `POST /api/v2/series`. Bypasses anime resolution entirely. Use after `search_tvdb` when `add_anime` fails.

Arguments:

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `tvdb_id` | yes | none | TVDB show ID (from `search_tvdb`). |
| `root_dir` | yes | none | Medusa root directory path. |
| `anime` | no | `true` | |
| `status` | no | `wanted` | Accepts strings like `wanted`, `skipped`, `ignored` — converted to numeric internally. |
| `language` | no | none | Indexer language override. |
| `show_dir` | no | none | Custom series folder name override. |
| `scene` | no | `false` | |
| `paused` | no | `false` | |

```json
{ "tvdb_id": 370761, "root_dir": "/media/videos/Anime" }
```

### `set_episode_status`

Sets episode statuses for a series via `POST /api/v2/internal/updateEpisodeStatus`. Use when TVDB metadata is out of sync and Medusa is not downloading episodes that should be available.

Arguments:

| Argument | Required | Default | Notes |
| --- | --- | --- | --- |
| `series_slug` | yes | none | Series slug (e.g. `tvdb370761`). |
| `episodes` | yes | none | List of episode slugs (e.g. `["s01e01", "s01e02"]`). |
| `status` | no | `wanted` | Target status: `wanted`, `skipped`, `ignored`. |

```json
{ "series_slug": "tvdb370761", "episodes": ["s01e01", "s01e02"], "status": "wanted" }
```

### `bulk_add_anime`

Dry-runs or executes multiple anime adds through `POST /api/v2/anime/bulk-add`. Writes require `execute: true`.

```json
{
  "items": [{"animeId": 62076, "displayTitle": "Example Title", "aiDecision": "add"}],
  "decision_field": "aiDecision",
  "allowed_decisions": ["add"],
  "execute": false
}
```

## Configuration

Environment variables:

| Variable | Required | Description |
| --- | --- | --- |
| `MEDUSA_URL` | yes | Base URL for Medusa, for example `http://medusa:8081` |
| `MEDUSA_API_KEY` | recommended | Medusa API key sent as `X-Api-Key` |
| `MEDUSA_WEB_ROOT` | no | Web root if Medusa is hosted below a path |
| `MEDUSA_TIMEOUT` | no | HTTP timeout in seconds, default `30` |
| `MCP_TRANSPORT` | no | `stdio` or `sse`, default `stdio` |
| `MCP_HOST` | no | SSE bind host, default `0.0.0.0` |
| `MCP_PORT` | no | SSE bind port, default `8000` |
| `MCP_DNS_REBINDING_PROTECTION` | no | Enable MCP SDK Host/Origin validation, default `false` |
| `MCP_ALLOWED_HOSTS` | no | Comma-separated allowed `Host` headers when DNS rebinding protection is enabled, for example `daniel-nas.localdomain:3001,localhost:3001` |
| `MCP_ALLOWED_ORIGINS` | no | Comma-separated allowed `Origin` headers when DNS rebinding protection is enabled |

For container/SSE deployments, keep `MCP_HOST=0.0.0.0` or set it explicitly. DNS rebinding protection is disabled by default because mapped ports, reverse proxies, and LAN hostnames otherwise commonly cause `421 Misdirected Request` errors. If you enable it, include the externally visible host and port in `MCP_ALLOWED_HOSTS`.

## Run locally

```bash
pip install -e .
MEDUSA_URL=http://localhost:8081 MEDUSA_API_KEY=... mcp-medusa
```

For SSE:

```bash
MEDUSA_URL=http://localhost:8081 MEDUSA_API_KEY=... mcp-medusa --transport sse --port 8000
```

## Docker

The image defaults to SSE on port `8000`.

```bash
docker build -t mcp-medusa .
docker run --rm -p 8000:8000 \
  -e MEDUSA_URL=http://medusa:8081 \
  -e MEDUSA_API_KEY=... \
  mcp-medusa
```

For stdio instead:

```bash
docker run --rm -i \
  -e MEDUSA_URL=http://medusa:8081 \
  -e MEDUSA_API_KEY=... \
  mcp-medusa --transport stdio
```
