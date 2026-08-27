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

### Holder structure and insiders

Label-only risk checks are not enough. A token can show 32% insider supply and
still clear a check that only reads risk labels — which is how a name like
$PENSION ends up on a board it has no business being on.

The full RugCheck report is parsed into numbers, and those numbers gate:

| Condition | Outcome |
|---|---|
| Insider supply ≥ 30% | Cut outright |
| Insider supply ≥ 15% with fewer than 2 tracked wallets buying | Cut |
| Top 10 holders ≥ 55%, or any single holder ≥ 25% | Cut |
| Under 150 holders | Cut |

The second row is the important one. Heavy concentration is not proof of fraud,
but it removes the benefit of the doubt: if the cap table is crowded and nobody
you follow is buying, that is the classic exit-liquidity setup.

### Tracked-wallet confluence

This is the hardest signal to fake. Volume is manufacturable; the composition of
an address book is not. If seven wallets you have followed for months buy the
same mint inside the same window, that outweighs any volume metric.

The tracker also records *when* each wallet bought, so a tight cluster (six
wallets inside eleven minutes) is distinguishable from six wallets drifting in
over a day. The first is a signal; the second is coincidence.

This module needs a Helius key (see §5). Everything else works without it.

### Why it ran — the causal layer

A theme label is not a reason. "Animals" describes what a token *is*, not what
happened to it. For every runner the tracker gathers the facts that could
actually explain a move:

- **Launch context** — pump.fun metadata, whether it graduated, whether it hit
  king-of-the-hill, and how many tokens the creator has launched before
- **Attention** — comment volume per hour, paid Dexscreener boosts, whether the
  socials are real or decorative
- **The wallet timeline** — who bought, how early, how tightly clustered
- **The shape of the move** — vertical spike vs sustained grind, and roughly
  when it started
- **Holder structure** — from the section above

Those facts are then turned into a specific claim: *"nine tracked wallets bought
inside a 14-minute window, four hours after the repo went public"*, with a
confidence score and the single risk most likely to kill it. With an Anthropic
key this is a language-model analysis; without one it falls back to a rule
engine that is blunter but never wrong about the facts.

When the evidence does not explain the move, the report says so. "Unexplained
move on thin liquidity" is a useful answer.

### The learning loop

Every session, twice a day, before anything new is judged:

1. **Re-price the last calls.** Market cap up = good data. Market cap down = bad
   data, and it gets investigated.
2. **Diagnose each failure** against the evidence recorded at call time. The
   honest answer is sometimes "no warning was present — this was ordinary market
   risk", and the system is allowed to say that rather than inventing a lesson.
3. **Mine the accumulated record for patterns.** A pattern is only promoted once
   it holds across enough calls to be more than noise — for example *"tokens
   with 15%+ insider supply fail 71% of the time vs a 44% baseline, over 21
   calls"*.
4. **Feed the lessons back.** They go into the next session's causal analysis and
   produce concrete threshold suggestions. Those are suggestions by default; set
   `auto_apply_lessons: true` to let the tracker tighten its own gates.

Everything lands in `data/lessons.json` in plain text, so you can always see
what the model believes and why.

### The wallets rank themselves

The tiers shipped in `kol_wallets.csv` are **priors** — reputation, not
measurement. They are a starting point, and the tracker replaces them.

The metric that matters is not PnL and it is not win rate. It is **Early Alpha
Rate**: the share of a wallet's entries where the token went on to multiply
*after* they bought.

> A wallet entering at $300k on a token that reaches $6M is worth following.
> A wallet entering at $4.5M on the same token made money and is worth nothing
> to you.

A wallet with 41% win rate that repeatedly enters before a 5x outranks one with
78% win rate that always arrives late. Peaks are built up observation by
observation across runs, so the numbers sharpen the longer it runs. Nothing is
back-filled. Below 8 measured entries a wallet keeps its prior; past that,
measurement takes over and can demote a famous name or promote an unknown one.

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

## 4. The wallet list

`config/kol_wallets.csv` already ships with **100 wallets**, distilled from a
~600-entry export: side wallets, numbered duplicates, dev wallets, bundlers,
bots and exchange addresses removed, one main wallet kept per person.

```csv
address,label,tier,weight,twitter
CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o,Cented,S,3,Cented7
```

Seed distribution: **12 S · 26 A · 34 B · 28 C**. Weights 3 / 2 / 1.4 / 1.

Those tiers are reputation priors and nothing more — nobody can measure Early
Alpha Rate without running the tracker first. From roughly two weeks in, the
"Tracked wallets" section of the report will start showing measured tiers next
to the seeds, and where they disagree, trust the measurement.

To edit the list: click the file on GitHub, click the pencil, change what you
need, **Commit changes**. Only `address` is required on a row.

Once the confluence returns sensible results, set `require_kol: true` in
`config/settings.yaml`: from then on nothing enters the runners without
validation from your wallets. That single setting turns this from a generic
scanner into your personal filter.

---

## 5. API budget

Three of the four sources are free and need no signup. Only wallet confluence
costs money.

| Tier | Cost | What you get | What is missing |
|---|---|---|---|
| **Free** | $0 | Dexscreener, GeckoTerminal, RugCheck, pump.fun. Runners, insider gates, metas, anti-wash, anti-rug, the learning loop, rule-based causal reads | No wallet tracking, and the causal reads stay blunt |
| **Helius Free** | $0 | The 100 tracked wallets, twice a day | 1M credits/month is workable at 100 wallets and 2 runs/day, with little headroom |
| **Helius Developer** | $49/mo | The same, with real headroom, plus room to widen the list later | Nothing for this use case |
| **+ Anthropic API** | ~$10-20/mo | Language-model causal analysis instead of the rule engine, plus written diagnoses of every failure | Check current pricing — the tracker sends roughly 3M input and 600k output tokens a month at two runs a day |
| **+ X/Twitter API Basic** | +$100/mo | Real engagement metrics on the accounts attached to tokens, follower-bot detection | Only worth it once you want to score community *quality*, not just existence |

**Recommendation: start at $0, then add Anthropic before Helius.** The causal
layer is what makes the report worth reading — it is the difference between
"animals, $8.8M volume" and "a dormant 2024 ticker revived 19h ago, comment
volume at 61/hour". At ~$15/month it is the cheapest upgrade with the largest
visible effect.

Helius comes next: 100 wallets fit inside the free tier at two runs a day, so
try that before paying $49. Move up when the free quota starts biting.

To add a key: **Settings** → **Secrets and variables** → **Actions** → **New
repository secret**.

- `HELIUS_API_KEY` — from [helius.dev](https://helius.dev)
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com)

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
config/settings.yaml       thresholds, gates and weights
config/kol_wallets.csv     the 100 tracked wallets
src/sources.py             API clients, rate limiting, retries
src/filters.py             wash-trading and structural-risk detection
src/insiders.py            holder structure, insider concentration, gates
src/scoring.py             composite score
src/kol.py                 wallet confluence and buy timeline
src/kol_scoring.py         Early Alpha Rate, automatic re-tiering
src/research.py            causal evidence gathering
src/synthesis.py           "why it ran" analysis (LLM or rule engine)
src/postmortem.py          re-pricing past calls, diagnosis, pattern mining
src/narrative.py           themes, metas, emergence detection
src/learn.py               weight calibration against realised outcomes
src/report.py              HTML rendering and post drafts
data/lessons.json          patterns the record supports, in plain text
data/kol_scores.json       per-wallet measured performance
data/calibration.json      current weights and performance history
out/index.html             the published report
```

---

*Une version française du guide d'installation se trouve dans `INSTALL-FR.md`.*
