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
| `source` | no | `myanimelist` | `myanimelist`, `livechart` |
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
| `season` | no | current anime season | `WINTER`, `SPRING`, `SUMMER`, `FALL`. If omitted, calculated from the MCP server's current month. |
| `source` | no | `myanimelist` | `myanimelist`, `livechart` |
| `source_sort` | no | `anime_num_list_users` | `anime_num_list_users`, `anime_score` |
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
["animeId", "source", "displayTitle", "titleRomanji", "titleEnglish", "year", "season", "animeType", "status", "episodes", "episodeInfo", "genres", "studios", "score", "imageUrl", "anidbId", "tvdbId", "malId", "url", "directoryName"]
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
