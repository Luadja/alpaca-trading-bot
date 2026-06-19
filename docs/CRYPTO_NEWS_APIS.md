# Free crypto-news APIs for trading indicators — research & recommendation

_Researched 2026-06-19 via a fan-out + adversarial-verification harness (5 search angles, 20 sources
fetched, 25 claims through 3-vote verification — 19 confirmed, 6 refuted). Confidence and vote counts
are noted per finding. The deciding lens: a news signal is only useful if it's **free AND backtestable**
(we can't validate a signal we can't replay), so "free history" is weighted above everything else._

## TL;DR

- **Primary: Alpaca News API (Benzinga).** ✅ Free, covers crypto, real-time + historical **back to 2015** —
  the one option that is both free and backtestable, and we already use `alpaca-py` with the same keys.
  The prior repo finding is **VERIFIED**.
- **Overlay: Crypto Fear & Greed Index.** ✅ Keyless, free, numeric 0–100 score, daily history to Feb 2018.
  A *market-wide* sentiment number (not per-coin news) — complements Alpaca, doesn't replace it.
- **Everything else fails the free-backtestable test** for our use: CryptoPanic (free tier removed
  2026-04-01 *and* no date-range history), Santiment (free = rolling 1yr **minus last 30 days**),
  CoinDesk Data (news is REST-poll only; free limit unconfirmed), LunarCrush / cryptonews-api (no
  confirmed free tier).

**Recommendation:** build the news-signal research on **Alpaca News** (per-coin, backtestable to ~2018)
with the **Fear & Greed Index** as a free market-wide overlay. Derive sentiment ourselves (neither ships
a numeric score on the free tier). Backtest first — same discipline as the price strategies.

## Comparison

| API | Free tier | Crypto coverage | Signal it gives | Free history (backtestable?) | Delivery | Fit |
|---|---|---|---|---|---|---|
| **Alpaca News (Benzinga)** | ✅ 200 calls/min (Basic); 10k/min (Algo Trader Plus) | ✅ stocks **and** crypto, one endpoint | Headlines/body teaser + symbol tags (**no numeric sentiment — derive it**) | ✅ **to 2015** (crypto dense only ~2018+) | REST + WebSocket | **★ Best — use this** |
| **Crypto Fear & Greed** (alternative.me) | ✅ keyless, ~60 req/min, commercial OK *with attribution* | ⚠️ market-wide, not per-coin | **0–100 numeric** score + 5 classes | ✅ **daily to Feb 2018** (`?limit=0`) | REST (daily) | **★ Overlay** |
| CoinDesk Data (ex-CryptoCompare) | ⚠️ free limit **unconfirmed** | ✅ crypto-native | News from 53–100+ publishers, sentiment/asset tags | ❓ unverified for free | **REST poll only** (no news websocket) | Maybe (verify free tier) |
| CryptoPanic | ❌ free Developer tier **removed 2026-04-01** | ✅ crypto-native | Votes + panic score | ❌ **no date-range history** (cursor feed only) | REST | ✗ paid + not backtestable |
| Santiment SanAPI | ⚠️ ~1k calls/mo | ✅ crypto-native | MVRV, social volume, dev activity | ❌ rolling **1yr − last 30 days** on the good metrics | REST (GraphQL) | ✗ not backtestable / 30-day lag |
| LunarCrush | ❓ no free tier confirmed (sources unreliable) | ✅ social | Galaxy Score, social volume | ❓ | REST | ✗ unverified / likely paid |
| NewsAPI.org / cryptonews-api / CoinGecko / Messari | mixed | general or market-data, weak per-coin news | varies | mostly ❌ free | REST | ✗ poor fit |

## Per-API notes (with citations)

### ✅ Alpaca News API (Benzinga) — primary, VERIFIED (high confidence, 3-0)
- One endpoint (`/v1beta1/news`) returns **both stock and crypto** news; source is **Benzinga**, ~130+
  articles/day. Real-time via REST **and** WebSocket (`wss://stream.data.alpaca.markets/v1beta1/news`),
  plus **historical back to 2015**. An apparent "2016 data missing" forum report was resolved *in
  Alpaca's favour* — it was a user pagination bug; data exists from 2015-01.
  [docs](https://docs.alpaca.markets/us/docs/historical-news-data) ·
  [blog](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/) ·
  [reference](https://docs.alpaca.markets/us/reference/news-3)
- **Free** with rate limits tied to the Market Data plan: **200 calls/min (Free/Basic)**, 10,000/min
  (Algo Trader Plus). Free historical access has **persisted to 2026** (still listed; the 2026 docs still
  cite "Algorithmic Trading using Sentiment Analysis on News"). _(2-1; dissent was citation hygiene.)_
- **Caveats (all 3-0):** (1) "back to 2015" is the *feed's* earliest date — per-coin crypto tagging is
  **thin pre-2018**, so a realistic crypto-news backtest is data-rich only from ~2018. (2) The payload has
  **no numeric sentiment field** — we compute sentiment ourselves (lexicon / FinBERT / an LLM pass).
  (3) Free access was announced as a **"limited-time beta"** with a standing warning of possible future
  pricing changes — it has held, but isn't contractually guaranteed; re-check periodically.
- **Refuted (0-3, don't rely on these):** the `alpaca-py` **README** does *not* itself document a
  `NewsClient` / the 2015 history / Benzinga — those come from the docs+blog. So **verify the exact SDK
  class names and the crypto symbol format (`BTC/USD` vs bare `BTC`) empirically** before building (same
  way we confirmed the `BTCUSD` position format). A separate AWS "Benzinga Basic" listing is a *different*
  product and does not describe Alpaca's tier.

### ✅ Crypto Fear & Greed Index — free overlay, VERIFIED (high, 3-0)
- `https://api.alternative.me/fng/` — **keyless**, HTTP 200 on a live test, JSON `{value:"14",
  value_classification:"Extreme Fear", timestamp:…}`. A single **0–100** score + 5 text classes; commercial
  use allowed **with attribution**; ~60 req/min.
- **History:** `?limit=0` returned **3,057 daily records back to 2018-02-01**, no auth — fully free and
  backtestable. **Limitations:** one **market-wide** number (not per-coin), **daily** granularity only.
  [api docs](https://alternative.me/crypto/api/) · [index](https://alternative.me/crypto/fear-and-greed-index/)

### ✗ CryptoPanic — fails twice (high, 3-0 structure / 2-1 date)
- Free **Developer** tier **discontinued, removed 2026-04-01**; now paid-only (Growth $50/wk, $199/mo).
  AND the news endpoints **never supported date-range/timestamp history** — only cursor pagination of the
  *current* feed (~50 pages). So it's real-time-feed-only and **not freely backtestable**.
  [plans](https://cryptopanic.com/developers/api/plans) · [about](https://cryptopanic.com/developers/api/about)

### ✗ Santiment SanAPI — fails on history (high, 3-0)
- Free tier restricts the valuable (paid) metrics to a **rolling 1-year window excluding the last 30 days**,
  ~1k calls/mo. So it's neither deeply backtestable (only ~1yr) nor usable for recent signals (30-day lag).
  Free "basic" metrics have no cutoff, but the trading-relevant ones (MVRV, social volume, on-chain) are
  the restricted ones. [restrictions](https://academy.santiment.net/sanapi/historical-and-realtime-data-restrictions)

### ⚠️ CoinDesk Data (ex-CryptoCompare/CCData) — maybe, but unverified (high on delivery, 3-0)
- Crypto news under REST `/data-api/news` (53–100+ publishers, sentiment/asset tags), but **no news
  channel on its websocket** (legacy ws is market-data only) — so news is **REST-poll only**. The free-tier
  limit is **unconfirmed**: the "250k one-time lifetime cap" claim was **refuted (0-3)**, so the real free
  allowance and whether news/sentiment are in-scope free remain open.
  [news docs](https://developers.coindesk.com/documentation/data-api/news) ·
  [legacy ws](https://developers.coindesk.com/documentation/legacy-websockets/HowToConnect)

### ✗ LunarCrush / cryptonews-api.com / NewsAPI.org / CoinGecko / Messari
- LunarCrush: social sentiment (Galaxy Score) but **no free tier confirmed** (sources flagged unreliable).
- cryptonews-api.com: AI sentiment per article but **paid** (free trial only).
- NewsAPI.org: general news, weak per-coin tagging, short free history, free = non-commercial.
- CoinGecko / Messari: primarily market-data, not a per-coin news/sentiment feed.

## What this means for the bot

1. **Build the news-signal research on Alpaca News** — free, per-coin, already in our SDK, backtestable
   from ~2018. First step (like the position-format check): empirically pull BTC/ETH/SOL news 2018→now,
   confirm the symbol-tag format and the real article density.
2. **Add the Fear & Greed Index as a free market-wide overlay** (a regime/panic feature), daily.
3. **Derive sentiment ourselves** — neither ships a numeric score free. Start with a cheap lexicon, then
   optionally an LLM/FinBERT pass.
4. **Backtest before wiring live.** A news signal that can't beat the price-only baseline (or can't survive
   a walk-forward like the breakout did *not*) shouldn't go live. News alpha for retail crypto is
   latency-disadvantaged and decays fast — measure it, don't assume it.

## Open questions / re-check later
- Alpaca crypto news **symbol format** (`BTC/USD` vs `BTC`) and real **pre-2018 density** — confirm empirically.
- Will Alpaca's **free News tier survive** a post-beta pricing change? (Standing "limited-time beta" warning.)
- CoinDesk Data's **actual free-tier limit / reset** and whether news+sentiment are free-tier in scope.
- Is the Fear & Greed Index's **daily, market-wide** nature too coarse for a swing signal, or fine as an overlay?

## Sources
Primary: [Alpaca historical-news docs](https://docs.alpaca.markets/us/docs/historical-news-data),
[Alpaca News blog](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/),
[Alpaca news reference](https://docs.alpaca.markets/us/reference/news-3),
[Fear & Greed API](https://alternative.me/crypto/api/),
[CryptoPanic plans](https://cryptopanic.com/developers/api/plans),
[Santiment restrictions](https://academy.santiment.net/sanapi/historical-and-realtime-data-restrictions),
[CoinDesk Data news](https://developers.coindesk.com/documentation/data-api/news).
