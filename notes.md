# Engineering notes — Naver SmartStore scraper

How the target is protected, what did and didn't work, and why the final architecture looks
the way it does.

---

## TL;DR

Naver SmartStore is fronted by Naver's own WAF (`server: nfront`) with three independent
gates: a **TLS/JA3 fingerprint** check, an **IP-reputation** check, and an **nCaptcha**
challenge. The combination that gets through is specific:

> **`curl_cffi` with the `safari17_0` fingerprint + a Korean residential IP + rotate the IP
> and retry on the captcha wall.**

Each section below explains why each piece is necessary, and why the obvious alternatives
(a JavaScript HTTP client; the proxy from the brief; a persistent server) do not work.

---

## 1. The target's protection

| Gate | Observed behaviour | Notes |
|---|---|---|
| **TLS / JA3** | Desktop Chrome/Edge/Firefox fingerprints (real `curl`, `curl_cffi chrome131`, `impit`, `node-tls-client`) get **HTTP `490`** — a Naver-specific block with a tiny 583-byte body. | Naver blocklists the *fingerprints of common automation tools*, not just "non-browsers". |
| **IP reputation** | From a datacenter IP, even a passing fingerprint gets an **nCaptcha security wall** (`ncaptcha-api.js` + a login page) or `429`, on *every* path including the store homepage. | Requires a **residential** IP — Korean works best. |
| **nCaptcha** | Even from a real Korean residential IP, ~25% of requests still hit the captcha wall (the "receipt" challenge). | Beaten by **rotating the exit IP and retrying**. |
| **Anti-DevTools** | Opening Chrome DevTools on the page triggers a redirect to the `현재 서비스 접속이 불가합니다` (429) error page. | Makes manual API capture painful — a good reason to go HTTP-only, no browser. |

The target data is server-side-rendered into the page as a global JS variable,
`window.__PRELOADED_STATE__`.

---

## 2. Why the proxy in the brief is dead

The brief provided:

```
6n8xhsmh.as.thordata.net:9999 : td-customer-mrscraperTrial-country-kr : P3nNRQ8C2
```

The proxy **tunnel connects fine** (TCP `CONNECT` returns `200 Connection established`), but
the moment any request goes through it, the thordata exit returns:

```
HTTP/1.1 403 Forbidden
x-thor-error-code: Auth_303
x-thor-error: Credential Parameter Error
x-thor-error-msg: Credential verification failed. Please check your account and password…
```

Every plausible username format was tested — `…-country-kr`, `…-countrykr`, `…-country-KR`,
`…-region-kr`, and the bare `td-customer-mrscraperTrial` with no country — **all return the
same `Auth_303`**. Because even the base account (without any country parameter) fails
authentication, the problem is the **credential pair itself**, not the format: the trial
account/password is expired or invalid at thordata's side, which cannot be fixed client-side.

**Why this wasn't a blocker:** the brief explicitly allows other proxy providers ("free to
search for free or trial proxy providers"), so the solution uses Apify's proxy instead (§4).

---

## 3. Why a pure-JavaScript scraper does not work

Keeping the fetch in Node was the goal, since the challenge prefers JavaScript. It fails at
the **TLS layer**, before any HTTP is even sent:

- **Native Node `fetch`/undici** sends Node's own TLS ClientHello → Naver `490`.
- **`impit`** (Rust-based browser-impersonation fetch for Node) only offers *desktop* Chrome
  and Firefox fingerprints → both get `490`.
- **`node-tls-client`** (Go `tls-client` bindings, can mimic Safari/Chrome JA3s): it downloads
  its native TLS library at runtime and, inside the Apify container, its worker thread
  **crashes** (`Piscina` worker error) before a request completes — fragile and not viable for
  a "1000 requests / 1 hour stable" requirement.

The reason is subtle but important: Naver's `490` is not "block all non-browsers" — it is a
**blocklist of specific known fingerprints**, and the fingerprints the popular Node libraries
emit are on it. `curl_cffi`'s `safari17_0` profile produces a JA3 that Naver still accepts, and
no Node library reproduces that exact profile reliably.

**Conclusion:** keep the public API in TypeScript, and delegate the one fingerprinted HTTP call
to a small `curl_cffi` (Python) engine.

> It is the **fingerprint + User-Agent pair** that matters, not the UA alone: `safari17_0` with
> its matching Safari UA passes; the same fingerprint with a desktop-Chrome UA is blocked `490`.

---

## 4. Why the engine runs on Apify

A Korean residential proxy is required. The brief's proxy is dead, so the engine uses **Apify
Proxy** (the account has a `RESIDENTIAL` group on the free tier). There is one catch:

- **Apify's proxy only works when the code runs *on the Apify platform*.** On the free plan,
  "Proxy external access" is **disabled** — calling `proxy.apify.com` from a local machine
  returns `403 "Proxy external access feature isn't enabled"`. So running the scraper locally
  and pointing it at Apify's proxy is not possible on this tier.
- The fix is to run the scraper **as an Apify actor**, where the residential proxy is available
  automatically (`APIFY_PROXY_PASSWORD` is injected into the run).

That provides a working Korean residential exit for free, at the cost of the scraper living on
Apify. To keep it **cheap**, the actor is a **one-shot task**, not a persistent server: the API
worker invokes it per request (`run-sync-get-dataset-items`); the actor starts, scrapes, returns
the data, and **exits**. Billing is only for the few seconds of each run — an always-on
("Standby") actor would bill continuously.

```
API worker  ──run-sync──▶  Apify actor (one-shot, residential-KR)  ──▶ Naver
```

---

## 5. The winning recipe + measurements

- Engine: `curl_cffi` **`safari17_0`**.
- Proxy: Apify **`RESIDENTIAL, country-KR`**, a **fresh rotating session per attempt**.
- **IP racing:** fire **3 fresh-IP attempts concurrently** per wave, take the first success,
  up to 6 total. (~75% success per IP → a wave of 3 succeeds ~98%, which kills the retry
  latency tail.)
- Extraction: slice `window.__PRELOADED_STATE__` with a brace-matcher, then **sanitize** it —
  it is a *JS object literal*, not strict JSON (it contains `undefined`), so `undefined` /
  `NaN` / `Infinity` are replaced with `null` **outside string values** before parsing.

Measured on the Apify platform (real Korea Telecom exit IPs), 3 different stores:

| Product | Scrape time (engine) | Result |
|---|---|---|
| sajansaja/12104664129 | **1.54 s** | ✅ 110 state keys |
| rainbows9030/11102379008 | **1.04 s** | ✅ 110 state keys |
| minibeans/8768399445 | **1.26 s** | ✅ 110 state keys |

Sanity-check against the live page — matches exactly:
`name: 아이폰 6S（그레이） 16GB iPhone 6s 자급제폰 무음카메라 순정폰`, `salePrice: 133000`,
`stockQuantity: 114`.

Fingerprint sweep that established the recipe (residential-KR, 12 fresh IPs each):

| Profile | Outcome |
|---|---|
| `chrome131` | 12 / 12 → `490` (blocked) |
| `chrome99_android` | 11 / 12 → captcha |
| **`safari17_0`** | **9 / 12 → real `__PRELOADED_STATE__`** |

---

## 6. Where the product data is

`__PRELOADED_STATE__` has ~110 top-level keys (Redux slices). The main product record:

```
state.simpleProductForDetailPage.A
  → { productNo, name, salePrice, dispSalePrice, stockQuantity, … }
```

Other useful slices: `productBenefit`, `productDelivery`, `productReviewSummary`,
`relationProducts`. The API returns the **whole** `state` object (raw), so any field is
available downstream.

---

## 7. Latency - how to get under 6 s

The scrape is ~1–2 s, but Apify's free-plan per-run orchestration adds ~7–8 s, so end-to-end is
~8–10 s — over the 6 s target. Two ways to close the gap, when needed:

1. **Paid Apify plan** — faster container starts drop the overhead substantially, same code.
2. **Run the engine locally** (in the API worker) with an **external** Korean residential proxy
   — removes Apify from the hot path. The engine already supports this: set
   `PROXY_URL=http://user:pass@kr-gateway:port` and call it directly (≈1–2 s total). This needs
   one working external residential-KR proxy (a Webshare / IPRoyal / Bright Data trial, or a
   renewed thordata credential).

The scraping logic is identical in both cases; only *where it runs* changes.