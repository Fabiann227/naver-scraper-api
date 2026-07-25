import 'dotenv/config';
import express, { Request, Response } from 'express';
import { scrapeViaActor, ScrapeResult } from './apifyClient';

const PORT = Number(process.env.PORT || 3000);
const MAX_CONCURRENCY = Number(process.env.MAX_CONCURRENCY || 10);

const STORE_URL = 'https://smartstore.naver.com';
const PRODUCT_RE = /smartstore\.naver\.com\/[^/?#]+\/products\/\d+/;

class Semaphore {
  private active = 0;
  private queue: Array<() => void> = [];
  constructor(private readonly max: number) {}
  async run<T>(fn: () => Promise<T>): Promise<T> {
    if (this.active >= this.max) await new Promise<void>((r) => this.queue.push(r));
    this.active++;
    try {
      return await fn();
    } finally {
      this.active--;
      this.queue.shift()?.();
    }
  }
}
const sem = new Semaphore(MAX_CONCURRENCY);

function resolveUrl(q: Request['query']): string | null {
  const productUrl = typeof q.productUrl === 'string' ? q.productUrl : undefined;
  if (productUrl && PRODUCT_RE.test(productUrl)) return productUrl;
  const store = typeof q.store === 'string' ? q.store : undefined;
  const id =
    typeof q.id === 'string' ? q.id : typeof q.productId === 'string' ? q.productId : undefined;
  if (store && id) return `${STORE_URL}/${store}/products/${id}`;
  return null;
}

async function handleNaver(req: Request, res: Response) {
  const url = resolveUrl(req.query);
  if (!url) {
    return res.status(400).json({
      ok: false,
      error:
        'Provide ?productUrl=https://smartstore.naver.com/<store>/products/<id>  (or ?store=<store>&id=<id>)',
    });
  }
  const result: ScrapeResult = await sem.run(() => scrapeViaActor(url));
  res.status(result.ok ? 200 : 502).json(result);
}

const app = express();
app.disable('x-powered-by');
app.get('/', (_req, res) =>
  res.json({
    service: 'naver-smartstore-scraper-api',
    usage: 'GET /naver?productUrl=https://smartstore.naver.com/<store>/products/<id>',
    health: 'GET /health',
  }),
);
app.get('/health', (_req, res) => res.json({ ok: true, ts: Date.now() }));
app.get('/naver', handleNaver);

app.listen(PORT, () => {
  console.log(`naver-scraper-api worker listening on :${PORT} (concurrency=${MAX_CONCURRENCY})`);
});
