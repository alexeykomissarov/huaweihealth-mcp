# Huawei Health MCP

[![CI](https://github.com/alexeykomissarov/huaweihealth-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/alexeykomissarov/huaweihealth-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server for **Huawei Health** data:
smart-scale body composition (Huawei Scale), daily activity, sleep, heart rate, stress and workouts.

It reads the **Huawei Health app's own cloud** — the same place the app on your phone syncs your watch, band and
scale to — so it works with a regular HUAWEI ID and needs no Health Kit developer approval.

> **Unofficial.** The protocol was reverse-engineered from the Huawei Health Android app by
> [and7ey/huawei_health](https://github.com/and7ey/huawei_health) (MIT, see [NOTICE](NOTICE)). It may violate
> Huawei's terms of service and can break with an app update. Not affiliated with or endorsed by Huawei.

## Features

- **Body composition from Huawei smart scales** (checked with Scale 3 Pro): weight, BMI, body fat % and kg,
  fat-free mass, skeletal muscle, water, protein, bone mass, visceral fat, BMR, body age and score, waist-hip ratio,
  heart rate; optionally per-segment fat and muscle (arms, legs, trunk) and raw impedances; change over the period.
- **Family scales**: measurements carry no user id, so the owner's weigh-ins are picked by the height in the scale
  profile (`HUAWEI_OWNER_HEIGHT`); other people's and unrecognised weigh-ins are hidden unless asked for.
- **Read-only**, compact model-friendly JSON, **tool allowlist** (`HUAWEI_ENABLED_TOOLS`) to keep the context small.
- **No stored password**: you sign in to HUAWEI ID in your own browser; the server only gets a one-time code and
  keeps the token pair (file mode `0600`), rotating it automatically. A browser re-login is needed every 180 days.
- Streamable HTTP transport (`/mcp`), stateless, `/healthz` for container health checks; small non-root Docker image.

## Tools

| Tool | What it returns |
|---|---|
| `get_body_composition(start_date, end_date, detailed=False, all_people=False)` | Smart-scale measurements and the trend over the period (up to 366 days) |
| `get_daily_activity(start_date, end_date)` | Steps, distance, calories, active minutes and hours, intensity minutes per day |
| `get_daily_health(start_date, end_date, metrics=None)` | `sleep` (phases, score, HRV, SpO2), `heart_rate` (resting/min/max), `stress`, `intensity` per day |
| `get_workouts(start_date, end_date, min_minutes=3)` | Workouts: start/end, duration, distance, steps, calories (totals only, up to 62 days) |
| `get_devices()` | Devices bound to the account |
| `get_login_status()` | Whether the login is valid and days left until a browser re-login |

Dates are `YYYY-MM-DD` in the account's time zone (`HUAWEI_TZ`).

## Quick start (Docker)

```bash
docker build -t huaweihealth-mcp https://github.com/alexeykomissarov/huaweihealth-mcp.git#v0.1.0
docker volume create huawei-data
docker run --rm -it -v huawei-data:/data --entrypoint huawei-mcp-login huaweihealth-mcp --region eu
```

The login command prints a HUAWEI ID sign-in link. The page finishes by redirecting to `hms://redirect_url?code=…`,
which only the phone app can open, so the code is taken from the browser console:

1. Open the link in Chrome (or another Chromium browser) on a computer and sign in.
2. Turn on device emulation — F12, then Ctrl+Shift+M (macOS: Cmd+Shift+M), pick any phone — and **reload the page**:
   the "Allow" button only reacts to touch events.
3. Press "Allow". The DevTools console shows `Failed to launch 'hms://redirect_url?code=…'` — copy that whole line
   and paste it into the login prompt. The code is single-use and expires in a few minutes.

Then run the server:

```bash
docker run -d --name huaweihealth-mcp --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v huawei-data:/data --read-only --tmpfs /tmp --cap-drop ALL \
  -e HUAWEI_REGION=eu -e HUAWEI_TZ=Europe/Berlin -e HUAWEI_OWNER_HEIGHT=180 \
  huaweihealth-mcp
```

and connect a client to `http://127.0.0.1:8000/mcp`, for example Claude Code:

```bash
claude mcp add --transport http huawei-health http://127.0.0.1:8000/mcp
```

Check the login at any time with `huawei-mcp-login --verify` (exit code 0 = valid).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HUAWEI_REGION` | `ru` | Cloud of your HUAWEI ID: `ru`, `eu` or `cn` (used by `huawei-mcp-login`) |
| `HUAWEI_TZ` | `Europe/Moscow` | Time zone for dates and times |
| `HUAWEI_OWNER_HEIGHT` | — | Your height in cm as set in the scale profile; filters out other family members' weigh-ins |
| `HUAWEI_ENABLED_TOOLS` | all | Comma-separated allowlist of tools to publish, e.g. `get_body_composition`. An unknown name stops the server at startup |
| `HUAWEI_TOKEN_FILE` | `/data/tokens.json` | Token pair written by `huawei-mcp-login` |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Listen address of the Streamable HTTP server |

## Notes and limitations

- The `ru` cloud is tested end to end; the `eu` and `cn` hosts come from upstream projects.
- The token pair is shared with the session the cloud issued for this login; if the phone app rotates it,
  the server rotates and retries automatically.
- Time-range queries are split into the 10-day windows the cloud accepts, so long ranges take several requests.
- Workouts are rebuilt from per-minute segments: totals only, no heart-rate curve or laps.
- Measurements without `full_analysis` were taken without the hand electrodes (weight only).

## Security

The server itself has **no authentication**: anyone who can reach the port can read your health data.
Keep it on `127.0.0.1` or a private network, or put it behind an authenticating reverse proxy
(for example an OAuth 2.1 proxy such as [mcp-auth-proxy](https://github.com/sigbit/mcp-auth-proxy)).
The token file grants access to your Huawei Health data — keep it private.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen
uv run pytest -q                 # or: docker build --target test .
```

Tests run without network access; fixtures mirror the cloud's response format and all values are synthetic.
Code comments and server messages are in Russian; tool descriptions (what the model sees) are in English.

## License

[MIT](LICENSE). The protocol is ported from [and7ey/huawei_health](https://github.com/and7ey/huawei_health)
(MIT) — see [NOTICE](NOTICE) and [LICENSE-and7ey-huawei_health](LICENSE-and7ey-huawei_health).
