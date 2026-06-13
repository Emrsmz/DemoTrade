#!/usr/bin/env python3
"""
================================================================================
 BACKTEST COMPARE  -  BASELINE vs TUNED, SIDE BY SIDE, IN ONE READABLE .ods
================================================================================

When tuning the strategy you run the backtest twice: once with the current
settings (the baseline) and once with the change you are testing (the tuned
run). Each run writes its own results JSON (see backtest.py --output). Eyeballing
two separate workbooks to see whether the change actually helped is painful.

This tool reads BOTH results JSONs and writes a single comparison workbook
(`backtest_karsilastirma.ods`) with two sheets:

  * Summary   - the headline numbers for each run next to each other, plus the
                change: win rate, average and total P/L per trade, average bot
                return per coin, how many coins beat buy & hold, trade counts.
                The "Change" column is green when the tuned run is better.
  * Per Coin  - one row per coin: buy & hold, the baseline vs tuned return, the
                difference, the trade counts and win rates. Sorted by the tuned
                return so the winners and losers are easy to find.

Buy & hold is identical in both runs (same coins, same period), so it is shown
once as the passive benchmark both runs are trying to beat.

Run:
    python backtest_compare.py --baseline A.json --tuned B.json
    python backtest_compare.py            # uses the default file names below
No network: it only reads the two cached JSONs.
================================================================================
"""

import argparse
import json
import os
import statistics

from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P

# Reuse the styling + small helpers from the grouped report so the two
# workbooks look like they belong to the same family.
from backtest_report import (
    _style_factory,
    _scell,
    _ncell,
    _add_columns,
    _signed,
    HEADER_BG,
    TITLE_BG,
    TOTAL_BG,
    GREEN_FG,
    RED_FG,
)

HERE = os.path.dirname(os.path.abspath(__file__))
# Backtest artifacts live in a dedicated backtests/ folder (created on demand) so
# they never clutter the project root.
BACKTESTS_DIR = os.path.join(HERE, "backtests")
DEFAULT_BASELINE = os.path.join(BACKTESTS_DIR, "backtest_long90_base.json")
DEFAULT_TUNED = os.path.join(BACKTESTS_DIR, "backtest_long90_tuned.json")
DEFAULT_OUTPUT = os.path.join(BACKTESTS_DIR, "backtest_karsilastirma.ods")


# ----------------------------------------------------------------------------
# AGGREGATION
# ----------------------------------------------------------------------------
def run_aggregate(cache):
    """Collapse one results cache into the headline numbers we compare on."""
    summaries = cache.get("summaries", [])
    trades = cache.get("trades", [])
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    coins = len(summaries)
    bot_returns = [s["bot_return"] for s in summaries]
    return {
        "coins": coins,
        "trades": len(trades),
        "avg_trades": (len(trades) / coins) if coins else 0.0,
        "win_rate": (len(wins) / len(pnls) * 100) if pnls else 0.0,
        "avg_pl": (sum(pnls) / len(pnls)) if pnls else 0.0,
        "total_pl": sum(pnls),
        "avg_bot": (sum(bot_returns) / coins) if coins else 0.0,
        "median_bot": statistics.median(bot_returns) if bot_returns else 0.0,
        "avg_bh": (sum(s["buy_hold"] for s in summaries) / coins) if coins else 0.0,
        "beat": sum(1 for s in summaries if s["diff"] > 0),
    }


# ----------------------------------------------------------------------------
# SUMMARY SHEET
# ----------------------------------------------------------------------------
# (label, key, format-fn, higher_is_better). higher_is_better=None means the
# metric is neutral (no green/red on the change, e.g. trade counts).
def _fmt_int(v):
    return str(int(round(v)))


def _fmt_1(v):
    return f"{v:.1f}"


def _summary_sheet(doc, st, base, tuned):
    table = Table(name="Summary")
    _add_columns(doc, table, ["6.5cm", "3.2cm", "3.2cm", "3.2cm"], "su")

    header = st(align="center", background=HEADER_BG, colour="#FFFFFF", bold=True)
    row = TableRow()
    for t in ["Metric", "Baseline", "Tuned", "Change"]:
        row.addElement(_scell(t, header))
    table.addElement(row)

    metrics = [
        ("Coins tested", "coins", _fmt_int, None),
        ("Coins that beat buy & hold", "beat", _fmt_int, True),
        ("Avg bot return / coin %", "avg_bot", _signed, True),
        ("Median bot return / coin %", "median_bot", _signed, True),
        ("Avg buy & hold / coin %", "avg_bh", _signed, None),
        ("Win rate %", "win_rate", _fmt_1, True),
        ("Avg P/L per trade %", "avg_pl", _signed, True),
        ("Total P/L % (sum, equal-size)", "total_pl", _signed, True),
        ("Total trades", "trades", _fmt_int, None),
        ("Avg trades / coin", "avg_trades", _fmt_1, None),
    ]

    label_style = st(align="left", bold=True)
    for label, key, fmt, higher_better in metrics:
        b, t = base[key], tuned[key]
        delta = t - b
        if higher_better is None:
            change_fg = None
        else:
            improved = (delta > 0) if higher_better else (delta < 0)
            change_fg = GREEN_FG if improved else (RED_FG if delta else None)
        # value cells coloured by sign for the return-like rows
        signed_metric = fmt in (_signed,)
        b_fg = (GREEN_FG if b >= 0 else RED_FG) if signed_metric else None
        t_fg = (GREEN_FG if t >= 0 else RED_FG) if signed_metric else None

        row = TableRow()
        row.addElement(_scell(label, label_style))
        row.addElement(_ncell(b, fmt(b), st(align="center", colour=b_fg)))
        row.addElement(_ncell(t, fmt(t), st(align="center", colour=t_fg, bold=True)))
        change_text = "0" if abs(delta) < 1e-9 else f"{delta:+.1f}"
        row.addElement(_ncell(delta, change_text,
                              st(align="center", colour=change_fg, bold=True)))
        table.addElement(row)

    doc.spreadsheet.addElement(table)


# ----------------------------------------------------------------------------
# PER-COIN SHEET
# ----------------------------------------------------------------------------
def _per_coin_sheet(doc, st, base_cache, tuned_cache):
    table = Table(name="Per Coin")
    _add_columns(
        doc, table,
        ["3cm", "2.6cm", "2.6cm", "2.6cm", "2.4cm", "2.4cm", "2.4cm", "2.4cm", "2.4cm"],
        "pc",
    )

    base = {s["symbol"]: s for s in base_cache.get("summaries", [])}
    tuned = {s["symbol"]: s for s in tuned_cache.get("summaries", [])}
    symbols = sorted(set(base) | set(tuned),
                     key=lambda s: tuned.get(s, {}).get("bot_return", float("-inf")),
                     reverse=True)

    headers = ["Coin", "Buy & Hold %", "Base Ret %", "Tuned Ret %", "Δ Tuned-Base",
               "Base Trades", "Tuned Trades", "Base Win %", "Tuned Win %"]
    header = st(align="center", background=HEADER_BG, colour="#FFFFFF", bold=True)
    row = TableRow()
    for h in headers:
        row.addElement(_scell(h, header))
    table.addElement(row)

    def col(v):
        return GREEN_FG if v >= 0 else RED_FG

    name_style = st(align="left", bold=True)
    for sym in symbols:
        b = base.get(sym, {})
        t = tuned.get(sym, {})
        bh = (t or b).get("buy_hold", 0.0)
        b_ret = b.get("bot_return", 0.0)
        t_ret = t.get("bot_return", 0.0)
        delta = t_ret - b_ret
        row = TableRow()
        row.addElement(_scell(sym.replace("/USDT", ""), name_style))
        row.addElement(_ncell(bh, _signed(bh), st(align="center", colour=col(bh))))
        row.addElement(_ncell(b_ret, _signed(b_ret), st(align="center", colour=col(b_ret))))
        row.addElement(_ncell(t_ret, _signed(t_ret),
                              st(align="center", colour=col(t_ret), bold=True)))
        row.addElement(_ncell(delta, f"{delta:+.2f}",
                              st(align="center", colour=col(delta), bold=True)))
        row.addElement(_ncell(b.get("trades", 0), str(b.get("trades", 0)),
                              st(align="center")))
        row.addElement(_ncell(t.get("trades", 0), str(t.get("trades", 0)),
                              st(align="center")))
        row.addElement(_ncell(b.get("win_rate", 0.0), f"{b.get('win_rate', 0.0):.1f}",
                              st(align="center")))
        row.addElement(_ncell(t.get("win_rate", 0.0), f"{t.get('win_rate', 0.0):.1f}",
                              st(align="center", bold=True)))
        table.addElement(row)

    # TOTAL / average row.
    def avg(d, key):
        vals = [s.get(key, 0.0) for s in d.values()]
        return sum(vals) / len(vals) if vals else 0.0

    tot_style = st(align="left", background=TOTAL_BG, bold=True)
    tcell = lambda v, disp, c=None: _ncell(v, disp, st(align="center", background=TOTAL_BG,
                                                       colour=c, bold=True))
    row = TableRow()
    row.addElement(_scell("AVERAGE", tot_style))
    abh, ab, at = avg(tuned, "buy_hold"), avg(base, "bot_return"), avg(tuned, "bot_return")
    row.addElement(tcell(abh, _signed(abh), col(abh)))
    row.addElement(tcell(ab, _signed(ab), col(ab)))
    row.addElement(tcell(at, _signed(at), col(at)))
    row.addElement(tcell(at - ab, f"{at - ab:+.2f}", col(at - ab)))
    row.addElement(tcell(avg(base, "trades"), f"{avg(base, 'trades'):.1f}"))
    row.addElement(tcell(avg(tuned, "trades"), f"{avg(tuned, 'trades'):.1f}"))
    row.addElement(tcell(avg(base, "win_rate"), f"{avg(base, 'win_rate'):.1f}"))
    row.addElement(tcell(avg(tuned, "win_rate"), f"{avg(tuned, 'win_rate'):.1f}"))
    table.addElement(row)

    doc.spreadsheet.addElement(table)


# ----------------------------------------------------------------------------
# BUILD
# ----------------------------------------------------------------------------
def build_comparison(baseline_path, tuned_path, output_path):
    with open(baseline_path, encoding="utf-8") as fh:
        base_cache = json.load(fh)
    with open(tuned_path, encoding="utf-8") as fh:
        tuned_cache = json.load(fh)

    base = run_aggregate(base_cache)
    tuned = run_aggregate(tuned_cache)

    doc = OpenDocumentSpreadsheet()
    st = _style_factory(doc)
    _summary_sheet(doc, st, base, tuned)
    _per_coin_sheet(doc, st, base_cache, tuned_cache)
    doc.save(output_path)
    return base, tuned


def _print_console(base, tuned):
    print("\nBaseline vs Tuned")
    print("-" * 60)
    rows = [
        ("Coins beat B&H", "beat", "{:.0f}"),
        ("Avg bot return %", "avg_bot", "{:+.2f}"),
        ("Median return %", "median_bot", "{:+.2f}"),
        ("Win rate %", "win_rate", "{:.1f}"),
        ("Avg P/L/trade %", "avg_pl", "{:+.3f}"),
        ("Total P/L %", "total_pl", "{:+.1f}"),
        ("Trades", "trades", "{:.0f}"),
    ]
    for label, key, fmt in rows:
        b = fmt.format(base[key])
        t = fmt.format(tuned[key])
        print(f"  {label:<18} baseline {b:>9}   tuned {t:>9}")
    print(f"  (avg buy & hold {base['avg_bh']:+.2f}%, benchmark both must beat)")


def main():
    parser = argparse.ArgumentParser(
        description="Compare two backtest result JSONs into one readable .ods.")
    parser.add_argument("--baseline", default=DEFAULT_BASELINE,
                        help=f"baseline results JSON (default: {os.path.basename(DEFAULT_BASELINE)})")
    parser.add_argument("--tuned", default=DEFAULT_TUNED,
                        help=f"tuned results JSON (default: {os.path.basename(DEFAULT_TUNED)})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output .ods (default: {os.path.basename(DEFAULT_OUTPUT)})")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    base, tuned = build_comparison(args.baseline, args.tuned, args.output)
    _print_console(base, tuned)
    print("-" * 60)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
