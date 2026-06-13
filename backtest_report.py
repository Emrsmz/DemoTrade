#!/usr/bin/env python3
"""
================================================================================
 BACKTEST REPORT  -  A GROUPED, AT-A-GLANCE .ods FROM THE BACKTEST RESULTS
================================================================================

The raw backtest writes `backtest_sonuclar.ods`: a per-coin summary plus a flat
log of every one of the (hundreds of) trades. That is accurate but hard to read
when the goal is to *tune the strategy* — you want to see how it does broken
down by the things you can actually change.

This module reads the backtest's JSON cache (`backtest_sonuclar.json`, written by
backtest.py) and produces `backtest_analiz.ods`, an analysis workbook whose every
sheet is an AGGREGATE, not a trade dump:

  * Overview        - the run's settings, the headline totals, and Long vs Short
                      side by side.
  * By Score        - performance grouped by signal score (how many indicators
                      agreed: 2, 3, 4, 5...), and the same split by direction.
  * By Signal Group - performance per indicator COMBINATION (e.g. "BB+RSI"),
                      and per single indicator (every trade RSI took part in).
  * By Coin         - performance per coin, next to its buy & hold.

Every group row carries the same metrics so they compare directly:
    Trades | Win % | Avg P/L % | Total P/L % | Avg Win % | Avg Loss % | Best % | Worst %
Greens are gains, reds are losses, so the good and bad buckets jump out.

Notes / honest limits:
  * "Total P/L %" is the SUM of the trades' individual percentages (equal-size
    assumption), a quick proxy for "how much did this bucket contribute"; it is
    not a compounded return. "Avg P/L %" is the cleaner per-trade number.
  * A trade is tagged with the score/strength/indicators of the signal that
    OPENED it (recorded by backtest.py at entry).

Run:
    python backtest_report.py                       # uses the defaults below
    python backtest_report.py --results FOO.json --output BAR.ods
No network: it only reads the cached JSON.
================================================================================
"""

import argparse
import json
import os
from collections import Counter, defaultdict

from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import (
    ParagraphProperties,
    Style,
    TableCellProperties,
    TableColumnProperties,
    TextProperties,
)
from odf.table import Table, TableColumn, TableCell, TableRow
from odf.text import P

# Default file names, next to this script.
HERE = os.path.dirname(os.path.abspath(__file__))
# Backtest artifacts live in a dedicated backtests/ folder (created on demand).
BACKTESTS_DIR = os.path.join(HERE, "backtests")
DEFAULT_RESULTS = os.path.join(BACKTESTS_DIR, "backtest_sonuclar.json")
DEFAULT_OUTPUT = os.path.join(BACKTESTS_DIR, "backtest_analiz.ods")

# Colours (shared with backtest.py's scheme so the two files look related).
HEADER_BG = "#1F3864"   # navy header, white bold text
TITLE_BG = "#2E5496"    # section-title band
TOTAL_BG = "#DDEBF7"    # light blue total / average row
GREEN_FG = "#107C41"    # gains
RED_FG = "#C00000"      # losses

# The metric columns every group table shares, in order.
METRIC_HEADERS = ["Trades", "Win %", "Avg P/L %", "Total P/L %",
                  "Avg Win %", "Avg Loss %", "Best %", "Worst %"]
METRIC_WIDTHS = ["1.8cm", "1.8cm", "2.3cm", "2.4cm",
                 "2.3cm", "2.4cm", "2cm", "2cm"]


# ----------------------------------------------------------------------------
# STATS
# ----------------------------------------------------------------------------
def stats(trades):
    """Aggregate a list of trade dicts into the shared metric set."""
    n = len(trades)
    if n == 0:
        return dict(n=0, win_rate=0.0, avg=0.0, total=0.0,
                    avg_win=0.0, avg_loss=0.0, best=0.0, worst=0.0)
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    return dict(
        n=n,
        win_rate=len(wins) / n * 100,
        avg=sum(pnls) / n,
        total=sum(pnls),
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        best=max(pnls),
        worst=min(pnls),
    )


def indicators_in(trades):
    """The set of single indicator names seen across all trades' groups."""
    names = set()
    for t in trades:
        for name in (t.get("signals") or "").split("+"):
            if name:
                names.add(name)
    return sorted(names)


# ----------------------------------------------------------------------------
# STYLING
# ----------------------------------------------------------------------------
def _style_factory(doc):
    """Return get(...) creating/caching one table-cell style per look."""
    cache = {}

    def get(*, align="center", background=None, colour=None, bold=False):
        key = (align, background, colour, bold)
        if key in cache:
            return cache[key]
        name = f"c{len(cache)}"
        style = Style(name=name, family="table-cell")
        props = {"border": "0.5pt solid #cfcfcf", "verticalalign": "middle"}
        if background:
            props["backgroundcolor"] = background
        style.addElement(TableCellProperties(**props))
        if colour or bold:
            style.addElement(TextProperties(color=colour or "#000000",
                                            fontweight="bold" if bold else "normal"))
        style.addElement(ParagraphProperties(textalign=align))
        doc.automaticstyles.addElement(style)
        cache[key] = name
        return name

    return get


def _scell(text, stylename):
    cell = TableCell(valuetype="string", stylename=stylename)
    cell.addElement(P(text="" if text is None else str(text)))
    return cell


def _ncell(number, display, stylename):
    """Numeric cell: keeps the real value (so sort/filter works) but shows a
    tidy string."""
    cell = TableCell(valuetype="float", value=number, stylename=stylename)
    cell.addElement(P(text=display))
    return cell


def _add_columns(doc, table, widths, tag):
    for i, width in enumerate(widths):
        name = f"{tag}{i}"
        col_style = Style(name=name, family="table-column")
        col_style.addElement(TableColumnProperties(columnwidth=width))
        doc.automaticstyles.addElement(col_style)
        table.addElement(TableColumn(stylename=name))


def _signed(value):
    return f"{value:+.2f}"


# ----------------------------------------------------------------------------
# GROUP TABLE  (the reusable heart of the report)
# ----------------------------------------------------------------------------
def _metric_cells(st, s, *, bg=None):
    """The eight metric cells for one stats dict, coloured by sign/threshold."""
    def g(colour=None, bold=False):
        return st(align="center", background=bg, colour=colour, bold=bold)

    win_fg = GREEN_FG if s["win_rate"] >= 50 else RED_FG
    avg_fg = GREEN_FG if s["avg"] >= 0 else RED_FG
    tot_fg = GREEN_FG if s["total"] >= 0 else RED_FG
    return [
        _ncell(s["n"], str(s["n"]), g()),
        _ncell(s["win_rate"], f"{s['win_rate']:.1f}", g(colour=win_fg, bold=True)),
        _ncell(s["avg"], _signed(s["avg"]), g(colour=avg_fg, bold=True)),
        _ncell(s["total"], _signed(s["total"]), g(colour=tot_fg, bold=True)),
        _ncell(s["avg_win"], _signed(s["avg_win"]), g(colour=GREEN_FG)),
        _ncell(s["avg_loss"], _signed(s["avg_loss"]), g(colour=RED_FG)),
        _ncell(s["best"], _signed(s["best"]), g(colour=GREEN_FG)),
        _ncell(s["worst"], _signed(s["worst"]), g(colour=RED_FG)),
    ]


def _header_row(st, titles):
    style = st(align="center", background=HEADER_BG, colour="#FFFFFF", bold=True)
    row = TableRow()
    for t in titles:
        row.addElement(_scell(t, style))
    return row


def _title_row(st, text, span):
    """A section-title band spanning `span` columns."""
    row = TableRow()
    cell = TableCell(valuetype="string", numbercolumnsspanned=span,
                     numberrowsspanned=1,
                     stylename=st(align="left", background=TITLE_BG,
                                  colour="#FFFFFF", bold=True))
    cell.addElement(P(text=text))
    row.addElement(cell)
    for _ in range(span - 1):
        row.addElement(TableCell())
    return row


def _group_block(table, st, first_header, groups, *, total_label="TOTAL",
                 total_trades=None):
    """Append a header row + one row per (label, trades) group + a TOTAL row.

    `groups` is a list of (label, trades_list). The TOTAL row aggregates
    `total_trades` if given, otherwise the union of the groups' trades.
    """
    table.addElement(_header_row(st, [first_header] + METRIC_HEADERS))
    label_style = st(align="left", bold=True)
    for label, trs in groups:
        row = TableRow()
        row.addElement(_scell(label, label_style))
        for cell in _metric_cells(st, stats(trs)):
            row.addElement(cell)
        table.addElement(row)

    if total_trades is None:
        total_trades = [t for _, trs in groups for t in trs]
    tot_label = st(align="left", background=TOTAL_BG, bold=True)
    row = TableRow()
    row.addElement(_scell(total_label, tot_label))
    for cell in _metric_cells(st, stats(total_trades), bg=TOTAL_BG):
        row.addElement(cell)
    table.addElement(row)


def _blank_row(table):
    table.addElement(TableRow())


# ----------------------------------------------------------------------------
# SHEETS
# ----------------------------------------------------------------------------
def _overview_sheet(doc, st, meta, summaries, trades):
    table = Table(name="Overview")
    _add_columns(doc, table, ["5.5cm"] + METRIC_WIDTHS, "ov")

    longs = [t for t in trades if t["type"] == "LONG"]
    shorts = [t for t in trades if t["type"] == "SHORT"]
    beat = sum(1 for s in summaries if s["diff"] > 0)
    avg_bot = sum(s["bot_return"] for s in summaries) / len(summaries) if summaries else 0
    avg_bh = sum(s["buy_hold"] for s in summaries) / len(summaries) if summaries else 0

    key = st(align="left", bold=True)
    val = st(align="left")
    facts = [
        ("Timeframe", meta.get("timeframe", "")),
        ("History (days)", str(meta.get("days", ""))),
        ("Higher-timeframe filter", "ON" if meta.get("use_htf") else "OFF"),
        ("Coins tested", str(len(summaries))),
        ("Skipped coins", ", ".join(meta.get("skipped", [])) or "-"),
        ("Coins that beat buy & hold", f"{beat} / {len(summaries)}"),
        ("Avg bot return / coin %", _signed(avg_bot)),
        ("Avg buy & hold / coin %", _signed(avg_bh)),
    ]
    table.addElement(_title_row(st, "Backtest run", len(METRIC_HEADERS) + 1))
    for k, v in facts:
        row = TableRow()
        row.addElement(_scell(k, key))
        cell = TableCell(valuetype="string", numbercolumnsspanned=len(METRIC_HEADERS),
                         numberrowsspanned=1, stylename=val)
        cell.addElement(P(text=v))
        row.addElement(cell)
        for _ in range(len(METRIC_HEADERS) - 1):
            row.addElement(TableCell())
        table.addElement(row)

    _blank_row(table)
    table.addElement(_title_row(st, "Direction (Long vs Short)", len(METRIC_HEADERS) + 1))
    _group_block(table, st, "Direction",
                 [("Long", longs), ("Short", shorts)], total_trades=trades)

    doc.spreadsheet.addElement(table)


def _by_score_sheet(doc, st, trades):
    table = Table(name="By Score")
    _add_columns(doc, table, ["5.5cm"] + METRIC_WIDTHS, "sc")

    scores = sorted({int(t.get("score") or 0) for t in trades})
    by_score = [(f"Score {sc} (indicators agreed)",
                 [t for t in trades if int(t.get("score") or 0) == sc])
                for sc in scores]
    table.addElement(_title_row(st, "By signal score", len(METRIC_HEADERS) + 1))
    _group_block(table, st, "Signal score", by_score, total_trades=trades)

    _blank_row(table)
    table.addElement(_title_row(st, "Score x Direction", len(METRIC_HEADERS) + 1))
    rows = []
    for sc in scores:
        for side in ("LONG", "SHORT"):
            trs = [t for t in trades
                   if int(t.get("score") or 0) == sc and t["type"] == side]
            if trs:
                rows.append((f"Score {sc} - {side.title()}", trs))
    _group_block(table, st, "Score / direction", rows, total_trades=trades)

    doc.spreadsheet.addElement(table)


def _by_group_sheet(doc, st, trades):
    table = Table(name="By Signal Group")
    _add_columns(doc, table, ["5.5cm"] + METRIC_WIDTHS, "gr")

    # Per indicator COMBINATION, most profitable first.
    combos = defaultdict(list)
    for t in trades:
        combos[t.get("signals") or "(none)"].append(t)
    combo_rows = sorted(combos.items(), key=lambda kv: stats(kv[1])["total"],
                        reverse=True)
    table.addElement(_title_row(st, "By indicator combination (best total first)",
                                len(METRIC_HEADERS) + 1))
    _group_block(table, st, "Indicator group", combo_rows, total_trades=trades)

    # Per SINGLE indicator: every trade that indicator took part in (groups
    # overlap, so there is no meaningful TOTAL row here).
    _blank_row(table)
    table.addElement(_title_row(st, "By single indicator (trades it contributed to)",
                                len(METRIC_HEADERS) + 1))
    table.addElement(_header_row(st, ["Indicator"] + METRIC_HEADERS))
    label_style = st(align="left", bold=True)
    for name in indicators_in(trades):
        trs = [t for t in trades if name in (t.get("signals") or "").split("+")]
        row = TableRow()
        row.addElement(_scell(name, label_style))
        for cell in _metric_cells(st, stats(trs)):
            row.addElement(cell)
        table.addElement(row)

    doc.spreadsheet.addElement(table)


def _by_coin_sheet(doc, st, summaries, trades):
    table = Table(name="By Coin")
    # coin + 8 metrics + bot/buy&hold/diff/max_dd
    extra = ["Bot Ret %", "Buy&Hold %", "Diff %", "Max DD %"]
    widths = ["3.2cm"] + METRIC_WIDTHS + ["2.2cm", "2.4cm", "2cm", "2.2cm"]
    _add_columns(doc, table, widths, "co")

    summ = {s["symbol"]: s for s in summaries}
    by_coin = defaultdict(list)
    for t in trades:
        by_coin[t["symbol"]].append(t)
    # Rank by total P/L of the coin's trades, best first.
    ranked = sorted(by_coin.items(), key=lambda kv: stats(kv[1])["total"],
                    reverse=True)

    table.addElement(_header_row(st, ["Coin"] + METRIC_HEADERS + extra))
    coin_style = st(align="left", bold=True)
    for symbol, trs in ranked:
        s = stats(trs)
        row = TableRow()
        row.addElement(_scell(symbol, coin_style))
        for cell in _metric_cells(st, s):
            row.addElement(cell)
        sm = summ.get(symbol, {})
        br = sm.get("bot_return", 0.0)
        bh = sm.get("buy_hold", 0.0)
        df = sm.get("diff", 0.0)
        dd = sm.get("max_dd", 0.0)
        row.addElement(_ncell(br, _signed(br),
                              st(align="center", colour=GREEN_FG if br >= 0 else RED_FG, bold=True)))
        row.addElement(_ncell(bh, _signed(bh), st(align="center")))
        row.addElement(_ncell(df, _signed(df),
                              st(align="center", colour=GREEN_FG if df >= 0 else RED_FG)))
        row.addElement(_ncell(dd, f"{dd:.1f}", st(align="center")))
        table.addElement(row)

    doc.spreadsheet.addElement(table)


# ----------------------------------------------------------------------------
# BUILD
# ----------------------------------------------------------------------------
def build_report(results_path, output_path):
    with open(results_path, encoding="utf-8") as fh:
        cache = json.load(fh)
    meta = cache.get("meta", {})
    summaries = cache.get("summaries", [])
    trades = cache.get("trades", [])

    if not trades:
        raise SystemExit("No trades in the results cache - run backtest.py first.")
    if any("score" not in t for t in trades):
        raise SystemExit(
            "These results predate the signal-detail fields (score/strength/"
            "signals). Re-run backtest.py to regenerate the cache, then retry.")

    doc = OpenDocumentSpreadsheet()
    st = _style_factory(doc)
    _overview_sheet(doc, st, meta, summaries, trades)
    _by_score_sheet(doc, st, trades)
    _by_group_sheet(doc, st, trades)
    _by_coin_sheet(doc, st, summaries, trades)
    doc.save(output_path)
    return meta, summaries, trades


def _print_console(meta, summaries, trades):
    """A short text echo so the run is reviewable without opening the file."""
    longs = [t for t in trades if t["type"] == "LONG"]
    shorts = [t for t in trades if t["type"] == "SHORT"]

    def line(label, trs):
        s = stats(trs)
        print(f"  {label:<22} {s['n']:>4} trades   win {s['win_rate']:>5.1f}%   "
              f"avg {s['avg']:+.2f}%   total {s['total']:+.1f}%")

    print(f"\nBacktest analysis  ({meta.get('timeframe')}, {meta.get('days')}d, "
          f"HTF {'on' if meta.get('use_htf') else 'off'})")
    print("-" * 72)
    line("ALL", trades)
    line("Long", longs)
    line("Short", shorts)
    print("  by score:")
    for sc in sorted({int(t.get("score") or 0) for t in trades}):
        line(f"  score {sc}", [t for t in trades if int(t.get("score") or 0) == sc])


def main():
    parser = argparse.ArgumentParser(
        description="Build a grouped .ods analysis from the backtest results cache.")
    parser.add_argument("--results", default=DEFAULT_RESULTS,
                        help=f"results JSON (default: {os.path.basename(DEFAULT_RESULTS)})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output .ods (default: {os.path.basename(DEFAULT_OUTPUT)})")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    meta, summaries, trades = build_report(args.results, args.output)
    _print_console(meta, summaries, trades)
    print("-" * 72)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
