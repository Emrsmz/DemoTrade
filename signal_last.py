#!/usr/bin/env python3
"""
================================================================================
 RECENT-SIGNAL BACKFILL  ->  signal_last.ods (trade_log.ods format)
================================================================================

Writes a NEW .ods log, in the EXACT same column layout and styling as
trade_log.ods, holding every above-threshold BUY signal of the last N days
(default 20), so the recent signals can be reviewed together.

How it works (long-only, faithful to the live board):
  * Download recent 15-minute candles for the same 30 coins the live board scans
    (N reporting days + a warm-up tail so the indicators and the 1-hour trend are
    fully formed). Read-only, via the 451-bypass data mirror.
  * Replay each coin candle by candle. At each candle it runs the live
    `analyze_symbol` on the rolling window and KEEPS the signal only when it
    clears the same live gate:
        - a real signal that is not WEAK (>= CT_MIN_AGREE indicators agree), and
        - freshly triggered (candles_ago <= CROSS_FRESHNESS_CANDLES), and
        - it agrees with the 1-hour higher-timeframe trend (htf_allows).
    The strategy is long-only, so every kept signal is a BUY / Long.
  * One trade at a time per coin (exactly like the live logger, which never
    re-logs a coin while it still has an open position). Once taken, the signal
    is followed to its exit using the live exit rule: the 1-hour trend flipping
    DOWN, or the 24h max-hold cap (CT_LIVE_MAX_HOLD) - whichever comes first. The
    close price then fills the WIN/LOSS result.
  * A signal still open at the end of the data is written with a blank close /
    result (an open position), just as the live log shows it.

This is NOT a stop-loss and does not place any orders - it only reconstructs
what the live board would have flagged over the last N days for review.

    python signal_last.py                   # -> signal_last.ods (last 20 days)
    python signal_last.py --days 5 --output mylog.ods
================================================================================
"""

import argparse
import os
import time
from datetime import datetime, timedelta

import pandas as pd
from colorama import Fore, Style, init

from backtest import (
    build_exchange,
    compute_htf_trends,
    fetch_ohlcv_range,
    timeframe_to_minutes,
    HTF_FACTOR,
    WINDOW,
)
from signal_analyzer import (
    analyze_symbol,
    htf_allows,
    BASE_TIMEFRAME,
    CROSS_FRESHNESS_CANDLES,
    MAX_HOLD_MINUTES,
    MIN_CANDLES,
    SYMBOLS,
)
from trade_logger import _write_records

init(autoreset=True)

# Reporting window (entries within the last N days) plus a warm-up tail so the
# rolling indicators and the 1h-trend EMAs are fully formed before the window.
REPORT_DAYS = 20
WARMUP_DAYS = 4
DAY_MS = 86_400_000

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "signal_last.ods"
)


def _build_record(rec, entry_price, entry_ms, htf_at_entry, close_price, close_ms):
    """Turn one taken signal into a trade_log.ods record dict.

    `close_price`/`close_ms` are None for a signal still open at the end of the
    data; that row is written with a blank close + result, like the live log.
    Times use local wall-clock (datetime.fromtimestamp), matching the live logger.
    """
    base, _, quote = rec["symbol"].partition("/")
    entry_dt = datetime.fromtimestamp(entry_ms / 1000)
    # The measured value behind each agreeing indicator, in display order, e.g.
    # "RSI 24 | MACD 0.12% | VOL 3.2x" - identical to the live Signal Detail.
    details = rec.get("details", {})
    signals = " | ".join(
        details.get(name) or name for name in rec.get("contributors", [])
    )

    if close_price is not None:
        profit_pct = (close_price - entry_price) / entry_price * 100
        result = f"{'WIN' if profit_pct >= 0 else 'LOSS'} {profit_pct:+.2f}%"
        close_dt = datetime.fromtimestamp(close_ms / 1000)
    else:
        # Still open: show the projected max-hold cap as Kap Saat, like the live
        # log does for an open trade, but leave the close price + result blank.
        result = ""
        close_dt = entry_dt + timedelta(minutes=MAX_HOLD_MINUTES)

    return {
        "trade": None,                       # assigned chronologically later
        "base": base,
        "quote": quote,
        "entry": entry_price,
        "close": close_price,                # None -> blank cell (open position)
        "open_time": f"{entry_dt:%H:%M}",
        "close_time": f"{close_dt:%H:%M}",
        "position": "Long",                  # long-only
        "date": f"{entry_dt:%d/%m/%Y}",
        "result": result,
        "signal": float(rec["score"]),
        "signals": signals,
        "htf_trend": htf_at_entry,
        "note": "",
        "_entry_ms": entry_ms,               # sort key only; ignored by the writer
    }


def replay_symbol(symbol, df, htf_trends, cutoff_ms):
    """Replay one coin and return its taken-signal records (one trade at a time).

    Entries are only opened on candles within the reporting window (ts >= cutoff),
    but exits may extend up to the latest fetched candle. A position still open at
    the end is returned with a blank close.
    """
    n = len(df)
    closes = df["close"].tolist()
    ts = df["timestamp"].astype("int64").tolist()
    max_hold_ms = MAX_HOLD_MINUTES * 60_000

    records = []
    in_position = False
    entry = None

    for i in range(MIN_CANDLES, n):
        # --- EXIT first: release an open trade when the 1h trend flips DOWN or
        # the max-hold cap is reached (read at this candle's close). ---
        if in_position:
            flipped_down = htf_trends[i] == "DOWN"
            capped = ts[i] - entry["ms"] >= max_hold_ms
            if flipped_down or capped:
                records.append(_build_record(
                    entry["rec"], entry["price"], entry["ms"], entry["htf"],
                    closes[i], ts[i],
                ))
                in_position = False
                entry = None
                continue

        # --- ENTRY: only while flat and only inside the reporting window. ---
        if not in_position and ts[i] >= cutoff_ms:
            lo = max(0, i - WINDOW + 1)
            window = df.iloc[lo:i + 1]
            rec = analyze_symbol(symbol, window)
            actionable = (
                rec is not None
                and rec["strength"] != "WEAK"
                and rec["candles_ago"] <= CROSS_FRESHNESS_CANDLES
                and htf_allows(htf_trends[i], rec["direction"])
            )
            if actionable:
                entry = {"rec": rec, "price": rec["price"],
                         "ms": ts[i], "htf": htf_trends[i]}
                in_position = True

    if in_position:   # still open at the end of the data -> blank close
        records.append(_build_record(
            entry["rec"], entry["price"], entry["ms"], entry["htf"], None, None,
        ))
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Backfill the last N days of above-threshold BUY signals "
                    "into a trade_log.ods-format file for review."
    )
    parser.add_argument("--days", type=int, default=REPORT_DAYS,
                        help=f"reporting window in days (default: {REPORT_DAYS})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output .ods path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    timeframe = BASE_TIMEFRAME            # "15m" - matches the live board
    tf_minutes = timeframe_to_minutes(timeframe)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - (args.days + WARMUP_DAYS) * DAY_MS
    cutoff_ms = now_ms - args.days * DAY_MS

    print(f"{Style.BRIGHT}{Fore.CYAN}Backfilling the last {args.days} days of "
          f"above-threshold BUY signals for {len(SYMBOLS)} coins "
          f"({timeframe} candles)...{Style.RESET_ALL}")

    exchange = build_exchange()
    all_records = []
    coins_with_signals = 0
    skipped = []

    for n_done, symbol in enumerate(SYMBOLS, start=1):
        print(f"\r  {n_done:>2}/{len(SYMBOLS)}  {symbol:<12}", end="", flush=True)
        df = fetch_ohlcv_range(exchange, symbol, timeframe, since_ms, now_ms, tf_minutes)
        if df is None or len(df) < MIN_CANDLES + 5:
            skipped.append(symbol)
            continue
        htf_trends = compute_htf_trends(df, tf_minutes, HTF_FACTOR)
        recs = replay_symbol(symbol, df, htf_trends, cutoff_ms)
        if recs:
            coins_with_signals += 1
            all_records.extend(recs)
    print()

    # Chronological order across all coins, then sequential trade numbers.
    all_records.sort(key=lambda r: r["_entry_ms"])
    for number, record in enumerate(all_records, start=1):
        record["trade"] = number
        del record["_entry_ms"]

    _write_records(args.output, all_records)

    open_n = sum(1 for r in all_records if r["close"] is None)
    closed = [r for r in all_records if r["close"] is not None]
    wins = sum(1 for r in closed if r["result"].startswith("WIN"))
    print(f"\n{Fore.GREEN}Wrote {len(all_records)} signal(s) from "
          f"{coins_with_signals} coin(s) to {args.output}{Style.RESET_ALL}")
    if closed:
        win_rate = wins / len(closed) * 100
        print(f"  {len(closed)} closed (win rate {win_rate:.0f}%), "
              f"{open_n} still open")
    if skipped:
        print(f"  {Fore.YELLOW}skipped (no data): "
              f"{', '.join(skipped)}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
