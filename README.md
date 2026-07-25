# Naver SmartStore Scraper API

A scalable REST API that returns the `__PRELOADED_STATE__` JSON of a Naver SmartStore
product page, bypassing Naver's anti-scraping (`nfront` WAF).

```
GET /naver?productUrl=https://smartstore.naver.com/{store}/products/{id}
GET /naver?store={store}&id={id}
```

> Background on how the protection was defeated (why the given proxy is dead, why a
> pure-JS scraper doesn't work, why the engine runs on Apify) is in **[notes.md](notes.md)**.

---

## Architecture

Two parts in one repo:

```
naver-scraper-api/
├── src/                     ← API worker (TypeScript / Express), exposed via a public tunnel
│   ├── server.ts            ← HTTP endpoints
│   └── apifyClient.ts       ← runs the scraper actor per request
├── library/
│   └── naver_scraper/       ← the scraper engine (Python), packaged as an Apify actor
│       ├── src/
│       │   ├── engine.py    ← curl_cffi safari17_0 + residential-KR + IP racing + extraction
│       │   ├── main.py      ← Apify input/output glue
│       │   └── __main__.py
│       ├── .actor/          ← actor.json · input_schema.json · Dockerfile
│       └── requirements.txt
├── .env.example             ← copy to .env and fill in
├── package.json · tsconfig.json
├── README.md · notes.md
```

**Request flow** — the worker never scrapes directly; it invokes the actor per request, and
the actor **exits** when done (no always-on server, near-zero idle cost):

```
client ──HTTP──▶ API worker ──run-sync──▶ Apify actor (one-shot) ──▶ Naver
                     ▲                          │ curl_cffi safari17_0
                     └──── __PRELOADED_STATE__ ──┘ + residential-KR + IP racing
```

### Why the engine is Python and the API is TypeScript

No Node HTTP client can reliably send the TLS fingerprint Naver requires (details in
[notes.md](notes.md)). So the **API surface is TypeScript** and it delegates the single
fingerprinted fetch to a proven **`curl_cffi`** engine, packaged as an Apify actor in
`library/naver_scraper/`.

---

## Prerequisites

- **Node.js ≥ 20** (has global `fetch`)
- An **Apify account** and API token (From Me, check email, i send a .env file)

---

## Setup & run

Config lives in a `.env` file (loaded automatically via `dotenv`):

```bash
cd naver-scraper-api
cp .env.example .env         # set APIFY_TOKEN and APIFY_ACTOR_ID
npm install
npm run build
npm start                    # → "naver-scraper-api worker listening on :3000"
```

`.env` (see [.env.example](.env.example) for all options):
```dotenv
APIFY_TOKEN=apify_api_xxxxxxxx
APIFY_ACTOR_ID=owner/naver-scraper-actor
PORT=3000
```

Test (live instance — host provided separately by email):
```bash
curl "http://<API_HOST>:8083/health"
curl "http://<API_HOST>:8083/naver?productUrl=https://smartstore.naver.com/sajansaja/products/12104664129"
```

---

## API reference

| Method | Path | Description |
|---|---|---|
| GET | `/naver?productUrl=...` | Scrape by full product URL |
| GET | `/naver?store=..&id=..` | Scrape by store slug + product id |
| GET | `/health` | Liveness: `{ ok, ts }` |
| GET | `/` | Service info |

**200 OK**
```json
{
  "ok": true,
  "productUrl": "https://smartstore.naver.com/sajansaja/products/12104664129",
  "store": "sajansaja",
  "productId": "12104664129",
  "attempts": 3,
  "elapsedMs": 1536,
  "state": { "...": "raw __PRELOADED_STATE__ (110 top-level keys)" }
}
```
The product record lives at `state.simpleProductForDetailPage.A`
(`name`, `salePrice`, `stockQuantity`, …).

**502** — scrape failed after all retries (`{ ok:false, error, attempts, elapsedMs }`).
**400** — missing/invalid product URL.

---

## Configuration (env)

### API worker (`src/`)
| Var | Default | Meaning |
|---|---|---|
| `APIFY_TOKEN` | — | **Required.** Token used to run the actor |
| `APIFY_ACTOR_ID` | `xtracto/naver-scraper-actor` | `owner/actor` to invoke |
| `APIFY_RUN_TIMEOUT_SECS` | `60` | Max wait per actor run |
| `PORT` | `3000` | HTTP port |
| `MAX_CONCURRENCY` | `10` | Max simultaneous actor runs |

### Scraper actor (`library/naver_scraper/`)
| Var | Default | Meaning |
|---|---|---|
| `NAVER_TLS_PROFILE` | `safari17_0` | curl_cffi impersonation profile |
| `NAVER_RACE` | `3` | Concurrent fresh-IP attempts per wave |
| `NAVER_MAX_ATTEMPTS` | `6` | Total attempts before giving up |
| `NAVER_PROXY_COUNTRY` | `KR` | Residential exit country |
| `NAVER_TIMEOUT` | `20` | Per-request timeout (s) |
| `PROXY_URL` | — | External proxy for local dev (used only if `APIFY_PROXY_PASSWORD` is unset) |

---

## Performance

The scrape itself is **~1–2 s** (IP racing). On the Apify free plan, per-run orchestration
adds ~7–8 s, so end-to-end is ~8–10 s/request. A paid Apify plan (faster container starts)
or running the engine locally with an external KR proxy (`PROXY_URL`) brings this under 2 s