import json
import os
import sys

from curl_cffi import requests as cr

from src.engine import scrape

API = os.environ.get("APIFY_API_BASE_URL", "https://api.apify.com").rstrip("/")
TOKEN = os.environ.get("APIFY_TOKEN", "")
KVS = os.environ.get("APIFY_DEFAULT_KEY_VALUE_STORE_ID", "")
DATASET = os.environ.get("APIFY_DEFAULT_DATASET_ID", "")
INPUT_KEY = os.environ.get("APIFY_INPUT_KEY", "INPUT")


def get_input() -> dict:
    if len(sys.argv) > 1:
        return {"productUrl": sys.argv[1]}
    if os.environ.get("PRODUCT_URL"):
        return {"productUrl": os.environ["PRODUCT_URL"]}
    if KVS:
        suffix = f"?token={TOKEN}" if TOKEN else ""
        u = f"{API}/v2/key-value-stores/{KVS}/records/{INPUT_KEY}{suffix}"
        try:
            r = cr.get(u, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return {}


def push(item: dict) -> None:
    if TOKEN and DATASET:
        cr.post(f"{API}/v2/datasets/{DATASET}/items?token={TOKEN}", json=item, timeout=60)


def main() -> int:
    inp = get_input()
    product_url = (inp or {}).get("productUrl") or ""
    if not product_url:
        result = {"ok": False, "error": "missing 'productUrl' in input"}
    else:
        result = scrape(product_url)

    push(result)

    log = {k: v for k, v in result.items() if k != "state"}
    log["hasState"] = "state" in result
    print("RESULT " + json.dumps(log, ensure_ascii=False))
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
