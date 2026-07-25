import json
import os
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cr

IMPERSONATE = os.environ.get("NAVER_TLS_PROFILE", "safari17_0")
MAX_ATTEMPTS = int(os.environ.get("NAVER_MAX_ATTEMPTS", "6"))

RACE = int(os.environ.get("NAVER_RACE", "3"))
PROXY_COUNTRY = os.environ.get("NAVER_PROXY_COUNTRY", "KR")
TIMEOUT = int(os.environ.get("NAVER_TIMEOUT", "20"))

PROXY_HOST = os.environ.get("APIFY_PROXY_HOSTNAME", "proxy.apify.com")
PROXY_PORT = os.environ.get("APIFY_PROXY_PORT", "8000")
PROXY_PW = os.environ.get("APIFY_PROXY_PASSWORD", "")
EXTERNAL_PROXY = os.environ.get("PROXY_URL", "")

URL_RE = re.compile(r"smartstore\.naver\.com/([^/?#]+)/products/(\d+)")
MARKER_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

_LITERALS = ("-Infinity", "Infinity", "undefined", "NaN")
_IDENT = re.compile(r"[A-Za-z0-9_$]")


def proxy_url():
    if PROXY_PW:
        sess = "s" + secrets.token_hex(6)
        return (f"http://groups-RESIDENTIAL,country-{PROXY_COUNTRY},session-{sess}"
                f":{PROXY_PW}@{PROXY_HOST}:{PROXY_PORT}")
    return EXTERNAL_PROXY or None


def _js_object_to_json(text: str) -> str:
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        matched = False
        for lit in _LITERALS:
            if text.startswith(lit, i):
                before = text[i - 1] if i > 0 else ""
                after = text[i + len(lit)] if i + len(lit) < n else ""
                if not _IDENT.match(before or "") and not _IDENT.match(after or ""):
                    out.append("null")
                    i += len(lit)
                    matched = True
                    break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def extract_state(html: str):
    m = MARKER_RE.search(html)
    if not m:
        return None
    start = html.index("{", m.end())
    depth, in_str, esc = 0, False, False
    for j in range(start, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = html[start:j + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return json.loads(_js_object_to_json(blob))
    return None


def is_wall(status: int, text: str) -> bool:
    return status in (429, 490) or ("ncaptcha" in text) or ("ncaptcha-api.js" in text)


def _one_attempt(url: str):
    p = proxy_url()
    proxies = {"http": p, "https": p} if p else None
    try:
        r = cr.Session(impersonate=IMPERSONATE, proxies=proxies,
                       timeout=TIMEOUT).get(url, headers=HEADERS)
    except Exception as e:
        return ("exc", f"{type(e).__name__}: {str(e)[:120]}")
    if "__PRELOADED_STATE__" in r.text:
        state = extract_state(r.text)
        if state is None:
            return ("parsefail", "marker present but state failed to parse")
        return ("ok", state)
    if is_wall(r.status_code, r.text):
        return ("wall", f"wall (status={r.status_code})")
    return ("other", f"unexpected status={r.status_code}")


def scrape(url: str) -> dict:
    m = URL_RE.search(url)
    if not m:
        return {"ok": False, "error": f"not a SmartStore product URL: {url}",
                "productUrl": url}
    store, product_id = m.group(1), m.group(2)
    t0 = time.time()
    last = "no attempts"
    fired = 0
    while fired < MAX_ATTEMPTS:
        wave = min(RACE, MAX_ATTEMPTS - fired)
        fired += wave
        with ThreadPoolExecutor(max_workers=wave) as ex:
            futures = [ex.submit(_one_attempt, url) for _ in range(wave)]
            for fut in as_completed(futures):
                kind, payload = fut.result()
                if kind == "ok":
                    return {"ok": True, "productUrl": url, "store": store,
                            "productId": product_id, "attempts": fired,
                            "elapsedMs": int((time.time() - t0) * 1000), "state": payload}
                last = payload
    return {"ok": False, "productUrl": url, "store": store, "productId": product_id,
            "attempts": fired, "elapsedMs": int((time.time() - t0) * 1000), "error": last}
