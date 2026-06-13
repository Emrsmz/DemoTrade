# CTrading — Project Status & Roadmap

_Single source of truth for what exists, what works, and what is next._
_Last updated: 2026-06-06._

> Disclaimer: educational/informational tool, not financial advice. The bot does
> NOT place orders. It prints signals; the user trades manually 1–5 min later and
> sets stop-loss by hand.

---

## 1. What this is

A live crypto **signal analyzer**. It pulls public Binance market data (no API
key) for **30 USDT pairs** on **15-minute candles**, runs **6 technical
indicators** behind an **ADX regime filter**, and ranks coins by how many
indicators agree on a BUY (long-only — never shorts). For each coin it prints an entry time, a
suggested exit time and an "exit-in" countdown, refreshing every 60 s. It can
also push signals to Telegram and append every signal to an `.ods` trade log.

---

## 2. File map

| File | Role |
|------|------|
| `signal_analyzer.py` | **Core.** Indicators, ADX regime filter, conviction gates, scoring, live board, CLI. Single source of the strategy AND of the coin list. |
| `backtest.py` | **Simulator.** Replays the strategy candle-by-candle (lookahead-safe). Imports the live strategy + coin list so it can never drift. Env-selectable exit model. |
| `trade_logger.py` | Reads/writes `trade_log.ods`. Preserves manual notes, atomic save + file lock. |
| `performance.py` | Win-rate / profit stats from the trade log (`--stats`, Telegram `/istatistik`). |
| `telegram_notifier.py` | Telegram notifications + `/tara`, `/istatistik` command bot. |
| `backtest_report.py` | `backtest_*.json` → grouped `.ods` (direction / score / signal-group / coin). |
| `backtest_indicators.py` | Per-indicator VALUE-bucket analysis (which indicator value predicts wins). |
| `backtest_compare.py` | Two backtest JSONs → one side-by-side `.ods` (baseline vs variant). |
| `signal_last.py` | Backfills the last N days (default 20) of above-threshold BUY signals into `signal_last.ods` (exact `trade_log.ods` format) for review. Replays the live gate + 1h-trend exit; one trade at a time per coin. |
| `robust_sweep.py` | Parameter-neighbourhood (OAT) robustness sweep around P1A3 on the cached 90d/15m candles. Runs `backtest.py` in a fresh subprocess per gate value (gates are read from env at import), all replay identical candles. Prints per-value avg/exTop/beatBH to test overfit (knife-edge vs plateau). |

All backtest outputs (`.ods` reports, `.json` caches, candle pickles) are written
to **`backtests/`** by default, so they stay out of the project root. Older
snapshots are backed up in `~/Desktop/CopyCtrade/`.

---

## 3. How to run

**Live board**
```bash
python3 signal_analyzer.py              # live board, refresh every 60s
python3 signal_analyzer.py --once       # one scan and exit
python3 signal_analyzer.py --daemon     # headless 24/7 (systemd/journald)
python3 signal_analyzer.py --bot        # Telegram /tara command bot
python3 signal_analyzer.py --stats      # performance report from the trade log
```

**Backtest** (defaults: 15m, 90 days, the live 30 coins; all outputs default to `backtests/`)
```bash
# Download once into a candle cache, then A/B different exit modes on identical data:
CT_EXIT_MODE=htf python3 backtest.py --days 90 \
    --candle-cache backtests/candles_90d_15m.pkl \
    --output backtests/backtest_exit_htf.ods
# Compare two result JSONs:
python3 backtest_compare.py --baseline backtests/A.json --tuned backtests/B.json \
    --output backtests/cmp.ods
```

---

## 4. Strategy (current)

**6 indicators, 2 families**
- TREND (ride momentum): MACD, EMA-cross(9/21), VOL-spike, VWAP
- REVERSION (fade extremes): RSI(14), Bollinger(20,2)

**ADX regime filter** chooses which family is even eligible (not direction):
ADX≥25 → trust TREND only · ADX<20 → trust REVERSION only · 20–25 → all six.
In a trend, +DI/−DI must confirm the direction.

**Filters layered on top**
1. 1-hour HTF trend filter (drops a BUY fighting a clear 1h downtrend).
2. Conviction gates, env-overridable (see below).

(LONG-ONLY since 2026-06-02: shorts are removed from all trade-deciding code —
`analyze_symbol` only ever returns BUY, a bearish coin yields no signal, and the
backtester no longer opens shorts. The old short-VWAP entry gate was deleted.)

**Scoring / strength**: votes from eligible indicators. All agree → STRONG ·
≥ `CT_MIN_AGREE` (default 3) → MODERATE · fewer → WEAK (hidden).

**Exit (live, since 2026-06-02)**: TREND-BASED, not a fixed timer — hold while the
1h trend stays in the trade's favour, exit when it flips (a long exits when the 1h
trend turns DOWN). A max-hold cap (`CT_LIVE_MAX_HOLD`, default 24h) is the backstop
and the close time written to the trade log (auto-close still works; the earlier
trend-flip exit is recorded by hand).

### Tuning knobs (environment variables)
| Var | Live default | Meaning |
|-----|---------|---------|
| `CT_REVERSION_LONG` | `0` (off) | If `0`, drop RSI & Bollinger BUYs (the losing reversion longs). |
| `CT_VWAP_MIN` | `1.5` | A VWAP BUY counts only if price is ≥ this % above VWAP. |
| `CT_MACD_MIN` | `0.08` | MACD cross ignored below this histogram %. |
| `CT_VOL_MULT` | `3.8` | Volume-spike multiple (higher = fewer, stronger signals). |
| `CT_MIN_AGREE` | `3` | Min eligible indicators that must agree to show a coin. |
| `CT_LIVE_MAX_HOLD` | `1440` | Live max-hold cap (min); the trade-log close time. |
| `CT_EXIT_MODE` | `timer` | Backtest exit rule: `timer`\|`vwap`\|`htf`\|`ema` (the LIVE exit is always trend-based). |
| `CT_EXIT_MAX_HOLD` / `CT_EXIT_MIN_HOLD` | `1440` / `0` | Backtest condition-exit caps. |

**Validated live config (P1A3, baked in as defaults 2026-06-02):**
`CT_REVERSION_LONG=0 CT_VWAP_MIN=1.5 CT_MACD_MIN=0.08 CT_VOL_MULT=3.8 CT_MIN_AGREE=3`
+ trend (htf) exit — the first config with positive per-trade expectancy (see §5).
Set the knobs back to `1 / 0 / 0 / 2.0 / 2` to reproduce the old, looser board.

---

## 5. Research status & key findings

The strategy is **long-only** (shorts were a net drag in testing) and is a
**low-win / high-payoff trend follower** (~25–38% win rate, a few big winners).

### Exit-model A/B (2026-06-02, 90d, 30 coins, tuned entries, only exit varied)
Files (in `backtests/`): `backtest_exit_{timer,vwap,htf,ema}.ods/.json`, `backtest_cmp_{vwap,htf,ema}.ods`, cache `candles_90d_15m.pkl`.

| Exit | avg ret | median | beat B&H | trades | win% | exp/trade | profitable coins |
|------|--------:|-------:|---------:|-------:|-----:|----------:|-----------------:|
| `timer` (control) | −23.5% | −22.7% | 2/30 | 2719 | 26.3% | −0.295% | 0/30 |
| `vwap` | −18.5% | −19.1% | 5/30 | 1860 | 23.8% | −0.326% | 2/30 |
| **`htf`** ⭐ | **−10.0%** | −18.2% | **7/30** | **964** | **38.2%** | −0.328% | **4/30** |
| `ema` | −15.0% | −16.9% | 7/30 | 1509 | 25.6% | −0.317% | 3/30 |

avg Buy&Hold over the window = **+4.56%**.

**Verdict:**
- ✅ "Let winners run" works. `htf` (hold until the 1h trend flips) is the clear
  winner: it more than halved the loss (−23% → −10%), cut trades 2719 → 964
  (less cost), lifted win rate 26% → 38%, let NEAR run to +58%.
- ⚠️ Not solved yet. Per-trade expectancy stayed ~−0.33% in EVERY mode → the
  entries still have ~zero edge net of the ~0.3% round-trip cost. Exits decide
  how much you bleed and let rare winners run; they cannot create an entry edge.
- ⚠️ `htf` is still negative on average, B&H (+4.56%) still beats it, and the
  median coin is −18% (its good average is partly NEAR; ex-NEAR avg ≈ −12%,
  still the best mode).

**Bottom line:** keep `htf` exit. The next lever is the ENTRY edge, not the exit.

### Phase 1 — entry-edge A/B (2026-06-02, 90d, 30 coins, htf exit, only entries varied)
Files (in `backtests/`): `p1a1_score3.*`, `p1a2_buckets.*`, `p1a3_both.*`, `p1b_1h.*`.

| Config | avg ret | beat B&H | trades | win% | exp/trade | profitable |
|--------|--------:|---------:|-------:|-----:|----------:|-----------:|
| htf control | −10.0% | 7/30 | 964 | 38.2% | −0.328% | 4/30 |
| score≥3 | −1.4% | 17/30 | 360 | 40.8% | −0.106% | 11/30 |
| top buckets | −1.8% | 12/30 | 358 | 40.5% | −0.172% | 10/30 |
| **P1A3 (both)** ⭐ | **+0.20%** | **20/30** | **56** | 42.9% | **+0.098%** | 8/30 |
| 1h timeframe | −5.8% | 16/30 | 530 | 39.8% | −0.340% | 8/30 |

**Verdict:** stacking BOTH levers (score≥3 AND top buckets) flips it positive — P1A3
is the FIRST config with positive per-trade expectancy (+0.098%) and avg return
(+0.20%), beating B&H on 20/30 coins. 1h did NOT help → stay on 15m.
⚠️ Only **56 trades** over 90d (thin → fragile, possibly overfit; validate live/oos),
and still below avg B&H (+4.56%) — a selective, defensive **signal source**, not a
moonshot-capturer. **P1A3 is now the live default (Phase 2 done).**

### Phase 3 — Validation & robustness (2026-06-06)

**90d out-of-sample (`signal_last.py --days 90` → `signal_last.ods`):** replays the
LIVE gate (current defaults = old P1A3 center 3.8/0.08) candle-by-candle over 90d.
Result: **56 trades / 26 coins / 42.9% win / net ≈ +0.09%/trade** — matches the P1A3
backtest +0.098 EXACTLY, confirming the live code path is faithful to the backtest.
BUT 3 coins (XLM +27%, TON +22%, INJ +10% = +59%) carry it; **drop XLM alone →
52 trades −0.094%/trade, negative even before fees.** Fragility confirmed, not refuted.

**Parameter-robustness sweep (`robust_sweep.py`, 90d/15m, htf, OAT around P1A3).**
Two-layer verdict:
- ✅ **NOT overfit to parameters — broad positive plateau.** VWAP_MIN 1.0→2.0 all
  positive; VOL_MULT 3.0→4.6 all positive; MACD_MIN positive for ≥0.08 (a one-sided
  floor — 0.05/0.065 go negative); MIN_AGREE: 2 disastrous (−0.195%, 362 trades), 3 =
  correct floor, 4 too thin (7 trades). Small threshold moves don't flip the edge.
- ⚠️ **Single-coin dependence is STRUCTURAL** — `exTop` (avg/trade after dropping the
  single best coin) is negative in EVERY row (−0.19 … −3.1). Inherent to the
  low-win/high-payoff style; parameter tuning can't fix it.

**Free improvement the sweep revealed → VERIFIED COMBINED (`backtests/p1a3_improved.json`):**
the P1A3 center is a local dip. Re-centering to **`CT_VOL_MULT=4.2` + `CT_MACD_MIN=0.10`**
(VWAP 1.5, AGREE 3, REVERSION off, htf) gives:

| Config | n | avg/trade | total | win | beat B&H | exTop |
|--------|--:|----------:|------:|----:|---------:|------:|
| P1A3 center (3.8 / 0.08) | 56 | +0.098% | +5.48% | 42.9% | 20/30 | −0.386% |
| **Improved (4.2 / 0.10)** | 44 | **+0.631%** | **+27.78%** | **47.7%** | **21/30** | **−0.051%** |

The two axes compound positively. ~6.4× the per-trade expectancy, FIRST time total
return (+27.78%) beats avg B&H (+4.56%), and exTop −0.386 → −0.051 (dropping XLM is
now ~breakeven → single-coin fragility much reduced, not gone). ⚠️ CAVEAT: 4.2/0.10
were chosen as the sweep argmax on THIS 90d window (mild in-sample selection bias) —
the broad plateau mitigates it, but **CONFIRM out-of-sample before baking into live
defaults.** `signal_analyzer.py` defaults are UNTOUCHED — this is a candidate center only.

---

## 6. Roadmap

**Phase 0 — Exit-model A/B — DONE (2026-06-02).** `htf` wins. See §5.

**Phase 1 — Lift the entry edge — DONE (2026-06-02).** P1A3 (score≥3 + top buckets,
on the htf exit) is the first positive-expectancy config. 1h dropped. See §5.

**Phase 2 — Port the winning config to live — DONE (2026-06-02).** Baked P1A3 into
`signal_analyzer.py` defaults; replaced the fixed-timer exit DISPLAY with trend
guidance ("hold while 1h trend ↑, exit when it flips ↓") + a max-hold cap; the
trade-log close time + auto-close are anchored to the cap (Design A — `_close_dt`
now resolves the 24h-cap / cross-midnight close correctly).

**Phase 3 — Validate & robustness — IN PROGRESS (2026-06-06).** See §5.
- ✅ 90d out-of-sample (`signal_last.py --days 90`) — live path faithful to backtest,
  but single-coin fragility confirmed.
- ✅ Parameter-robustness sweep (`robust_sweep.py`) — params NOT overfit (broad
  plateau); single-coin dependence is structural.
- ✅ Improved center (VOL 4.2 / MACD 0.10) verified COMBINED — ~6.4× expectancy,
  beats avg B&H, exTop near breakeven. Candidate only (in-sample selection caveat).
- ⏭ **NEXT (out-of-sample confirmation of the improved center, then deploy):**
  (a) WIDER coin universe (~50-60 USDT pairs) → more independent moonshot chances,
  directly attacks single-coin fragility; (b) WALK-FORWARD on older/other windows →
  time-stability + removes the in-sample bias of 4.2/0.10. Only after one of these
  holds: bake 4.2/0.10 into `signal_analyzer.py` defaults, then 24/7 `--daemon`
  (Telegram + daemon ready; host shortlist Hetzner / AWS / Turkish VPS).
- ❌ Do NOT deploy on the 56-trade / single-window edge alone.

**Constraint:** never add an automatic stop-loss — the user sets stop-loss by hand.

---

## 7. Tech-debt / housekeeping (fixed 2026-06-02)
- ✅ Backtest now imports the live coin list (was a separate, drifted list).
- ✅ `MATIC` → `POL` (MATIC delisted/renamed on Binance).
- ✅ CLI description "5-minute" → "15-minute".

Open / minor:
- `trade_log.ods.lock` sidecar file appears in the dir (fcntl lock; harmless).
- The fcntl lock does not coordinate with LibreOffice — close the `.ods` in LO
  before/while the analyzer writes to avoid a save race.
