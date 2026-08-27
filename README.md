# Solana Runner Tracker

Two reports a day: what ran, why it ran, and what is starting to form.
Automatic filtering of manufactured volume and rug structures.

No coding knowledge is needed to run it. This document walks through everything.

---

## 1. What it produces

At 08:00 and 22:00 CET a web page is regenerated automatically:

| Section | What it contains |
|---|---|
| **Today's runners** | 12 ranked tokens with volume, liquidity, age, tracked-wallet confluence and a trust badge |
| **Where the volume is going** | Runner volume split by theme — the picture of what is pulling money |
| **What's forming** | Narratives absent on previous days. This is the section with real value: everyone already sees the rest |
| **Metas ranked by the market** | Dexscreener's narrative ranking, sorted by 6h acceleration |
| **Cut — and why** | Tokens that are climbing but that the filter refuses, with the exact reason |
| **Post drafts** | Three texts built from this run's numbers, to rewrite in your own voice |
| **Model performance** | What previous selections actually did at 24h, including when it is bad |

The page is public and has a fixed URL. It belongs in your X bio.

---

## 2. How the filtering works

### Gates (instant rejection)

A token is cut without discussion if mint or freeze authority is still active,
liquidity is under $25,000, the pair is younger than 3 hours, 24h volume is under
$100,000, or there are fewer than 250 transactions.

The 3-hour floor is deliberate: it gives instant rugs time to reveal themselves
before anything gets published.

### Manufactured-volume detection

Seven signals, scored 0-100. None is damning alone — what matters is how many
stack up. A real runner trips zero or one; an inflated chart trips four or more.

| Signal | What it catches |
|---|---|
| Extreme volume / liquidity | A $50k pool cannot absorb $5M in 24h without breaking price |
| Heavy volume, flat price | Buy and sell in a loop: volume climbs, price does not move |
| Perfect buy/sell symmetry | Bot round-trips produce a balance that is too clean |
| Micro-trades at scale | Transaction-count spam to inflate the counter |
| Constant cadence | A real run comes in bursts; a bot is a metronome |
| Uniform trade size | Identical average size at 1h and 24h means a hardcoded amount |
| Starved liquidity vs mcap | The chart is decorative — you will not be able to exit |

### Tracked-wallet confluence

This is the hardest signal to fake. Volume is manufacturable; the composition of
an address book is not. If seven wallets you have followed for months buy the
same mint inside the same window, that outweighs any volume metric.

This module needs a Helius key (see §5). Everything else works without it.

### The score

Five components — momentum, volume, liquidity, social footprint, freshness —
weighted, multiplied by the confluence bonus, then discounted by suspected fake
volume and structural risk.

### Calibration

Every run archives its selections with the price at call time. The next run picks
up the ones from 24h earlier and measures what they did. After 30 observations
the system shifts its weights in small steps toward the components that actually
predicted performance.

It is slow and deliberately capped: the model drifts toward the current market
regime rather than overreacting to one unusual day. Everything is readable in
`data/calibration.json` — nothing hidden in a black box.

---

## 3. Setup (30 minutes, no code)

The system runs on **GitHub Actions**: GitHub's servers execute the script twice
a day, for free. Your own computer can stay switched off.

### Step 1 — Create an account

Go to [github.com](https://github.com) and create a free account.

### Step 2 — Create the repository

Click **+** in the top right → **New repository**.

- Name: `runners-tracker`
- Tick **Public** (required for the free public page)
- Tick nothing else, click **Create repository**

### Step 3 — Upload the files

On the empty repository page, click **uploading an existing file**.
Unzip the archive, then drag **all of its contents** into the drop zone. Wait for
the upload to finish, then click **Commit changes**.

> The `.github` folder is hidden by default on some systems. On Mac press
> `Cmd + Shift + .` in Finder; on Windows, View tab → tick "Hidden items".
> Without that folder nothing will ever trigger.

### Step 4 — Allow write access

**Settings** tab → **Actions** → **General** in the left menu.
Scroll to **Workflow permissions**, select **Read and write permissions**, click
**Save**.

### Step 5 — Turn on the public page

**Settings** tab → **Pages** in the left menu.
Under **Source**, choose **GitHub Actions**.

Your URL will be `https://YOUR-USERNAME.github.io/runners-tracker/`.

### Step 6 — First run

**Actions** tab → click **Solana Runner Tracker** in the left column →
**Run workflow** button → **Run workflow**.

Give it two to four minutes. A green tick means it worked. Then open your GitHub
Pages URL.

From then on it runs itself at 08:00 and 22:00 CET.

> **Daylight saving.** The schedule is in UTC. At the end of October, open
> `.github/workflows/tracker.yml`, click the pencil icon, and change `0 6` to
> `0 7` and `0 20` to `0 21`. That is the only maintenance in the year.

---

## 4. Adding your wallets

Prepare a `kol_wallets.csv` file in this shape:

```csv
address,label,tier,weight
7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU,Cented,S,3
9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM,Euris,A,2
```

Only `address` is required. `tier` accepts S / A / B (weights 3 / 2 / 1.5);
`weight` lets you force a value. A wallet you know actually moves price deserves
a high weight.

Drop the file into the `config/` folder of your repository (open the folder, then
**Add file** → **Upload files**). **Name it exactly `kol_wallets.csv`**, with no
`.example`.

Once you have confirmed the confluence returns sensible results, open
`config/settings.yaml` and set `require_kol` to `true`: from then on nothing
enters the runners without validation from your wallets. That single setting is
what turns this from a generic scanner into your personal filter.

---

## 5. API budget

Three of the four sources are free and need no signup. Only wallet confluence
costs money.

| Tier | Cost | What you get | What is missing |
|---|---|---|---|
| **Free** | $0 | Dexscreener, GeckoTerminal, RugCheck. Runners, narratives, metas, anti-wash, anti-rug, calibration | No wallet tracking. Your main edge stays unused |
| **Helius Free** | $0 | ~100 wallets, twice a day | 1M credits/month: past ~100 wallets you burn the quota in three weeks |
| **Helius Developer** | $49/mo | All 400 wallets, twice a day, with headroom | Nothing for this use case. This is the right tier |
| **+ X/Twitter API Basic** | +$100/mo | Automatic reading of posts attached to tokens: real engagement, who is talking, follower-bot detection | Useful later, when you want to score the quality of a community rather than just its existence |

**Recommendation: start at $0.** Launch it, let it run a week, then look at the
"Model performance" section. You will know whether the tracker earns its keep
before spending anything.

Move to Helius Developer once the free version is running and you want the real
edge: cross-referencing against your 400 wallets. That cross-reference is what
separates you from the dozens of accounts reposting the Dexscreener leaderboard.

To add the key: [helius.dev](https://helius.dev) → create an account → copy the
API key. Then in your repository: **Settings** → **Secrets and variables** →
**Actions** → **New repository secret**. Name: `HELIUS_API_KEY`. Value: your key.

Never paste a key into a file in the repository — it is public.

---

## 6. Tuning

Everything lives in `config/settings.yaml`, one comment per line. To change a
value: click the file on GitHub, click the pencil, edit, **Commit changes**.

The two settings that change the character of the tracker:

- `min_liquidity_usd` — raise to 50000 to only surface tokens you could actually
  take a position in
- `wash_reject` — lower to 40 to be harsher on volume, raise to 70 to let more
  candidates through

---

## 7. What this system does not do

Read this before building an audience on it.

- **It does not predict.** It describes what happened and what is forming. The
  performance section exists so you can see honestly what its selections are
  worth, not to sell you a hit rate.
- **It does not catch every rug.** A token can clear the structural checks and
  still collapse: a team selling, a paid influencer, a narrative dying. The
  filters cut mechanical fraud, not bad intentions.
- **A competent wash-trader gets through.** The seven signals catch lazy bots,
  which are the majority. Someone who varies amounts, intervals and buy/sell
  imbalance stays invisible from public aggregated data.
- **This is not investment advice.** It is a market-data analysis tool. Your
  audience will make financial decisions based on what you publish: the "Cut —
  and why" section and the performance section are there to keep you honest with
  them. Long term, that is also what builds an audience that stays.

---

## 8. Running locally (optional)

```bash
pip install -r requirements.txt
python src/demo.py       # demo report, no network calls
python src/selftest.py   # 33 pipeline checks
python src/main.py --run morning
```

The report is written to `out/index.html`.

## Layout

```
config/settings.yaml       thresholds and weights
config/kol_wallets.csv     your tracked wallets (to add)
src/sources.py             API clients, rate limiting, retries
src/filters.py             wash-trading and structural-risk detection
src/scoring.py             composite score
src/kol.py                 wallet confluence
src/narrative.py           themes, metas, emergence detection
src/learn.py               calibration against realised outcomes
src/report.py              HTML rendering and post drafts
data/calibration.json      current weights and performance history
out/index.html             the published report
```

---

*Une version française du guide d'installation se trouve dans `INSTALL-FR.md`.*
