export interface ScrapeResult {
  ok: boolean;
  productUrl: string;
  store?: string;
  productId?: string;
  attempts?: number;
  elapsedMs?: number;
  state?: unknown;
  error?: string;
}

const API_BASE = process.env.APIFY_API_BASE_URL || 'https://api.apify.com';
const ACTOR_ID = process.env.APIFY_ACTOR_ID || 'xtracto/naver-scraper-actor';
const TOKEN = process.env.APIFY_TOKEN || '';
const RUN_TIMEOUT_SECS = Number(process.env.APIFY_RUN_TIMEOUT_SECS || 60);

export async function scrapeViaActor(productUrl: string): Promise<ScrapeResult> {
  if (!TOKEN) {
    return { ok: false, productUrl, error: 'APIFY_TOKEN is not set' };
  }
  const actorPath = ACTOR_ID.replace('/', '~');
  const url =
    `${API_BASE}/v2/acts/${actorPath}/run-sync-get-dataset-items` +
    `?token=${encodeURIComponent(TOKEN)}&timeout=${RUN_TIMEOUT_SECS}`;

  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), (RUN_TIMEOUT_SECS + 15) * 1000);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ productUrl }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const body = await resp.text();
      return { ok: false, productUrl, error: `run failed: HTTP ${resp.status} ${body.slice(0, 200)}` };
    }
    const items = (await resp.json()) as ScrapeResult[];
    if (!Array.isArray(items) || items.length === 0) {
      return { ok: false, productUrl, error: 'actor returned no dataset items' };
    }
    return items[0];
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, productUrl, error: `actor call error: ${msg}` };
  } finally {
    clearTimeout(t);
  }
}
