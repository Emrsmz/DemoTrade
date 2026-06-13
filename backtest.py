#!/usr/bin/env python3
"""
================================================================================
 BACKTEST  -  REPLAYS THE LIVE SIGNAL STRATEGY OVER 90 DAYS OF HISTORY
================================================================================

WHAT THIS DOES
--------------
It downloads historical candles for a list of coins and replays the EXACT signal
logic the live analyzer uses (`analyze_symbol` in signal_analyzer.py: six
indicators gated by the ADX regime filter, plus the higher-timeframe trend
filter and the strength/freshness gate from `scan()`), so the result reflects
the real strategy rather than a simplified RSI + moving-average copy.

For every coin it reports the bot's return, the Buy & Hold return for the same
period, the number of trades, the win rate, and the maximum drawdown, then writes
everything to a two-sheet .ods spreadsheet and prints a ranked comparison.

NO LOOKAHEAD BIAS (the most important rule)
-------------------------------------------
At each candle i the decision is made using ONLY candles up to and including i.
Concretely:
  * The signal for candle i is computed from a rolling window that ends at i
    (df.iloc[i-99 : i+1]). 100 candles matches the live bot, which never sees
    more than CANDLE_LIMIT candles, so this is both faithful and fast.
  * The trade is then FILLED on the NEXT candle's open (i+1). You cannot trade on
    a candle's close at the instant it closes, so filling on the next open is the
    honest, lookahead-free choice.
  * The higher-timeframe (HTF) trend filter only ever looks at HTF candles that
    have ALREADY fully closed by the time candle i closes.

TRADING MODEL (kept deliberately simple)
----------------------------------------
  * Spot, long-only: a BUY signal opens a position with the whole balance; the
    position is released by the active exit rule (see EXIT_MODE), not by an
    opposite signal. A BUY while already long is ignored (the balance is fully
    deployed), and the strategy never shorts (analyze_symbol only returns BUY).
  * Starting balance: 1000 USDT per coin, evaluated independently.
  * Commission: 0.1% per side (so 0.2% per round trip).
  * Slippage: an extra 0.05% per side (buys fill a touch higher, sells lower).
  * Any position still open at the end of the data is closed at the last close,
    so the final return and the stats include it.

NOTE ON TIMEFRAME
-----------------
This backtest defaults to 15-minute candles, matching the LIVE analyzer exactly:
15m signals plus a 1-hour higher-timeframe trend filter (HTF_FACTOR=4 makes the
HTF timeframe 15m x 4 = 1h, the same pair the live board uses). Run
`python3 backtest.py --timeframe 1h` to test the strategy on the slower 1-hour
timeframe instead.

DISCLAIMER: For educational purposes only. Past performance from a backtest does
not predict future results. This is not financial advice.
================================================================================
"""

import argparse
import bisect
import json
import math
import os
import pickle
import re
import time
from datetime import datetime, timezone

import ccxt
import pandas as pd
from colorama import Fore, Style, init
from ta.trend import EMAIndicator
from ta.volume import VolumeWeightedAveragePrice

# odfpy (imported as `odf`) is used to write the styled .ods spreadsheet.
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import (
    ParagraphProperties,
    Style as OdfStyle,
    TableCellProperties,
    TableColumnProperties,
    TextProperties,
)
from odf.table import Table, TableColumn, TableRow, TableCell
from odf.text import P
from odf.config import (
    ConfigItem,
    ConfigItemMapEntry,
    ConfigItemMapIndexed,
    ConfigItemMapNamed,
    ConfigItemSet,
)

# Reuse the real strategy and its tuning so the backtest can never drift away
# from what the live program actually does. `analyze_symbol` is the signal
# function; `resample_ohlcv` builds the higher-timeframe candles; `htf_allows`
# is the higher-timeframe trend gate. The constants are the same ones the live
# board uses (window size, warm-up, freshness, HTF EMAs).
from signal_analyzer import (
    analyze_symbol,
    htf_allows,
    resample_ohlcv,
    CANDLE_LIMIT,
    MIN_CANDLES,
    CROSS_FRESHNESS_CANDLES,
    HOLD_MINUTES,
    HTF_EMA_FAST,
    HTF_EMA_SLOW,
    HTF_NEUTRAL_BAND,
    EMA_FAST,
    EMA_SLOW,
    VWAP_WINDOW,
    SYMBOLS,
)


# ----------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------
TIMEFRAME = "15m"            # analysis timeframe — matches the live board (see docstring)
DAYS = 90                    # how much history to download and replay
INITIAL_CAPITAL = 1000.0     # starting balance per coin, in USDT
COMMISSION = 0.001           # 0.1% fee per side (entry and exit)
SLIPPAGE = 0.0005            # 0.05% extra slip per side (worse fill price)

# LONG-ONLY (permanent). This project never shorts: the 90d test showed shorts
# were a net drag and spot trading cannot truly short. A BUY signal opens a long;
# the trade is released by the exit rule (see EXIT_MODE). analyze_symbol only ever
# returns BUY, so a SELL is never even seen here.

# Replay the higher-timeframe trend filter the live bot applies in scan(). The
# live relationship is "base 15m, HTF 1h" = 4x coarser, so we keep the same 4x
# ratio: a 1h backtest uses a 4h trend filter, a 15m backtest uses a 1h one.
USE_HTF_FILTER = True
HTF_FACTOR = 4

# The rolling window fed to analyze_symbol at each step. It equals the live
# CANDLE_LIMIT so the backtest sees exactly what the live bot would, and it caps
# the per-step cost (otherwise replaying N candles would be O(N^2)).
WINDOW = CANDLE_LIMIT

OUTPUT_FILE = "backtest_sonuclar.ods"
# All backtest artifacts (.ods reports, .json caches, candle pickles) live in a
# dedicated `backtests/` folder next to this script, so they never clutter the
# project root. It is created on demand; override any path with the CLI flags.
BACKTESTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtests")
DEFAULT_OUTPUT = os.path.join(BACKTESTS_DIR, OUTPUT_FILE)

# Public Binance market-data mirror. The normal api.binance.com endpoint is
# geo-blocked (HTTP 451) in some regions; data-api.binance.vision serves the
# same public klines/exchangeInfo and bypasses that block. No API key needed.
DATA_API_URL = "https://data-api.binance.vision/api/v3"

# The coins to test default to the SAME 30 the live board scans (imported above
# as SYMBOLS from signal_analyzer), so the backtest measures exactly what gets
# traded live and the two lists can never silently drift apart again. Override
# with --symbols to test a different basket. (Until 2026-06-02 this was a separate
# hard-coded list that had drifted away from the live one — that gap is now fixed.)


# ----------------------------------------------------------------------------
# EXIT MODEL  (env-selectable: timer | vwap | htf | ema)
# ----------------------------------------------------------------------------
# The live bot today closes every trade on a fixed time window (HOLD_MINUTES,
# 30-45 min by strength). The 90-day test showed that timer is the core problem:
# it CAPS the winners, so a ~27%-win trend strategy can never collect the
# winners-much-bigger-than-losers payoff that profile depends on, and per-trade
# expectancy sits at roughly minus the round-trip fee. These modes let us A/B a
# "let winners run" trend-exit against that timer baseline without editing code:
#
#   timer  default. Hold HOLD_MINUTES[strength], then exit. Reproduces the
#          existing tuned-timer baseline exactly, so it is the experiment control.
#   vwap   Hold until price CLOSES back on the wrong side of the rolling VWAP
#          (a long exits when it closes below VWAP) - ride the trend as long as
#          price stays the right side of the average most participants paid.
#   htf    Hold until the 1-hour higher-timeframe trend flips against the trade
#          (a long exits when the 1h trend turns DOWN). The slowest, ride-the-
#          whole-leg exit.
#   ema    Hold until the fast EMA (9) crosses back through the slow EMA (21)
#          against the trade. A middle-speed momentum roll-over exit.
#
# Every condition mode keeps a MAX-hold safety cap (nothing rides forever) and an
# optional MIN-hold floor (so a trade is not stopped out the very next candle when
# price is a touch offside at the fill). NOT a stop-loss - the user sets that by
# hand while trading; this only decides WHEN a winning/flat trade is released.
def _env_str(name, default):
    """Read an environment variable as a trimmed string, or `default` if unset."""
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


def _env_float(name, default):
    """Read an environment variable as a float, or `default` if unset/invalid."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


EXIT_MODE = _env_str("CT_EXIT_MODE", "timer").lower()
# Condition modes only: never hold longer than this many minutes (a generous
# research default so the EXIT CONDITION, not the cap, drives the result; lower it
# for live realism once we know the condition helps). Timer mode ignores it.
EXIT_MAX_HOLD_MIN = _env_float("CT_EXIT_MAX_HOLD", 1440.0)   # 24h
# Condition modes only: do not let an exit fire in the first N minutes after entry
# (0 = no floor). Guards against an instant stop-out at the next candle.
EXIT_MIN_HOLD_MIN = _env_float("CT_EXIT_MIN_HOLD", 0.0)


# ----------------------------------------------------------------------------
# SMALL HELPERS
# ----------------------------------------------------------------------------
def timeframe_to_minutes(timeframe):
    """Convert a ccxt timeframe string like '1h' or '15m' into minutes."""
    unit = timeframe[-1]
    amount = int(timeframe[:-1])
    if unit == "m":
        return amount
    if unit == "h":
        return amount * 60
    if unit == "d":
        return amount * 1440
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def fmt_ts(ms):
    """Format an epoch-millisecond timestamp (UTC) as 'YYYY-MM-DD HH:MM'."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def num_text(value, sig=5):
    """Format a price with about `sig` significant figures, no scientific
    notation, trailing zeros trimmed. Keeps both big and tiny coins short and
    readable (BTC -> 69399, PEPE -> 0.0000123) instead of a fixed long decimal.
    """
    if value is None:
        return ""
    if value == 0:
        return "0"
    magnitude = abs(value)
    # Decimals needed to show `sig` significant figures at this magnitude.
    decimals = max(0, sig - (math.floor(math.log10(magnitude)) + 1))
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _detail_value(text):
    """Pull the leading number out of an indicator detail string.

    analyze_symbol describes each firing indicator with a short string -
    "RSI 24", "MACD 0.12%", "BB 0.30%", "EMA 0.02%", "VWAP +0.62%", "VOL 3.2x".
    This returns the numeric part (24.0, 0.12, ..., 3.2) so the report can study
    how an indicator's VALUE relates to the trade outcome. None if no number.
    """
    if not text:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text)
    return float(match.group()) if match else None


# ----------------------------------------------------------------------------
# DATA FETCHING (paginated, so 90 days fits despite the 1000-candle API limit)
# ----------------------------------------------------------------------------
def build_exchange():
    """Create a public Binance instance pointed at the 451-bypass data mirror."""
    exchange = ccxt.binance({"enableRateLimit": True})
    # Route every PUBLIC call (klines and exchangeInfo) through the data mirror.
    exchange.urls["api"]["public"] = DATA_API_URL
    # Only load SPOT markets: it avoids the futures/options endpoints (which are
    # not on the mirror and would fail), and all our pairs are spot USDT pairs.
    try:
        exchange.options["fetchMarkets"] = ["spot"]
    except Exception:
        pass
    return exchange


def fetch_ohlcv_range(exchange, symbol, timeframe, since_ms, until_ms, tf_minutes):
    """Download every candle in [since_ms, until_ms]. Returns a DataFrame or None.

    Binance returns at most 1000 candles per request, so we page forward from
    `since_ms`, advancing the cursor past the last candle each time, until we
    reach the end of the range (or the exchange runs out of data). Any failure
    (delisted pair, network hiccup) returns None so one bad coin never stops the
    whole run - the same isolation the live fetcher uses.
    """
    step_ms = tf_minutes * 60_000
    limit = 1000
    cursor = since_ms
    rows = []
    try:
        while cursor < until_ms:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
            if not batch:
                break
            rows.extend(batch)
            last_ts = batch[-1][0]
            next_cursor = last_ts + step_ms
            if next_cursor <= cursor:    # no forward progress -> stop (safety)
                break
            cursor = next_cursor
            if len(batch) < limit:       # the exchange returned the final page
                break
    except Exception:
        return None

    # Keep only candles inside the window, de-duplicate by timestamp (paging can
    # overlap by one), and sort to be safe.
    seen = set()
    clean = []
    for r in rows:
        ts = r[0]
        if ts > until_ms or ts in seen:
            continue
        seen.add(ts)
        clean.append(r)
    clean.sort(key=lambda r: r[0])

    if len(clean) < MIN_CANDLES:
        return None

    df = pd.DataFrame(clean, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["timestamp"] = df["timestamp"].astype("int64")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# HIGHER-TIMEFRAME TREND FILTER (lookahead-safe)
# ----------------------------------------------------------------------------
def compute_htf_trends(df, base_minutes, factor):
    """Return one trend label ('UP'/'DOWN'/'NEUTRAL') per base candle.

    The live bot reads the trend from a fast/slow EMA pair on a higher timeframe.
    We rebuild that locally (no extra network calls) by resampling the base
    candles into HTF candles `factor` times coarser, then for each base candle we
    use the trend of the most recent HTF candle that has FULLY CLOSED by the time
    the base candle closes. Using only closed HTF candles guarantees no lookahead.
    """
    n = len(df)
    neutral = ["NEUTRAL"] * n
    if factor <= 1:
        return neutral

    htf_minutes = base_minutes * factor
    htf = resample_ohlcv(df, htf_minutes)
    # Need enough HTF candles for the slow EMA to be meaningful, mirroring the
    # live guard; otherwise do not filter.
    if len(htf) < HTF_EMA_SLOW + 5:
        return neutral

    closes = htf["close"].reset_index(drop=True)
    fast = EMAIndicator(close=closes, window=HTF_EMA_FAST).ema_indicator()
    slow = EMAIndicator(close=closes, window=HTF_EMA_SLOW).ema_indicator()

    span_ms = htf_minutes * 60_000
    htf_ts = htf["timestamp"].astype("int64").tolist()
    trends = []
    close_times = []   # the moment each HTF candle is fully closed (sorted ascending)
    for k in range(len(htf)):
        f, s = fast.iloc[k], slow.iloc[k]
        if pd.isna(f) or pd.isna(s) or not s:
            trends.append("NEUTRAL")
        else:
            gap_pct = (f - s) / s * 100
            if gap_pct > HTF_NEUTRAL_BAND:
                trends.append("UP")
            elif gap_pct < -HTF_NEUTRAL_BAND:
                trends.append("DOWN")
            else:
                trends.append("NEUTRAL")
        close_times.append(htf_ts[k] + span_ms)

    # Map each base candle to the last HTF candle closed by the base candle's
    # own close time. bisect finds that index in O(log m).
    base_span = base_minutes * 60_000
    base_ts = df["timestamp"].astype("int64").tolist()
    out = []
    for i in range(n):
        decision_time = base_ts[i] + base_span   # candle i is closed at this moment
        idx = bisect.bisect_right(close_times, decision_time) - 1
        out.append(trends[idx] if idx >= 0 else "NEUTRAL")
    return out


# ----------------------------------------------------------------------------
# THE BACKTEST FOR ONE COIN
# ----------------------------------------------------------------------------
def run_backtest(symbol, df, htf_trends, use_htf):
    """Replay the strategy candle by candle. Returns (summary_dict, trades_list).

    Returns (None, []) if there is not enough data to trade.
    """
    n = len(df)
    start = MIN_CANDLES          # warm-up: indicators need some history first
    if n <= start + 1:
        return None, []

    opens = df["open"].tolist()
    closes = df["close"].tolist()
    ts = df["timestamp"].astype("int64").tolist()

    capital = INITIAL_CAPITAL    # realised balance while flat / before a trade
    position = 0                 # 0 = flat, +1 = long (long-only, never short)
    units = 0.0                  # coin amount held while in a position
    entry_price = 0.0
    entry_ts = None
    entry_sig = None             # (score, strength, contributors) captured at entry
    trades = []
    equity_curve = []            # mark-to-market balance each candle, for drawdown

    def mark_to_market(price):
        """Current value of the open long (or cash when flat). Long-only."""
        if position > 0:
            return units * price
        return capital

    def open_position(fill, fill_ts, rec):
        """Deploy the whole balance into a long.

        The fee is paid on entry and the fill is a touch worse (higher to buy).
        The triggering signal's score/strength/indicators are stored so the
        closed trade can be grouped by them later."""
        nonlocal position, units, entry_price, entry_ts, entry_sig
        price = fill * (1 + SLIPPAGE)
        units = capital * (1 - COMMISSION) / price
        entry_price = price
        entry_ts = fill_ts
        entry_sig = (int(rec["score"]), rec["strength"], list(rec["contributors"]),
                     {name: _detail_value(text)
                      for name, text in rec.get("details", {}).items()})
        position = 1

    def close_position(fill, fill_ts):
        """Close the open long at `fill` and record the finished trade. Long-only."""
        nonlocal capital, position, units, entry_sig
        exit_price = fill * (1 - SLIPPAGE)
        proceeds = units * exit_price * (1 - COMMISSION)
        kind = "LONG"
        pnl_pct = (proceeds / capital - 1) * 100      # capital = balance at entry
        score, strength, contributors, values = entry_sig
        trades.append({
            "symbol": symbol,
            "type": kind,
            "entry_ts": entry_ts,
            "entry_price": entry_price,
            "exit_ts": fill_ts,
            "exit_price": exit_price,
            "pnl": pnl_pct,
            # signal context, so the report can group by strength / indicators
            "score": score,
            "strength": strength,
            "signals": "+".join(sorted(contributors)),
            # the measured value of each contributing indicator at entry, so the
            # report can show how an indicator's VALUE relates to the outcome
            "values": values,
        })
        capital = proceeds
        position = 0
        units = 0.0
        entry_sig = None

    # Enter on a fresh actionable signal; while a position is open we do NOT
    # re-evaluate entries (the live trade is already placed). HOW the trade is
    # released depends on EXIT_MODE:
    #   timer  hold the strength-based window (HOLD_MINUTES), then exit — exactly
    #          like the live bot, which prints an entry and an exit time. This is
    #          the experiment control and the prior tuned baseline.
    #   vwap/htf/ema  hold until the TREND turns against the trade, so a winner can
    #          keep running instead of being cut at 30-45 min; a MAX-hold cap and an
    #          optional MIN-hold floor bound it. NOT a stop-loss (the user sets that
    #          by hand) — this only decides WHEN a winning/flat trade is released.
    #
    # For the condition modes we precompute one CAUSAL "is the trend still bullish?"
    # flag per candle (True / False / None=unknown->hold), read at candle i's close:
    #   vwap  price closed at/above the rolling VWAP. A 20-window rolling VWAP at
    #         candle i depends only on candles i-19..i, so reading index i over the
    #         full series equals the live value — no lookahead.
    #   ema   fast EMA(9) >= slow EMA(21). EMAs are causal; over the full series they
    #         converge to the live 100-window values after warm-up.
    #   htf   the 1h higher-timeframe trend (already per-candle and lookahead-safe):
    #         UP=bull, DOWN=bear, NEUTRAL=None (hold).
    exit_bull = None
    if EXIT_MODE == "vwap":
        vwap = VolumeWeightedAveragePrice(
            high=df["high"], low=df["low"], close=df["close"],
            volume=df["volume"], window=VWAP_WINDOW,
        ).volume_weighted_average_price().tolist()
        exit_bull = [None if pd.isna(v) else (closes[k] >= v)
                     for k, v in enumerate(vwap)]
    elif EXIT_MODE == "ema":
        close_s = df["close"]
        ema_fast = EMAIndicator(close=close_s, window=EMA_FAST).ema_indicator().tolist()
        ema_slow = EMAIndicator(close=close_s, window=EMA_SLOW).ema_indicator().tolist()
        exit_bull = [None if (pd.isna(f) or pd.isna(s)) else (f >= s)
                     for f, s in zip(ema_fast, ema_slow)]
    elif EXIT_MODE == "htf":
        exit_bull = [True if t == "UP" else False if t == "DOWN" else None
                     for t in htf_trends]

    def is_offside(i):
        """Has the trend turned against the open long by candle i's close?
        A long is offside when the trend flag is bearish (False). Unknown/neutral
        (None) holds the trade. Long-only."""
        state = exit_bull[i]
        if state is None:
            return False
        return state is False

    max_hold_ms = EXIT_MAX_HOLD_MIN * 60000
    min_hold_ms = EXIT_MIN_HOLD_MIN * 60000
    exit_deadline_ms = None      # timer mode: ms at/after which the open trade exits

    for i in range(start, n):
        # Equity for the drawdown curve: realised cash when flat, otherwise the
        # current mark-to-market value of the open position.
        equity_curve.append(mark_to_market(closes[i]))

        # --- EXIT first: release the open trade per the active exit rule. ---
        if position != 0:
            if EXIT_MODE == "timer":
                # Fixed window known at entry: exit at the open of the first candle
                # at/after the deadline (deadline is pre-known, so no lookahead).
                if exit_deadline_ms is not None and ts[i] >= exit_deadline_ms:
                    close_position(opens[i], ts[i])
                    exit_deadline_ms = None
            else:
                held_ms = ts[i] - entry_ts
                if held_ms >= max_hold_ms:
                    # Safety cap: time-based, so fill at this candle's open.
                    close_position(opens[i], ts[i])
                elif held_ms >= min_hold_ms and is_offside(i):
                    # Trend flipped against us: like an entry, the signal is read at
                    # this close and FILLED on the next open (symmetric, no
                    # lookahead). Skip a same-candle re-entry so that next open is
                    # not reused — the loop re-evaluates entries from candle i+1.
                    if i + 1 < n:
                        close_position(opens[i + 1], ts[i + 1])
                        continue
                    close_position(closes[i], ts[i])   # last bar: no next open

        # Only look for a new entry while flat — one trade at a time, like live.
        if position != 0:
            continue

        # --- DECIDE using only candles up to and including i (no lookahead) ---
        lo = max(0, i - WINDOW + 1)
        window = df.iloc[lo:i + 1]
        rec = analyze_symbol(symbol, window)

        # The live "is this actionable?" gate from scan(): a real signal, strong
        # enough (not WEAK), and freshly triggered.
        actionable = (
            rec is not None
            and rec["strength"] != "WEAK"
            and rec["candles_ago"] <= CROSS_FRESHNESS_CANDLES
        )
        # Higher-timeframe trend filter: drop a signal that fights the HTF trend.
        if actionable and use_htf and not htf_allows(htf_trends[i], rec["direction"]):
            actionable = False
        if not actionable:
            continue

        # Long-only: analyze_symbol only ever returns BUY, so every actionable
        # signal opens a long.

        # --- EXECUTE on the next candle's open (honest, lookahead-free fill) ---
        if i + 1 >= n:
            continue            # no next candle to fill on (last bar)
        open_position(opens[i + 1], ts[i + 1], rec)
        # Timer mode exits after this strength tier's hold window (minutes -> ms)
        # from the fill; the condition modes ignore this and use the trend rule.
        exit_deadline_ms = ts[i + 1] + HOLD_MINUTES[rec["strength"]] * 60000

    # Close any position still open at the end, at the final close price, so the
    # final return and the stats reflect the full period.
    if position != 0:
        close_position(closes[n - 1], ts[n - 1])

    # --- METRICS ---
    bot_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    buy_hold = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0
    n_trades = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = wins / n_trades * 100 if n_trades else 0.0
    max_dd = max_drawdown(equity_curve)

    summary = {
        "symbol": symbol,
        "bot_return": bot_return,
        "buy_hold": buy_hold,
        "diff": bot_return - buy_hold,
        "trades": n_trades,
        "win_rate": win_rate,
        "max_dd": max_dd,
    }
    return summary, trades


def max_drawdown(equity_curve):
    """Largest peak-to-trough drop of the equity curve, as a positive percent."""
    peak = float("-inf")
    worst = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            drop = (peak - value) / peak * 100
            if drop > worst:
                worst = drop
    return worst


# ----------------------------------------------------------------------------
# .ods OUTPUT  (two sheets: a ranked summary and the full trade log)
# ----------------------------------------------------------------------------
# Colours used across the sheets.
HEADER_BG = "#D9D9D9"   # light grey header
TOTAL_BG = "#DDEBF7"    # light blue average/total row
ZEBRA_BG = "#F2F2F2"    # alternating shade per coin block in the trade log
POS_BG, POS_FG = "#C6EFCE", "#006100"   # green fill + dark green text (gains)
NEG_BG, NEG_FG = "#FFC7CE", "#9C0006"   # red fill + dark red text (losses)
GREEN_FG, RED_FG = "#107C41", "#C00000"  # plain green / red text (no fill)


def _style_factory(doc):
    """Return a get(...) that creates and caches table-cell styles on demand.

    Each unique (align, background, colour, bold) combination becomes exactly one
    style, created the first time it is asked for. This keeps the many
    combinations (zebra shade x win/loss colour x left/right alignment)
    manageable without spelling every one out by hand.
    """
    cache = {}

    def get(*, align="center", background=None, colour=None, bold=False):
        key = (align, background, colour, bold)
        if key in cache:
            return cache[key]
        name = f"c{len(cache)}"
        style = OdfStyle(name=name, family="table-cell")
        props = {"border": "0.5pt solid #d0d0d0", "verticalalign": "middle"}
        if background:
            props["backgroundcolor"] = background
        style.addElement(TableCellProperties(**props))
        if colour or bold:
            style.addElement(TextProperties(
                color=colour or "#000000",
                fontweight="bold" if bold else "normal",
            ))
        style.addElement(ParagraphProperties(textalign=align))
        doc.automaticstyles.addElement(style)
        cache[key] = name
        return name

    return get


def _scell(text, stylename):
    """A text cell (left/centre aligned columns: coin, type, dates)."""
    cell = TableCell(valuetype="string", stylename=stylename)
    cell.addElement(P(text="" if text is None else str(text)))
    return cell


def _ncell(number, display, stylename):
    """A numeric cell - keeps the real value (so sorting/filtering works) while
    showing a tidy formatted string."""
    cell = TableCell(valuetype="float", value=number, stylename=stylename)
    cell.addElement(P(text=display))
    return cell


def _add_columns(doc, table, widths, tag):
    """Give each column its own width (cm)."""
    for i, width in enumerate(widths):
        name = f"{tag}{i}"
        col_style = OdfStyle(name=name, family="table-column")
        col_style.addElement(TableColumnProperties(columnwidth=width))
        doc.automaticstyles.addElement(col_style)
        table.addElement(TableColumn(stylename=name))


def _header_row(st, titles):
    """One bold, grey, centred header row."""
    row = TableRow()
    style = st(align="center", background=HEADER_BG, bold=True)
    for title in titles:
        row.addElement(_scell(title, style))
    return row


def _summary_sheet(doc, st, summaries):
    """Sheet 1: one row per coin, ranked by bot return (best to worst), with a
    coloured headline return, a coloured edge-vs-Buy&Hold, and an AVERAGE row."""
    headers = ["Coin", "Bot Return %", "Buy&Hold %", "Diff %",
               "Trades", "Win Rate %", "Max Drawdown %"]
    widths = ["3.2cm", "2.8cm", "2.8cm", "2.6cm", "2cm", "2.6cm", "3.2cm"]
    table = Table(name="Summary")
    _add_columns(doc, table, widths, "su")
    table.addElement(_header_row(st, headers))

    num = st(align="center")
    coin_style = st(align="center", bold=True)
    ranked = sorted(summaries, key=lambda x: x["bot_return"], reverse=True)
    for s in ranked:
        gain = s["bot_return"] >= 0
        ret_style = st(align="center", bold=True,
                       background=POS_BG if gain else NEG_BG,
                       colour=POS_FG if gain else NEG_FG)
        diff_style = st(align="center", colour=GREEN_FG if s["diff"] >= 0 else RED_FG)
        row = TableRow()
        row.addElement(_scell(s["symbol"], coin_style))
        row.addElement(_ncell(s["bot_return"], f"{s['bot_return']:+.1f}", ret_style))
        row.addElement(_ncell(s["buy_hold"], f"{s['buy_hold']:+.1f}", num))
        row.addElement(_ncell(s["diff"], f"{s['diff']:+.1f}", diff_style))
        row.addElement(_ncell(s["trades"], str(s["trades"]), num))
        row.addElement(_ncell(s["win_rate"], f"{s['win_rate']:.1f}", num))
        row.addElement(_ncell(s["max_dd"], f"{s['max_dd']:.1f}", num))
        table.addElement(row)

    # AVERAGE row: averages of the % columns, total of the trade counts.
    if ranked:
        n = len(ranked)
        avg = {k: sum(s[k] for s in ranked) / n
               for k in ("bot_return", "buy_hold", "diff", "win_rate", "max_dd")}
        total_trades = sum(s["trades"] for s in ranked)
        tot = st(align="center", background=TOTAL_BG, bold=True)
        bot_tot = st(align="center", background=TOTAL_BG, bold=True,
                     colour=GREEN_FG if avg["bot_return"] >= 0 else RED_FG)
        diff_tot = st(align="center", background=TOTAL_BG, bold=True,
                      colour=GREEN_FG if avg["diff"] >= 0 else RED_FG)
        row = TableRow()
        row.addElement(_scell("AVERAGE", st(align="center", background=TOTAL_BG, bold=True)))
        row.addElement(_ncell(avg["bot_return"], f"{avg['bot_return']:+.1f}", bot_tot))
        row.addElement(_ncell(avg["buy_hold"], f"{avg['buy_hold']:+.1f}", tot))
        row.addElement(_ncell(avg["diff"], f"{avg['diff']:+.1f}", diff_tot))
        row.addElement(_ncell(total_trades, str(total_trades), tot))
        row.addElement(_ncell(avg["win_rate"], f"{avg['win_rate']:.1f}", tot))
        row.addElement(_ncell(avg["max_dd"], f"{avg['max_dd']:.1f}", tot))
        table.addElement(row)

    doc.spreadsheet.addElement(table)


def _trade_log_sheet(doc, st, trades):
    """Sheet 2: every trade, grouped by coin, with each coin block shaded in an
    alternating tone so the boundaries between coins are easy to see."""
    headers = ["Coin", "Type", "Entry Date", "Entry Price",
               "Exit Date", "Exit Price", "P/L %"]
    widths = ["3.2cm", "1.8cm", "3.8cm", "3.2cm", "3.8cm", "3.2cm", "2.4cm"]
    table = Table(name="Trade Log")
    _add_columns(doc, table, widths, "tl")
    table.addElement(_header_row(st, headers))

    shade = False
    prev_symbol = None
    for t in sorted(trades, key=lambda x: (x["symbol"], x["entry_ts"])):
        if t["symbol"] != prev_symbol:   # a new coin block -> flip the shade
            shade = not shade
            prev_symbol = t["symbol"]
        bg = ZEBRA_BG if shade else None
        pnl_fg = GREEN_FG if t["pnl"] > 0 else RED_FG if t["pnl"] < 0 else None

        coin_style = st(align="center", background=bg, bold=True)
        text_style = st(align="center", background=bg)
        num_style = st(align="center", background=bg)
        pnl_style = st(align="center", background=bg, colour=pnl_fg, bold=True)

        row = TableRow()
        row.addElement(_scell(t["symbol"], coin_style))
        row.addElement(_scell(t["type"], text_style))
        row.addElement(_scell(fmt_ts(t["entry_ts"]), text_style))
        row.addElement(_ncell(t["entry_price"], num_text(t["entry_price"]), num_style))
        row.addElement(_scell(fmt_ts(t["exit_ts"]), text_style))
        row.addElement(_ncell(t["exit_price"], num_text(t["exit_price"]), num_style))
        row.addElement(_ncell(t["pnl"], f"{t['pnl']:+.1f}", pnl_style))
        table.addElement(row)

    doc.spreadsheet.addElement(table)


def _freeze_panes(doc, sheet_names):
    """Freeze the header row and the first column on each sheet, so the titles
    and the coin name stay visible while scrolling. Stored as a LibreOffice view
    setting; if a viewer ignores it the file still opens fine.
    """
    view_settings = ConfigItemSet(name="ooo:view-settings")
    views = ConfigItemMapIndexed(name="Views")
    view = ConfigItemMapEntry()
    view.addElement(ConfigItem(name="ViewId", type="string", text="view1"))
    tables = ConfigItemMapNamed(name="Tables")
    for name in sheet_names:
        sheet = ConfigItemMapEntry(name=name)
        # SplitMode 2 = "frozen" (1 would be a movable split). Position 1 freezes
        # the first column / first row.
        for key, kind, value in (
            ("HorizontalSplitMode", "short", "2"),
            ("VerticalSplitMode", "short", "2"),
            ("HorizontalSplitPosition", "int", "1"),
            ("VerticalSplitPosition", "int", "1"),
            ("PositionLeft", "int", "0"),
            ("PositionRight", "int", "1"),
            ("PositionTop", "int", "0"),
            ("PositionBottom", "int", "1"),
        ):
            sheet.addElement(ConfigItem(name=key, type=kind, text=value))
        tables.addElement(sheet)
    view.addElement(tables)
    views.addElement(view)
    view_settings.addElement(views)
    doc.settings.addElement(view_settings)


def write_ods(path, summaries, trades):
    """Build the two-sheet styled spreadsheet and save it next to the script."""
    doc = OpenDocumentSpreadsheet()
    st = _style_factory(doc)
    _summary_sheet(doc, st, summaries)
    _trade_log_sheet(doc, st, trades)
    _freeze_panes(doc, ["Summary", "Trade Log"])
    doc.save(path)
    print(f"\n✓ {path} saved")


# ----------------------------------------------------------------------------
# TERMINAL REPORT
# ----------------------------------------------------------------------------
def _coloured(value, width, prec=2, signed=True):
    """A fixed-width number string coloured green (>=0) or red (<0).

    The ANSI colour codes are zero-width on screen, so column alignment is kept
    even though the raw string is longer.
    """
    sign = "+" if signed else ""
    text = f"{value:{sign}{width}.{prec}f}"
    colour = Fore.GREEN if value >= 0 else Fore.RED
    return f"{colour}{text}{Style.RESET_ALL}"


def trend_comment(summaries):
    """A short, data-driven note on which kind of coin the strategy handled best.

    We use the size of the Buy & Hold move as a rough proxy for how strongly a
    coin trended over the period, split the coins into a trending half and a
    choppy half, and compare the bot's average return in each. Because the
    strategy is gated by ADX and the higher-timeframe trend filter, it is
    expected to do relatively better on coins that actually trended.
    """
    if len(summaries) < 4:
        return "Not enough coins for a trend/chop breakdown."

    ranked = sorted(summaries, key=lambda s: abs(s["buy_hold"]), reverse=True)
    half = len(ranked) // 2
    trending, choppy = ranked[:half], ranked[half:]

    def avg(group):
        return sum(s["bot_return"] for s in group) / len(group) if group else 0.0

    avg_trend, avg_chop = avg(trending), avg(choppy)
    best = max(summaries, key=lambda s: s["bot_return"])
    worst = min(summaries, key=lambda s: s["bot_return"])

    if avg_trend > avg_chop:
        head = (f"The bot did better on strongly-trending coins "
                f"(avg {avg_trend:+.2f}%) than on choppy/range-bound ones "
                f"(avg {avg_chop:+.2f}%), which fits a strategy gated by ADX and "
                f"the higher-timeframe trend filter.")
    elif avg_chop > avg_trend:
        head = (f"The bot did better on choppy/range-bound coins "
                f"(avg {avg_chop:+.2f}%) than on strongly-trending ones "
                f"(avg {avg_trend:+.2f}%); the mean-reversion side (RSI/Bollinger) "
                f"carried more of the result this period.")
    else:
        head = "Trending and choppy coins performed about the same."

    return (f"{head} Best: {best['symbol']} ({best['bot_return']:+.2f}%); "
            f"worst: {worst['symbol']} ({worst['bot_return']:+.2f}%).")


def print_report(summaries, skipped, args, use_htf):
    """Print the config, the ranked table, the beat count, and the comment."""
    ranked = sorted(summaries, key=lambda s: s["bot_return"], reverse=True)

    print()
    print(f"{Style.BRIGHT}{Fore.CYAN}{'=' * 78}{Style.RESET_ALL}")
    print(f"{Style.BRIGHT}{Fore.CYAN} BACKTEST RESULTS{Style.RESET_ALL}")
    print(f"{Style.BRIGHT}{Fore.CYAN}{'=' * 78}{Style.RESET_ALL}")
    htf_txt = "on" if use_htf else "off"
    print(f"Timeframe: {args.timeframe}   Period: {args.days} days   "
          f"Capital: {INITIAL_CAPITAL:.0f} USDT   "
          f"Fees: {COMMISSION*100:.2f}%/side + {SLIPPAGE*100:.2f}% slip   "
          f"HTF filter: {htf_txt}")
    if EXIT_MODE == "timer":
        exit_txt = "timer (HOLD_MINUTES by strength: 45/30 STRONG/MODERATE)"
    else:
        exit_txt = f"{EXIT_MODE} (max hold {EXIT_MAX_HOLD_MIN:.0f}m"
        if EXIT_MIN_HOLD_MIN:
            exit_txt += f", min hold {EXIT_MIN_HOLD_MIN:.0f}m"
        exit_txt += ")"
    print(f"Exit mode: {exit_txt}")
    print()

    # --- 1) ranked table ---
    print(f"{Style.BRIGHT}{'#':>2}  {'Coin':<10}{'Bot %':>10}{'B&H %':>10}"
          f"{'Diff %':>10}{'Trades':>8}{'Win %':>8}{'MaxDD %':>9}{Style.RESET_ALL}")
    print(f"{'-' * 75}")
    for rank, s in enumerate(ranked, 1):
        print(f"{rank:>2}  {s['symbol']:<10}"
              f"{_coloured(s['bot_return'], 10)}"
              f"{_coloured(s['buy_hold'], 10)}"
              f"{_coloured(s['diff'], 10)}"
              f"{s['trades']:>8}"
              f"{s['win_rate']:>8.1f}"
              f"{s['max_dd']:>9.2f}")

    # --- 2) how often the bot beat Buy & Hold ---
    beat = sum(1 for s in summaries if s["bot_return"] > s["buy_hold"])
    total = len(summaries)
    print()
    print(f"{Style.BRIGHT}Bot beat Buy & Hold on {beat}/{total} coins"
          f"{Style.RESET_ALL}  "
          f"(avg bot {sum(s['bot_return'] for s in summaries)/total:+.2f}%, "
          f"avg B&H {sum(s['buy_hold'] for s in summaries)/total:+.2f}%)")

    # --- 3) general comment ---
    print()
    print(f"{Style.BRIGHT}Comment:{Style.RESET_ALL} {trend_comment(summaries)}")

    if skipped:
        print()
        print(f"{Fore.YELLOW}Skipped (no/insufficient data): "
              f"{', '.join(skipped)}{Style.RESET_ALL}")
    print()


def rebuild_ods_from_cache(cache_path, args):
    """Regenerate the .ods (and reprint the report) from the cached results, with
    no network access - handy after changing the spreadsheet styling."""
    if not os.path.exists(cache_path):
        print(f"{Fore.RED}No results cache at {cache_path}; "
              f"run a full backtest first.{Style.RESET_ALL}")
        return
    with open(cache_path, encoding="utf-8") as fh:
        cache = json.load(fh)
    meta = cache.get("meta", {})
    # Reflect the cached run's settings in the report header.
    args.timeframe = meta.get("timeframe", args.timeframe)
    args.days = meta.get("days", args.days)
    write_ods(args.output, cache["summaries"], cache["trades"])
    print_report(cache["summaries"], meta.get("skipped", []),
                 args, meta.get("use_htf", True))


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Backtest the live signal strategy across many coins."
    )
    parser.add_argument("--timeframe", default=TIMEFRAME,
                        help=f"candle timeframe, e.g. 1h or 15m (default: {TIMEFRAME})")
    parser.add_argument("--days", type=int, default=DAYS,
                        help=f"days of history to test (default: {DAYS})")
    parser.add_argument("--symbols", default=",".join(SYMBOLS),
                        help="comma-separated pairs (default: the built-in 30)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output .ods path (default: {OUTPUT_FILE} next to the script)")
    parser.add_argument("--no-htf", action="store_true",
                        help="disable the higher-timeframe trend filter")
    parser.add_argument("--rebuild-ods", action="store_true",
                        help="rebuild the .ods from the cached results only "
                             "(no download); useful after a styling change")
    parser.add_argument("--candle-cache", default=None,
                        help="path to a raw-candle cache (pickle). If it exists it "
                             "is loaded instead of downloading; if not, the download "
                             "is saved there. Lets several runs (e.g. different "
                             "CT_EXIT_MODE values) replay the EXACT same candles.")
    args = parser.parse_args()

    init(autoreset=True)   # colorama: reset colours after each print

    # Make sure the output folder (backtests/ by default) exists before any write.
    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)

    # The .json cache sits next to the .ods. It lets --rebuild-ods regenerate the
    # spreadsheet (e.g. after a styling tweak) in a second, with no re-download.
    cache_path = os.path.splitext(args.output)[0] + ".json"
    if args.rebuild_ods:
        rebuild_ods_from_cache(cache_path, args)
        return

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    use_htf = USE_HTF_FILTER and not args.no_htf
    tf_minutes = timeframe_to_minutes(args.timeframe)

    exchange = build_exchange()
    try:
        exchange.load_markets()
    except Exception:
        pass   # public OHLCV may still work; per-coin errors are handled below

    until_ms = int(time.time() * 1000)
    since_ms = until_ms - args.days * 86_400 * 1000

    # Optional raw-candle cache: load it if it already exists (skip the network so
    # every exit-mode A/B run replays the IDENTICAL candles), otherwise download and
    # save it for the next run. Keyed by file path only — keep one cache per
    # timeframe/period you compare on.
    cached_candles = {}
    save_candles = bool(args.candle_cache) and not os.path.exists(args.candle_cache)
    if args.candle_cache and os.path.exists(args.candle_cache):
        with open(args.candle_cache, "rb") as fh:
            cached_candles = pickle.load(fh)
        print(f"{Style.BRIGHT}{Fore.CYAN}Loaded cached candles for "
              f"{len(cached_candles)} coins from {args.candle_cache}{Style.RESET_ALL}\n")
    else:
        print(f"{Style.BRIGHT}{Fore.CYAN}Downloading {args.days} days of "
              f"{args.timeframe} candles for {len(symbols)} coins...{Style.RESET_ALL}\n")

    summaries = []
    all_trades = []
    skipped = []
    fetched = {}                 # symbol -> df, to write back to the cache
    for k, symbol in enumerate(symbols, 1):
        print(f"[{k:>2}/{len(symbols)}] {symbol:<11} ", end="", flush=True)
        df = cached_candles.get(symbol)
        if df is None:
            try:
                df = fetch_ohlcv_range(exchange, symbol, args.timeframe,
                                       since_ms, until_ms, tf_minutes)
            except Exception:
                df = None
        if df is None or len(df) <= MIN_CANDLES + 1:
            print(f"{Fore.YELLOW}unavailable - skipped{Style.RESET_ALL}")
            skipped.append(symbol)
            continue
        fetched[symbol] = df

        htf_trends = compute_htf_trends(df, tf_minutes, HTF_FACTOR) if use_htf \
            else ["NEUTRAL"] * len(df)
        summary, trades = run_backtest(symbol, df, htf_trends, use_htf)
        if summary is None:
            print(f"{Fore.YELLOW}not enough data - skipped{Style.RESET_ALL}")
            skipped.append(symbol)
            continue

        summaries.append(summary)
        all_trades.extend(trades)
        print(f"{len(df):>4} candles  {summary['trades']:>3} trades  "
              f"return {_coloured(summary['bot_return'], 8)}")

    # Persist the freshly downloaded candles so the next exit-mode run can replay
    # the identical data with no network access.
    if save_candles and fetched:
        os.makedirs(os.path.dirname(os.path.abspath(args.candle_cache)) or ".", exist_ok=True)
        with open(args.candle_cache, "wb") as fh:
            pickle.dump(fetched, fh)
        print(f"\n{Fore.CYAN}Saved candles for {len(fetched)} coins to "
              f"{args.candle_cache}{Style.RESET_ALL}")

    if not summaries:
        print(f"\n{Fore.RED}No coins could be backtested "
              f"(check connectivity or the symbol list).{Style.RESET_ALL}")
        return

    # Cache the raw results so the spreadsheet can be rebuilt later without
    # re-downloading or re-computing anything (see --rebuild-ods).
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({
            "meta": {"timeframe": args.timeframe, "days": args.days,
                     "use_htf": use_htf, "skipped": skipped,
                     "exit_mode": EXIT_MODE,
                     "exit_max_hold_min": EXIT_MAX_HOLD_MIN,
                     "exit_min_hold_min": EXIT_MIN_HOLD_MIN},
            "summaries": summaries,
            "trades": all_trades,
        }, fh)

    write_ods(args.output, summaries, all_trades)
    print_report(summaries, skipped, args, use_htf)


if __name__ == "__main__":
    main()
