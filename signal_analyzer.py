#!/usr/bin/env python3
"""
================================================================================
 LIVE CRYPTO SIGNAL ANALYZER  -  15-MINUTE CHART SCANNER
================================================================================

WHAT THIS PROGRAM DOES
----------------------
It connects to a public cryptocurrency exchange (Binance by default; no API key
is required for public market data) and downloads native 15-minute candles for
the 30 most popular USDT trading pairs (a higher timeframe means less noise,
which also lets the ADX regime filter work reliably). It runs six well-known
technical-analysis algorithms on each pair, but first uses ADX to decide the
market REGIME and only trusts the indicators that suit it (see below), then
ranks every coin by how many of the trusted algorithms agree on a BUY (long)
decision — the project is LONG-ONLY and never suggests a short. For each ranked
coin it also estimates a precise entry time, a
suggested exit time, and a live "exit-in" countdown measured in minutes. The
board refreshes automatically every 60 seconds.

--------------------------------------------------------------------------------
THE FIVE ALGORITHMS (WHAT THEY ARE AND WHY THEY WORK)
--------------------------------------------------------------------------------

1) RSI - RELATIVE STRENGTH INDEX (period 14)
   What it measures: the speed and size of recent price moves on a 0-100 scale.
   Why it works: when RSI drops below 30 the asset is "oversold" (sellers are
   likely exhausted and a bounce often follows); when RSI rises above 70 it is
   "overbought" (buyers are likely exhausted and a pullback often follows).
   Signal: BUY when RSI < 30, SELL when RSI > 70.

2) MACD - MOVING AVERAGE CONVERGENCE DIVERGENCE (12, 26, 9)
   What it measures: momentum, by comparing a fast (12) and a slow (26) EMA. The
   difference between them is the MACD line; a 9-period EMA of that line is the
   signal line.
   Why it works: when the MACD line crosses above the signal line, short-term
   momentum is turning up ahead of the longer trend; the opposite cross warns
   that momentum is turning down.
   Signal: BUY on a bullish cross (MACD above signal), SELL on a bearish cross.

3) BOLLINGER BANDS (period 20, 2 standard deviations)
   What it measures: volatility-based envelopes around a 20-period moving
   average.
   Why it works: price tends to revert toward its mean. A touch of the lower
   band means price is statistically stretched to the downside (a mean-reversion
   buy); a touch of the upper band means it is stretched to the upside (a sell).
   Signal: BUY when price touches/breaks the lower band, SELL at the upper band.

4) EMA CROSS - EXPONENTIAL MOVING AVERAGE CROSSOVER (9 over 21)
   What it measures: trend direction using a fast (9) and a slow (21) EMA.
   Why it works: EMAs weight recent prices more heavily, so when the fast EMA
   crosses above the slow EMA a new up-trend is starting; a cross below signals
   a new down-trend.
   Signal: BUY when fast crosses above slow, SELL when fast crosses below.

5) VOLUME SPIKE + PRICE ACTION
   What it measures: unusually large trading volume combined with candle
   direction.
   Why it works: a sudden burst of volume confirms conviction behind a move. A
   volume spike on a rising (green) candle suggests strong buying; a spike on a
   falling (red) candle suggests strong selling.
   Signal: BUY when volume > 2x the recent average on a rising candle,
           SELL when volume > 2x the recent average on a falling candle.

6) VWAP - VOLUME WEIGHTED AVERAGE PRICE (rolling, 20 candles)
   What it measures: the average price weighted by where volume actually traded
   - the level most participants paid.
   Why it works: trading above VWAP shows buyers are in control (bullish bias);
   below it, sellers are. It adds a volume-aware view the other five lack.
   Signal: BUY when price is above VWAP, SELL when below it.

--------------------------------------------------------------------------------
THE ADX REGIME FILTER (WHY THE SIX ALGORITHMS DO NOT ALL VOTE AT ONCE)
--------------------------------------------------------------------------------
The six algorithms come from two opposite schools: RSI and Bollinger Bands fade
extremes (mean reversion), while MACD, EMA cross, Volume and VWAP ride momentum
(trend following). Blindly mixing them produces contradictory signals - e.g.
buying an "oversold" RSI in the middle of a strong downtrend (catching a falling
knife). ADX (Average Directional Index) measures TREND STRENGTH (0-100, not
direction) and is used as a regime filter:
      ADX >= 25  -> TRENDING  -> trust the TREND family (MACD, EMA, VOL, VWAP);
                                 ignore the reversion family
      ADX <  20  -> RANGING   -> trust the REVERSION family (RSI, BB);
                                 ignore the trend family
      20-25      -> NEUTRAL   -> uncertain; keep all six
ADX is NOT a buy/sell vote; it only chooses which indicators are even eligible.
In the TRENDING regime there is an extra check: ADX's companion lines +DI and
-DI carry direction, so a BUY that fights the dominant line is rejected (a BUY
needs +DI at/above -DI).

--------------------------------------------------------------------------------
THE HIGHER-TIMEFRAME (1-HOUR) TREND FILTER
--------------------------------------------------------------------------------
After a 15-minute signal passes the strength bar, it must also AGREE with the
1-hour trend (read from a 20/50 EMA pair): a BUY is dropped while the 1h trend
is clearly down; a NEUTRAL or UP 1h trend allows it.
This removes counter-trend trades (the main weakness in the trade log: the
average loss was bigger than the average win). The 1h trend is only fetched for
the few coins that already produced a signal, and is shown on screen, in the
Telegram message, and in the "Trend (1s)" column of the trade log.

--------------------------------------------------------------------------------
LONG-ONLY
--------------------------------------------------------------------------------
This project is LONG-ONLY: it never opens or suggests a short. The 90-day
backtest showed shorts were a net drag and spot trading cannot truly short, so
analyze_symbol only ever returns a BUY signal — a clearly bearish coin simply
produces no signal. (The backtester is long-only for the same reason.)

--------------------------------------------------------------------------------
HOW SCORING AND TIMING WORK
--------------------------------------------------------------------------------
* Each ELIGIBLE algorithm votes BUY or nothing (its bearish reads are ignored —
  the strategy is long-only). STRENGTH uses an absolute agreement floor (the
  regime already
  narrowed the indicators to the right family, so demanding a high fraction on
  top of that starves the board):
      every eligible indicator agrees      -> STRONG
      at least CT_MIN_AGREE agree (not all)-> MODERATE
      fewer than CT_MIN_AGREE agree        -> WEAK   (hidden)
  CT_MIN_AGREE defaults to 3 (the validated high-conviction setting), so only
  coins where at least 3 eligible indicators agree are displayed. Only MODERATE
  and STRONG coins are shown, so WEAK is hidden.
* Entry time = the timestamp of the most recent candle on which a winning signal
  triggered. Candles are 15 minutes apart, so "2 candles ago" means 30 minutes
  ago.
* Exit is TREND-BASED, not a fixed timer: hold the position WHILE the 1-hour
  trend stays in its favour and exit when that trend FLIPS against it (a long
  exits when the 1h trend turns DOWN). A max-hold cap (CT_LIVE_MAX_HOLD, default
  24h) is the safety backstop and the close time written to the trade log.
* Each agreeing signal is shown with its measured value rather than a plain
  check mark, so the strength of every contributor is visible: the RSI level
  (e.g. RSI 24), the MACD histogram and EMA gap as a percentage of price, how
  far price pushed past a Bollinger band (percentage), and the volume multiple
  (e.g. VOL 3.2x).

DISCLAIMER: This tool is for educational and informational purposes only. It is
not financial advice. Always do your own research before trading.
================================================================================
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import ccxt
import pandas as pd
from colorama import Fore, Style, init
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import BollingerBands
from ta.volume import VolumeWeightedAveragePrice

from trade_logger import TradeLogger
from telegram_notifier import TelegramNotifier
import performance


# ----------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------
EXCHANGE_ID = "binance"      # any public ccxt exchange id works here

# Analysis timeframe. Binance HAS a native 15-minute interval, so it is fetched
# directly (no resampling needed). A higher timeframe means less noise, which
# both improves signal quality and lets the ADX regime filter (below) work
# reliably - ADX whipsaws too much on very short timeframes like 5m.
BASE_TIMEFRAME = "15m"       # native timeframe fetched from the exchange
RESAMPLE_MINUTES = 15        # analysis timeframe
TIMEFRAME_LABEL = "15MIN"    # shown in the header
CANDLE_LIMIT = 100           # number of analysis (15-minute) candles to keep
# How many native base candles merge into one analysis candle. When the base
# timeframe already equals the analysis timeframe (the 15m case) this is 1 and
# no resampling happens; the code still supports a smaller base + resampling.
BASE_MINUTES = int(BASE_TIMEFRAME.rstrip("m"))
RESAMPLE_RATIO = max(1, RESAMPLE_MINUTES // BASE_MINUTES)
BASE_LIMIT = CANDLE_LIMIT * RESAMPLE_RATIO

REFRESH_SECONDS = 60         # auto-refresh interval
MAX_WORKERS = 8              # parallel download threads
MIN_CANDLES = 30             # minimum history needed for the slow indicators

# Every displayed signal is appended to this OpenDocument spreadsheet (.ods),
# in the same column layout as the user's Template.ods. See trade_logger.py.
TRADE_LOG_FILE = "trade_log.ods"

# If the most recent candle is older than this, the pair is treated as
# inactive or delisted (e.g. a migrated/renamed token) and is skipped so it
# cannot produce stale prices or nonsensical timing. Scaled to the timeframe so
# a normal forming 15-minute candle is never mistaken for stale.
MAX_CANDLE_AGE_MINUTES = RESAMPLE_MINUTES + 10

# Indicator parameters.
RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

BB_PERIOD = 20
BB_STD = 2

EMA_FAST, EMA_SLOW = 9, 21

VOL_WINDOW = 20              # candles used for the average-volume baseline
# A spike is volume > this multiple of the baseline. Higher = fewer, higher-
# conviction volume signals; the backtest can override via CT_VOL_MULT (see the
# conviction-gate block below for why; _env_float is defined there).
VOL_MULTIPLIER = 2.0

VWAP_WINDOW = 20             # candles for the rolling volume-weighted average price


# ---------------------------------------------------------------------------
# LONG-SIDE CONVICTION GATES (expectancy tuning, 2026-06)
# ---------------------------------------------------------------------------
# The 90-day backtest showed the strategy is a LOW-WIN / HIGH-PAYOFF trend
# follower, and that the two mean-reversion indicators (RSI, Bollinger Bands)
# LOSE money on the long side at every value, while the trend indicators (VWAP,
# MACD, VOL) are net-positive and get STRONGER the further past their threshold
# they fire. These gates act on that: they let us drop the losing reversion
# longs and demand more conviction from the trend votes, lifting expectancy.
#
# Every gate is overridable by an environment variable so the backtest can A/B
# test a setting without editing this file (baseline = leave them unset, which
# reproduces the original behaviour). To bake a winning value in, just change
# the default below.
def _env_bool(name, default):
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# These four LONG-side conviction gates now DEFAULT to the validated "P1A3"
# config (2026-06-02 backtest): the only setting that turned per-trade expectancy
# positive (+0.098%/trade, beat Buy&Hold on 20/30 coins over 90 days). They stay
# env-overridable — set them back to the neutral values (CT_REVERSION_LONG=1,
# CT_VWAP_MIN=0, CT_MACD_MIN=0, CT_VOL_MULT=2.0, CT_MIN_AGREE=2) to reproduce the
# older, looser board. Effect: the live board is now FAR more selective (it may
# be empty most of the time — that is expected, these are rare high-quality setups).
#
# Allow RSI / Bollinger BUY (oversold / lower-band) signals to count? Default OFF:
# the mean-reversion longs lose at every value in the test, so they are dropped.
REVERSION_LONG_ENABLED = _env_bool("CT_REVERSION_LONG", False)
# A VWAP BUY only counts when price is at least this % ABOVE VWAP.
VWAP_LONG_MIN_DIST_PCT = _env_float("CT_VWAP_MIN", 1.5)
# A MACD signal only counts when the histogram is at least this % of price.
MACD_MIN_HIST_PCT = _env_float("CT_MACD_MIN", 0.08)
# Volume-spike multiple: a higher bar means fewer, higher-conviction volume signals.
VOL_MULTIPLIER = _env_float("CT_VOL_MULT", 3.8)

# LONG-ONLY (permanent). This project never opens or suggests a short: the 90-day
# backtest showed shorts were a net drag and spot trading cannot truly short.
# analyze_symbol() only ever votes BUY (the SELL side of the indicator votes is
# discarded), so a clearly bearish coin simply yields no signal. The backtester
# is long-only for the same reason.

# ADX (Average Directional Index) - the REGIME FILTER, not a buy/sell vote.
# ADX measures TREND STRENGTH (0-100), not direction. It decides which family
# of indicators to trust, so the strategy stops fighting itself (e.g. buying an
# "oversold" RSI in the middle of a strong downtrend):
#   * ADX >= TREND threshold -> trending market  -> trust the TREND family,
#                                                    ignore the reversion family
#   * ADX <  RANGE threshold -> sideways market   -> trust the REVERSION family,
#                                                    ignore the trend family
#   * in between (transition) -> uncertain regime -> keep all indicators
ADX_PERIOD = 14
ADX_TREND_THRESHOLD = 25     # at/above this, the market is trending
ADX_RANGE_THRESHOLD = 20     # below this, the market is ranging/sideways

# Which indicators belong to each regime family.
#   TREND family rides momentum; REVERSION family fades extremes.
TREND_FAMILY = ["MACD", "EMA", "VOL", "VWAP"]
REVERSION_FAMILY = ["RSI", "BB"]

# Minimum number of ELIGIBLE indicators that must agree for a coin to be shown.
# The regime filter already narrows the indicators to the right family, so this
# is an absolute conviction floor (not a fraction): demanding a high fraction on
# top of the regime split double-filters and starves the board of signals.
# Overridable via CT_MIN_AGREE. Default is now 3 (the validated high-conviction
# setting from the 2026-06-02 backtest): require at least 3 eligible indicators to
# agree. Set CT_MIN_AGREE=2 to reproduce the older, looser board.
MIN_AGREE = int(_env_float("CT_MIN_AGREE", 3))

# HIGHER-TIMEFRAME (HTF) TREND FILTER.
# Before accepting a 15-minute signal, the program checks the trend on a much
# higher timeframe (1 hour) and rejects any signal that fights it - e.g. a BUY
# while the 1h trend is clearly down. This cuts counter-trend losers, the main
# weakness seen in the trade log (average loss bigger than average win). The 1h
# trend is read from two EMAs: UP when the fast EMA is above the slow one by more
# than a small dead-band, DOWN when below it, NEUTRAL in between (no filtering).
HTF_TIMEFRAME = "1h"         # the higher timeframe used for the trend context
HTF_LIMIT = 100              # 1h candles to download (enough for the slow EMA)
HTF_EMA_FAST = 20            # ~20 hours
HTF_EMA_SLOW = 50            # ~50 hours
HTF_NEUTRAL_BAND = 0.10      # EMA gap (% of price) under which the 1h trend is NEUTRAL

# A crossover or volume spike only counts as a live trigger if it happened
# within this many candles, which keeps the entry/exit timing meaningful.
# 1 candle on the 15-minute chart equals the last 15 minutes, so a shown signal
# triggered at most ~15 minutes ago - this keeps entries fresh (less drift
# between the trigger and entering "now"), at the cost of fewer signals.
CROSS_FRESHNESS_CANDLES = 1

# Minutes to hold a position for each strength tier. NOTE: the LIVE exit is now
# TREND-BASED (see render_coin), so these per-tier timers are used only by the
# backtest's `timer` exit mode (kept as the A/B control). backtest.py imports
# HOLD_MINUTES, so do not remove it.
HOLD_MINUTES = {"STRONG": 45, "MODERATE": 30, "WEAK": 15}

# The LIVE strategy now exits on a 1h-trend flip (the validated `htf` exit), to
# hold winners instead of cutting them at a fixed timer. This is the SAFETY CAP:
# the longest a position is ever suggested to be held, and the close time (Kap
# Saat) written to the trade log so its auto-close still works (the earlier
# trend-flip exit is recorded by hand). Env-overridable; matches the backtest's
# CT_EXIT_MAX_HOLD default.
MAX_HOLD_MINUTES = int(_env_float("CT_LIVE_MAX_HOLD", 1440))   # 24h

# Visual tags per strength tier.
STRENGTH_EMOJI = {"STRONG": "\U0001F525", "MODERATE": "⚡", "WEAK": "\U0001F4CA"}

# The 30 most popular USDT trading pairs to analyze.
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "POL/USDT",
    "LTC/USDT", "SHIB/USDT", "TRX/USDT", "LINK/USDT", "UNI/USDT",
    "ATOM/USDT", "XLM/USDT", "ETC/USDT", "BCH/USDT", "APT/USDT",
    "FIL/USDT", "NEAR/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT",
    "SUI/USDT", "TON/USDT", "HBAR/USDT", "VET/USDT", "ALGO/USDT",
]

# A neutral (no-signal) result used by every indicator function.
# "detail" carries the measured indicator value for the signal-quality display
# (e.g. the RSI level or the volume multiple); it is None when there is no signal.
NEUTRAL = {"signal": None, "candles_ago": None, "detail": None}


# ----------------------------------------------------------------------------
# SMALL TIMING HELPERS
# ----------------------------------------------------------------------------
def state_entry_ago(condition):
    """Return how many candles ago the current True-run of a condition began.

    `condition` is a boolean Series. If the most recent value is True, this
    returns the number of candles since that run started (0 means it started on
    the latest candle). Returns None if the latest value is not True.
    """
    if len(condition) == 0 or not bool(condition.iloc[-1]):
        return None
    ago = 0
    i = len(condition) - 1
    while i - 1 >= 0 and bool(condition.iloc[i - 1]):
        ago += 1
        i -= 1
    return ago


def latest_cross(diff, freshness):
    """Find the most recent crossover of zero within `freshness` candles.

    `diff` is a Series equal to (fast - slow). A move from <= 0 to > 0 is a
    bullish (BUY) cross; a move from >= 0 to < 0 is a bearish (SELL) cross.
    Returns a ("BUY"|"SELL", candles_ago) tuple, or (None, None) if no recent
    cross exists.
    """
    n = len(diff)
    for ago in range(0, freshness + 1):
        i = n - 1 - ago
        if i < 1:
            break
        prev = diff.iloc[i - 1]
        cur = diff.iloc[i]
        if pd.isna(prev) or pd.isna(cur):
            continue
        if prev <= 0 and cur > 0:
            return "BUY", ago
        if prev >= 0 and cur < 0:
            return "SELL", ago
    return None, None


# ----------------------------------------------------------------------------
# INDICATORS - ONE FUNCTION PER ALGORITHM
# Each returns {"signal": "BUY"|"SELL"|None, "candles_ago": int|None}.
# ----------------------------------------------------------------------------
def check_rsi(close):
    """RSI: BUY when oversold (< 30), SELL when overbought (> 70).

    Detail is the RSI level itself (0-100); the further past the threshold, the
    stronger the oversold/overbought reading.
    """
    rsi = RSIIndicator(close=close, window=RSI_PERIOD).rsi()
    last = rsi.iloc[-1]
    if pd.isna(last):
        return dict(NEUTRAL)
    if last < RSI_OVERSOLD and REVERSION_LONG_ENABLED:
        return {
            "signal": "BUY",
            "candles_ago": state_entry_ago(rsi < RSI_OVERSOLD) or 0,
            "detail": f"RSI {last:.0f}",
        }
    if last > RSI_OVERBOUGHT:
        return {
            "signal": "SELL",
            "candles_ago": state_entry_ago(rsi > RSI_OVERBOUGHT) or 0,
            "detail": f"RSI {last:.0f}",
        }
    return dict(NEUTRAL)


def check_macd(close):
    """MACD: BUY on a bullish line/signal cross, SELL on a bearish cross."""
    macd = MACD(
        close=close,
        window_slow=MACD_SLOW,
        window_fast=MACD_FAST,
        window_sign=MACD_SIGNAL,
    )
    diff = macd.macd_diff()  # MACD line minus signal line (the histogram)
    signal, ago = latest_cross(diff, CROSS_FRESHNESS_CANDLES)
    if signal is None:
        return dict(NEUTRAL)
    # Histogram magnitude as a percentage of price, so it is comparable across
    # coins of very different prices. A bigger bar means stronger momentum.
    price = close.iloc[-1]
    hist_pct = abs(diff.iloc[-1]) / price * 100 if price else 0.0
    # Conviction gate: ignore a weak cross whose histogram is below the floor.
    if hist_pct < MACD_MIN_HIST_PCT:
        return dict(NEUTRAL)
    return {"signal": signal, "candles_ago": ago, "detail": f"MACD {hist_pct:.2f}%"}


def check_bollinger(close):
    """Bollinger Bands: BUY at the lower band, SELL at the upper band.

    Detail is how far price has pushed past the band, as a percentage of price;
    a larger value means a more stretched (higher-conviction) mean-reversion.
    """
    bands = BollingerBands(close=close, window=BB_PERIOD, window_dev=BB_STD)
    upper = bands.bollinger_hband()
    lower = bands.bollinger_lband()
    last = close.iloc[-1]
    if pd.isna(upper.iloc[-1]) or pd.isna(lower.iloc[-1]):
        return dict(NEUTRAL)
    if last <= lower.iloc[-1] and REVERSION_LONG_ENABLED:
        past_pct = (lower.iloc[-1] - last) / last * 100 if last else 0.0
        return {
            "signal": "BUY",
            "candles_ago": state_entry_ago(close <= lower) or 0,
            "detail": f"BB {past_pct:.2f}%",
        }
    if last >= upper.iloc[-1]:
        past_pct = (last - upper.iloc[-1]) / last * 100 if last else 0.0
        return {
            "signal": "SELL",
            "candles_ago": state_entry_ago(close >= upper) or 0,
            "detail": f"BB {past_pct:.2f}%",
        }
    return dict(NEUTRAL)


def check_ema_cross(close):
    """EMA cross: BUY when fast (9) crosses above slow (21), SELL on the reverse.

    Detail is the gap between the two EMAs as a percentage of price; a wider gap
    means a more decisive trend separation.
    """
    fast = EMAIndicator(close=close, window=EMA_FAST).ema_indicator()
    slow = EMAIndicator(close=close, window=EMA_SLOW).ema_indicator()
    diff = fast - slow
    signal, ago = latest_cross(diff, CROSS_FRESHNESS_CANDLES)
    if signal is None:
        return dict(NEUTRAL)
    price = close.iloc[-1]
    gap_pct = abs(diff.iloc[-1]) / price * 100 if price else 0.0
    return {"signal": signal, "candles_ago": ago, "detail": f"EMA {gap_pct:.2f}%"}


def check_vwap(df):
    """VWAP: BUY when price trades above the rolling volume-weighted average
    price, SELL when below it.

    Why it works: VWAP is the average price weighted by where the volume
    actually traded, so it is the level most participants paid. Holding above it
    shows buyers are in control (a bullish bias); below it, sellers are. Detail
    is the distance from VWAP as a percentage of price; "candles_ago" is how long
    price has stayed on its current side of VWAP.
    """
    close = df["close"]
    vwap = VolumeWeightedAveragePrice(
        high=df["high"],
        low=df["low"],
        close=close,
        volume=df["volume"],
        window=VWAP_WINDOW,
    ).volume_weighted_average_price()
    last_vwap = vwap.iloc[-1]
    price = close.iloc[-1]
    if pd.isna(last_vwap) or not price:
        return dict(NEUTRAL)
    dist_pct = (price - last_vwap) / price * 100
    # Conviction gate: a BUY only counts when price is at least this % above VWAP
    # (the test showed VWAP longs pay off mainly once price is clearly above it).
    if price > last_vwap and dist_pct >= VWAP_LONG_MIN_DIST_PCT:
        return {
            "signal": "BUY",
            "candles_ago": state_entry_ago(close > vwap) or 0,
            "detail": f"VWAP {dist_pct:+.2f}%",
        }
    if price < last_vwap:
        return {
            "signal": "SELL",
            "candles_ago": state_entry_ago(close < vwap) or 0,
            "detail": f"VWAP {dist_pct:+.2f}%",
        }
    return dict(NEUTRAL)


def check_volume(df):
    """Volume spike + price action: BUY on a spike with a rising candle,
    SELL on a spike with a falling candle."""
    volume = df["volume"]
    close = df["close"]
    open_ = df["open"]
    # Average volume of the prior VOL_WINDOW candles (excludes the current one).
    baseline = volume.rolling(VOL_WINDOW).mean().shift(1)
    n = len(df)
    for ago in range(0, CROSS_FRESHNESS_CANDLES + 1):
        i = n - 1 - ago
        if i < VOL_WINDOW:
            break
        avg = baseline.iloc[i]
        if pd.isna(avg) or avg <= 0:
            continue
        if volume.iloc[i] > VOL_MULTIPLIER * avg:
            rising = close.iloc[i] >= open_.iloc[i]
            multiple = volume.iloc[i] / avg
            return {
                "signal": "BUY" if rising else "SELL",
                "candles_ago": ago,
                "detail": f"VOL {multiple:.1f}x",
            }
    return dict(NEUTRAL)


# ----------------------------------------------------------------------------
# ADX REGIME FILTER (NOT A BUY/SELL VOTE)
# ----------------------------------------------------------------------------
def compute_adx_di(df):
    """Return (ADX, +DI, -DI) for the latest candle, or (None, None, None).

    ADX measures trend STRENGTH (not direction). Its companion lines do carry
    direction: +DI rising above -DI means upward movement dominates, and the
    reverse means downward movement dominates. These are used to confirm that a
    trend-regime signal is not fighting the dominant directional movement.
    """
    if len(df) < ADX_PERIOD * 2:
        return None, None, None
    indicator = ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"], window=ADX_PERIOD
    )

    def last_of(series):
        value = series.iloc[-1]
        return None if pd.isna(value) else float(value)

    return (
        last_of(indicator.adx()),
        last_of(indicator.adx_pos()),   # +DI
        last_of(indicator.adx_neg()),   # -DI
    )


def compute_adx(df):
    """Return just the latest ADX value (trend strength, 0-100) or None."""
    return compute_adx_di(df)[0]


def di_confirms(plus_di, minus_di):
    """True unless the dominant DI line disagrees with a BUY (long-only).

    Used only in the TREND regime: a BUY needs +DI at or above -DI. Missing DI
    data does not filter (returns True).
    """
    if plus_di is None or minus_di is None:
        return True
    return plus_di >= minus_di


def classify_regime(adx_value):
    """Map an ADX value to a market regime: TREND, RANGE or NEUTRAL.

    NEUTRAL covers both the transition band and a missing ADX, and means "do not
    filter" - all indicators are kept.
    """
    if adx_value is None:
        return "NEUTRAL"
    if adx_value >= ADX_TREND_THRESHOLD:
        return "TREND"
    if adx_value < ADX_RANGE_THRESHOLD:
        return "RANGE"
    return "NEUTRAL"


def allowed_indicators(regime):
    """Return the list of indicator names trusted in the given regime."""
    if regime == "TREND":
        return list(TREND_FAMILY)
    if regime == "RANGE":
        return list(REVERSION_FAMILY)
    return TREND_FAMILY + REVERSION_FAMILY  # NEUTRAL: keep all five


# ----------------------------------------------------------------------------
# HIGHER-TIMEFRAME TREND FILTER
# ----------------------------------------------------------------------------
def fetch_htf_trend(exchange, symbol):
    """Return the 1-hour trend for one symbol: "UP", "DOWN" or "NEUTRAL".

    Read from a fast/slow EMA pair on the higher timeframe. Any failure returns
    NEUTRAL, which means "do not filter" - a data hiccup must not silently drop
    every signal. This is only called for the handful of coins that already
    produced a 15-minute signal, so it adds very few extra requests.
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=HTF_TIMEFRAME, limit=HTF_LIMIT)
        if not raw or len(raw) < HTF_EMA_SLOW + 5:
            return "NEUTRAL"
        closes = pd.Series([candle[4] for candle in raw], dtype=float)
        fast = EMAIndicator(close=closes, window=HTF_EMA_FAST).ema_indicator()
        slow = EMAIndicator(close=closes, window=HTF_EMA_SLOW).ema_indicator()
        fast_last, slow_last = fast.iloc[-1], slow.iloc[-1]
        if pd.isna(fast_last) or pd.isna(slow_last) or not slow_last:
            return "NEUTRAL"
        gap_pct = (fast_last - slow_last) / slow_last * 100
        if gap_pct > HTF_NEUTRAL_BAND:
            return "UP"
        if gap_pct < -HTF_NEUTRAL_BAND:
            return "DOWN"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def htf_allows(trend, direction):
    """True unless a BUY fights a clear higher-timeframe DOWN trend (long-only).

    A NEUTRAL or UP 1h trend allows the BUY (no filtering).
    """
    if trend == "DOWN" and direction == "BUY":
        return False
    return True


# ----------------------------------------------------------------------------
# DATA FETCHING (PARALLEL, WITH PER-COIN ERROR ISOLATION)
# ----------------------------------------------------------------------------
def resample_ohlcv(df, minutes):
    """Merge candles into a higher timeframe (e.g. 5-minute into 10-minute).

    Returns a DataFrame with the same columns (timestamp in epoch ms, then
    open/high/low/close/volume), aligned to clock boundaries.
    """
    indexed = df.copy()
    indexed.index = pd.to_datetime(indexed["timestamp"], unit="ms")
    merged = (
        indexed.resample(f"{minutes}min", label="left", closed="left")
        .agg(
            {
                "timestamp": "first",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    merged["timestamp"] = merged["timestamp"].astype("int64")
    return merged.reset_index(drop=True)


def fetch_ohlcv_safe(exchange, symbol):
    """Download OHLCV candles for one symbol. Returns a DataFrame or None.

    Any failure (network error, delisted pair, rate limit) is swallowed so that
    a single bad coin never stops the rest of the scan.
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=BASE_TIMEFRAME, limit=BASE_LIMIT)
        if not raw or len(raw) < MIN_CANDLES:
            return None
        # Reject stale data from inactive/delisted pairs: the most recent candle
        # must be fresh. Timestamps are epoch milliseconds (UTC), so this
        # comparison is timezone-independent.
        age_minutes = (time.time() * 1000 - raw[-1][0]) / 60000
        if age_minutes > MAX_CANDLE_AGE_MINUTES:
            return None
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        # Merge into the analysis timeframe only when the base candles are
        # smaller than it (RESAMPLE_RATIO > 1). With native 15m data the ratio
        # is 1, so the candles are already in the right timeframe.
        if RESAMPLE_RATIO > 1:
            df = resample_ohlcv(df, RESAMPLE_MINUTES)
        if len(df) < MIN_CANDLES:
            return None
        return df
    except Exception:
        return None


def fetch_all(exchange, symbols):
    """Fetch every symbol in parallel and render a live loading bar."""
    results = {}
    total = len(symbols)
    done = 0
    print_loading_bar(done, total)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_ohlcv_safe, exchange, s): s for s in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception:
                results[symbol] = None
            done += 1
            print_loading_bar(done, total)
    print()  # move off the loading-bar line
    return results


# ----------------------------------------------------------------------------
# ANALYSIS - COMBINE THE SIX SIGNALS INTO ONE RANKED RECOMMENDATION
# ----------------------------------------------------------------------------
def analyze_symbol(symbol, df):
    """Run the algorithms allowed by the ADX regime and combine them.

    The ADX regime filter decides which indicator family is trusted, so only the
    indicators that suit the current market are counted. Returns a result dict,
    or None when there is no signal at all.
    """
    if df is None or len(df) < MIN_CANDLES:
        return None

    close = df["close"]

    # Decide the market regime first, then only run the indicators it trusts.
    adx_value, plus_di, minus_di = compute_adx_di(df)
    regime = classify_regime(adx_value)
    allowed = allowed_indicators(regime)

    runners = {
        "RSI": lambda: check_rsi(close),
        "MACD": lambda: check_macd(close),
        "BB": lambda: check_bollinger(close),
        "EMA": lambda: check_ema_cross(close),
        "VOL": lambda: check_volume(df),
        "VWAP": lambda: check_vwap(df),
    }
    checks = {name: runners[name]() for name in allowed}

    # Long-only: only BUY votes count. A coin whose eligible indicators are
    # bearish (or silent) simply produces no signal — the project never shorts.
    direction = "BUY"
    group = [name for name, res in checks.items() if res["signal"] == "BUY"]
    if not group:
        return None

    # +DI/-DI directional confirmation: in a trend, reject a BUY that fights the
    # dominant directional movement (i.e. -DI is on top).
    if regime == "TREND" and not di_confirms(plus_di, minus_di):
        return None

    # Keep contributing indicators in a stable display order.
    order = ["RSI", "MACD", "BB", "EMA", "VOL", "VWAP"]
    contributors = [name for name in order if name in group]

    # The measured value behind each agreeing signal, for the quality display.
    details = {name: checks[name]["detail"] for name in contributors}

    # The headline entry time uses the freshest of the agreeing signals.
    candles_ago = min(checks[name]["candles_ago"] for name in contributors)
    trigger_index = len(df) - 1 - candles_ago
    trigger_ms = int(df["timestamp"].iloc[trigger_index])
    trigger_time = datetime.fromtimestamp(trigger_ms / 1000)

    score = len(contributors)
    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        # How many indicators were even eligible this regime, so strength can be
        # judged as a fraction (3/3 in a trend is as strong as 5/5 normally).
        "available": len(allowed),
        "strength": strength_for(score, len(allowed)),
        "contributors": contributors,
        "details": details,
        "candles_ago": candles_ago,
        "price": float(close.iloc[-1]),
        "trigger_time": trigger_time,
        "adx": adx_value,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "regime": regime,
    }


def strength_for(score, available):
    """Map a score to a strength tier using an absolute agreement floor.

    The regime filter has already restricted the indicators to the family that
    suits the market, so strength is judged on how many of those eligible
    indicators agree, with a floor of MIN_AGREE:
        fewer than MIN_AGREE agree   -> WEAK   (hidden)
        every eligible one agrees    -> STRONG
        at least MIN_AGREE (not all) -> MODERATE
    Examples: trend regime 2/4 MODERATE, 4/4 STRONG; range regime 2/2 STRONG.
    """
    if score < MIN_AGREE:
        return "WEAK"
    if available and score >= available:   # every eligible indicator agrees
        return "STRONG"
    return "MODERATE"


# ----------------------------------------------------------------------------
# TERMINAL OUTPUT
# ----------------------------------------------------------------------------
def clear_screen():
    """Clear the terminal on both Windows and Unix-like systems."""
    os.system("cls" if os.name == "nt" else "clear")


def make_box(lines, width=60):
    """Build a centered, fixed-width box around the given text lines."""
    top = "╔" + "═" * width + "╗"
    bottom = "╚" + "═" * width + "╝"
    middle = ["║" + line.center(width) + "║" for line in lines]
    return [top] + middle + [bottom]


def format_price(price):
    """Format a price with a sensible number of decimals for its magnitude."""
    if price >= 1:
        return f"${price:,.2f}"
    if price >= 0.01:
        return f"${price:,.4f}"
    return f"${price:,.8f}"


def print_loading_bar(done, total, prefix="Fetching live market data"):
    """Print/redraw a single-line progress bar while data is downloading."""
    bar_len = 30
    fraction = (done / total) if total else 1.0
    filled = int(round(bar_len * fraction))
    bar = "█" * filled + "░" * (bar_len - filled)
    pct = int(round(100 * fraction))
    print(
        f"\r{Fore.CYAN}{prefix} {Style.RESET_ALL}"
        f"{Fore.GREEN}[{bar}]{Style.RESET_ALL} {done}/{total} ({pct}%)",
        end="",
        flush=True,
    )


def render_coin(result, now):
    """Print one coin's recommendation block."""
    symbol = result["symbol"]
    direction = result["direction"]
    score = result["score"]
    available = result.get("available", 6)
    strength = result["strength"]
    emoji = STRENGTH_EMOJI[strength]
    price_str = format_price(result["price"])

    trigger = result["trigger_time"]
    minutes_ago = max(0, round((now - trigger).total_seconds() / 60))
    # Timing is anchored to "now": enter at the current time. The exit is no
    # longer a fixed timer — it is TREND-BASED (hold while the 1h trend stays in
    # the trade's favour, exit when it flips against it), with a max-hold cap as
    # the safety backstop. cap_exit is that latest-possible exit time.
    cap_exit = now + timedelta(minutes=MAX_HOLD_MINUTES)

    # Show the measured value behind each agreeing signal (e.g. "RSI 24",
    # "VOL 3.2x") so the strength of each contributor is visible at a glance.
    details = result.get("details", {})
    signal_line = " │ ".join(
        f"{Fore.GREEN}{details.get(name) or name}{Style.RESET_ALL}"
        for name in result["contributors"]
    )
    dir_color = Fore.GREEN   # long-only: every signal is a BUY
    head_style = Style.BRIGHT if strength == "STRONG" else Style.NORMAL

    # The market regime that was in force (from the ADX filter) and the aligned
    # higher-timeframe (1h) trend.
    regime = result.get("regime", "NEUTRAL")
    adx = result.get("adx")
    adx_text = f"ADX {adx:.0f}" if adx is not None else "ADX n/a"
    # In a trend, show which DI line confirmed the direction.
    plus_di, minus_di = result.get("plus_di"), result.get("minus_di")
    if regime == "TREND" and plus_di is not None and minus_di is not None:
        adx_text += " · +DI>−DI" if plus_di >= minus_di else " · −DI>+DI"
    htf_trend = result.get("htf_trend", "NEUTRAL")
    htf_arrow = {"UP": "↑", "DOWN": "↓"}.get(htf_trend, "→")

    print(
        f"{head_style}{dir_color}{emoji} {strength} {direction}{Style.RESET_ALL}"
        f" │ {Fore.CYAN}{symbol}{Style.RESET_ALL}"
        f" │ {Fore.YELLOW}{price_str}{Style.RESET_ALL}"
        f" │ {score}/{available} signals"
    )
    print(f"{'Triggered':<9} : {trigger:%H:%M:%S}  ({minutes_ago} min ago)")
    print(f"{'Entry':<9} : {now:%H:%M:%S}  (enter now)")
    hold_word = "↑ UP"      # long-only: hold while the 1h uptrend lasts
    flip_word = "↓ DOWN"
    print(
        f"{'Exit':<9} : {Fore.GREEN}hold while 1h trend {hold_word}"
        f" — exit when it flips {flip_word}{Style.RESET_ALL}"
    )
    print(
        f"{'':<9}   latest exit {cap_exit:%H:%M} "
        f"({Fore.YELLOW}max-hold {MAX_HOLD_MINUTES // 60}h cap{Style.RESET_ALL})"
    )
    print(
        f"{'Regime':<9} : {Fore.MAGENTA}{regime}{Style.RESET_ALL} ({adx_text})"
        f"  │  1h trend: {Fore.MAGENTA}{htf_arrow} {htf_trend}{Style.RESET_ALL}"
    )
    print(f"{'Signals':<9} : {signal_line}")


def render(results, scanned_count, unavailable=0):
    """Render the full board: header, ranked coins, and a legend."""
    now = datetime.now()
    for line in make_box(
        [
            f"LIVE CRYPTO SIGNAL ANALYZER  -  {TIMEFRAME_LABEL} CHART",
            f"Last update: {now:%H:%M:%S}",
        ]
    ):
        print(f"{Style.BRIGHT}{Fore.CYAN}{line}{Style.RESET_ALL}")
    print()

    if not results:
        print(
            f"{Fore.YELLOW}No active signals right now. "
            f"Waiting for the next refresh...{Style.RESET_ALL}"
        )
    else:
        summary = (
            f"Scanned {scanned_count} coins  •  "
            f"{len(results)} with signals"
        )
        if unavailable:
            summary += f"  •  {unavailable} unavailable"
        summary += "  •  sorted by strength"
        print(summary)
        print()
        for result in results:
            render_coin(result, now)
            print()

    print(
        f"{Style.DIM}Legend: {STRENGTH_EMOJI['STRONG']} STRONG (all agree)   "
        f"{STRENGTH_EMOJI['MODERATE']} MODERATE (>={MIN_AGREE})   │   "
        f"GREEN = BUY (long-only)   │   Regime via ADX{Style.RESET_ALL}"
    )


def refresh_countdown(seconds):
    """Show a single-line, live countdown until the next refresh."""
    for remaining in range(seconds, 0, -1):
        print(
            f"\r{Fore.MAGENTA}[ Refreshing in {remaining:2d} seconds... ]"
            f"{Style.RESET_ALL}   ",
            end="",
            flush=True,
        )
        time.sleep(1)
    print()


# ----------------------------------------------------------------------------
# MAIN LOOP
# ----------------------------------------------------------------------------
def build_exchange():
    """Create a public ccxt exchange instance (no API key needed)."""
    exchange_class = getattr(ccxt, EXCHANGE_ID)
    return exchange_class({"enableRateLimit": True})


def scan(exchange, notifier=None):
    """Fetch and analyze every symbol; return (ranked results, unavailable count).

    This is the read-only core shared by the live board and the Telegram /tara
    command. It raises on a total fetch failure (after alerting via Telegram).
    """
    try:
        data = fetch_all(exchange, SYMBOLS)
    except Exception as error:
        # A total fetch failure (e.g. the network is down) is a critical error:
        # alert via Telegram and let the caller decide whether to retry.
        if notifier is not None:
            notifier.notify_error("Market data download (fetch_all)", error)
        raise

    results = []
    unavailable = 0
    for symbol, df in data.items():
        if df is None:
            unavailable += 1
            continue
        try:
            recommendation = analyze_symbol(symbol, df)
        except Exception:
            recommendation = None
        # Keep only coins whose agreeing indicators clear the strength bar
        # (MODERATE or STRONG, i.e. not WEAK) AND have a recent trigger, so every
        # shown signal is both high-conviction and fresh enough to act on now.
        # (analyze_symbol is long-only, so every recommendation is already a BUY.)
        if (
            recommendation
            and recommendation["strength"] != "WEAK"
            and recommendation["candles_ago"] <= CROSS_FRESHNESS_CANDLES
        ):
            results.append(recommendation)

    # Higher-timeframe alignment: for each candidate, look up the 1h trend and
    # drop a BUY that fights it (the 1h trend is clearly down). Only the few
    # candidates are checked, so this adds very few extra requests.
    aligned = []
    for recommendation in results:
        trend = fetch_htf_trend(exchange, recommendation["symbol"])
        recommendation["htf_trend"] = trend
        if not htf_allows(trend, recommendation["direction"]):
            continue
        aligned.append(recommendation)
    results = aligned

    # Rank by strength tier first, then by raw agreement, then by freshness.
    rank = {"STRONG": 2, "MODERATE": 1, "WEAK": 0}
    results.sort(
        key=lambda r: (-rank[r["strength"]], -r["score"], r["candles_ago"])
    )
    return results, unavailable


def run_cycle(exchange, clear=True, logger=None, notifier=None):
    """Run a single scan: fetch, analyze, rank, display, log to .ods, and notify."""
    if clear:
        clear_screen()
    print(
        f"{Style.BRIGHT}{Fore.CYAN}"
        f"Loading {RESAMPLE_MINUTES}-minute market data for "
        f"{len(SYMBOLS)} coins...{Style.RESET_ALL}"
    )
    results, unavailable = scan(exchange, notifier)

    if clear:
        clear_screen()
    render(results, scanned_count=len(SYMBOLS), unavailable=unavailable)

    # Maintain the trade log (.ods): first fill in the outcome of any trade
    # whose suggested exit has passed, then append the freshly displayed ones.
    if logger is not None:
        now = datetime.now()
        try:
            closed = logger.update_closed_trades(exchange, now)
            if closed:
                noun = "trade" if closed == 1 else "trades"
                print(
                    f"\n{Fore.MAGENTA}Closed {closed} {noun} "
                    f"(filled close price + result){Style.RESET_ALL}"
                )
        except Exception as error:
            print(f"\n{Fore.YELLOW}Could not update trade log: {error}{Style.RESET_ALL}")
        if results:
            try:
                added = logger.log(results, now)
                if added:
                    noun = "trade" if added == 1 else "trades"
                    print(
                        f"\n{Fore.CYAN}Logged {added} new {noun} to "
                        f"{logger.path}{Style.RESET_ALL}"
                    )
            except Exception as error:
                print(f"\n{Fore.YELLOW}Could not write trade log: {error}{Style.RESET_ALL}")

    # Push each new BUY signal to Telegram. The notifier de-duplicates, so
    # a standing signal is only sent once even though the board refreshes every
    # 60 seconds. A Telegram outage is isolated and never stops the scan.
    if notifier is not None and notifier.enabled and results:
        try:
            sent = notifier.notify_signals(results)
            if sent:
                noun = "signal" if sent == 1 else "signals"
                print(
                    f"\n{Fore.CYAN}Sent {sent} new {noun} to Telegram"
                    f"{Style.RESET_ALL}"
                )
        except Exception as error:
            print(f"\n{Fore.YELLOW}Could not send Telegram message: {error}{Style.RESET_ALL}")


def main():
    parser = argparse.ArgumentParser(
        description="Live cryptocurrency 15-minute trading signal analyzer."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single scan and exit (no screen clearing or countdown)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=REFRESH_SECONDS,
        help="refresh interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--log-file",
        default=TRADE_LOG_FILE,
        help=f"path to the .ods trade log (default: {TRADE_LOG_FILE})",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="do not write the .ods trade log",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="do not send Telegram notifications even if credentials are set",
    )
    parser.add_argument(
        "--bot",
        action="store_true",
        help="run an interactive Telegram bot that scans on demand via /tara",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="run headless 24/7: no screen clearing or countdown, just "
        "timestamped log lines (for systemd/journald on a server)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="print a performance report from the trade log and exit",
    )
    args = parser.parse_args()

    init(autoreset=True)  # colorama: auto-reset colors after every print

    # Performance report mode: read the log and print stats, no network needed.
    if args.stats:
        print(performance.format_text(
            performance.load_stats(args.log_file), path=args.log_file
        ))
        return

    try:
        exchange = build_exchange()
    except Exception as error:
        print(f"Failed to initialize exchange '{EXCHANGE_ID}': {error}")
        sys.exit(1)

    # Load market metadata once so the worker threads can share it.
    try:
        exchange.load_markets()
    except Exception:
        # Public OHLCV requests may still work; per-coin errors are handled later.
        pass

    # Design-A live exit: the trade log's close time (Kap Saat) and auto-close are
    # anchored to the max-hold cap; the earlier 1h-trend-flip exit is recorded by
    # hand (the user trades manually and fills the close when they actually exit).
    live_hold = {tier: MAX_HOLD_MINUTES for tier in HOLD_MINUTES}
    logger = None if args.no_log else TradeLogger(args.log_file, live_hold)

    # Telegram is optional: it disables itself cleanly when the library or the
    # TELEGRAM_* environment variables are missing, so the analyzer still runs.
    notifier = None
    if not args.no_telegram:
        notifier = TelegramNotifier()
        if notifier.enabled:
            print(f"{Fore.GREEN}Telegram notifications enabled.{Style.RESET_ALL}")
            # The bot mode sends its own startup message, so skip this one there.
            if not args.bot:
                notifier.notify_startup()
        else:
            print(
                f"{Fore.YELLOW}Telegram notifications off "
                f"({notifier.reason}).{Style.RESET_ALL}"
            )

    # Interactive bot mode: instead of the live board, wait for the /tara command
    # and run one scan per request, replying with the signals on Telegram.
    if args.bot:
        if notifier is None or not notifier.enabled:
            reason = "use --no-telegram off and set credentials" if notifier is None \
                else notifier.reason
            print(f"{Fore.RED}Cannot start --bot: Telegram is not ready "
                  f"({reason}).{Style.RESET_ALL}")
            sys.exit(1)

        def scan_callback():
            """Run one scan for a /tara request and log it like a normal cycle."""
            results, unavailable = scan(exchange)
            if logger is not None:
                now = datetime.now()
                try:
                    logger.update_closed_trades(exchange, now)
                except Exception:
                    pass
                if results:
                    try:
                        logger.log(results, now)
                    except Exception:
                        pass
            return results, unavailable

        def stats_callback():
            """Build the HTML performance report for a /istatistik request."""
            return performance.format_telegram(performance.load_stats(args.log_file))

        print(
            f"{Fore.CYAN}Telegram command bot running. "
            f"Send /tara to scan or /istatistik for stats. Press Ctrl+C to stop."
            f"{Style.RESET_ALL}"
        )
        try:
            notifier.run_command_bot(
                scan_callback, coin_count=len(SYMBOLS), stats_callback=stats_callback
            )
        except KeyboardInterrupt:
            print(f"\n{Fore.CYAN}Bot stopped. Happy trading!{Style.RESET_ALL}")
        finally:
            notifier.close()
        return

    if args.once:
        run_cycle(exchange, clear=False, logger=logger, notifier=notifier)
        if notifier is not None:
            notifier.close()
        return

    # Headless 24/7 mode for an always-on server. No screen clearing or live
    # countdown (both assume a TTY); instead each cycle prints a timestamped
    # line so `journalctl -u ctrading -f` shows progress. Every cycle is wrapped
    # so a transient network error (Binance hiccup, DNS blip) is logged and the
    # loop keeps running instead of crashing the service.
    if args.daemon:
        print(
            f"{Fore.CYAN}CTrading daemon started "
            f"(interval={args.interval}s). Ctrl+C or SIGTERM to stop."
            f"{Style.RESET_ALL}",
            flush=True,
        )
        try:
            while True:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"\n{Fore.MAGENTA}[{stamp}] scan start{Style.RESET_ALL}",
                      flush=True)
                try:
                    run_cycle(exchange, clear=False, logger=logger,
                              notifier=notifier)
                except Exception as error:
                    print(f"{Fore.RED}[{stamp}] cycle error: {error}"
                          f"{Style.RESET_ALL}", flush=True)
                    if notifier is not None and notifier.enabled:
                        try:
                            notifier.notify_error(str(error))
                        except Exception:
                            pass
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n{Fore.CYAN}Daemon stopped.{Style.RESET_ALL}", flush=True)
        finally:
            if notifier is not None:
                notifier.close()
        return

    try:
        while True:
            run_cycle(exchange, clear=True, logger=logger, notifier=notifier)
            refresh_countdown(args.interval)
    except KeyboardInterrupt:
        print(f"\n{Fore.CYAN}Stopped. Happy trading!{Style.RESET_ALL}")
    finally:
        if notifier is not None:
            notifier.close()


if __name__ == "__main__":
    main()
