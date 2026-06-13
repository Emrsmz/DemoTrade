#!/usr/bin/env python3
"""
================================================================================
 INDICATOR VALUE IMPACT  -  HANGI GOSTERGE DEGERI KAZANDIRIYOR?
================================================================================

backtest.py artik her trade'i, o trade'i acan sinyaldeki her gostergenin OLCULEN
DEGERI ile kaydediyor ("values": {"RSI": 24.0, "VWAP": 0.62, ...}). Bu modul o
degerleri okur ve her gosterge icin "degeri su araliktayken ne kadar kazandirdi"
sorusunu yanitlayan, okunmasi kolay bir .ods uretir: backtest_indicators.ods.

Amac: long-only stratejide hangi gostergenin DEGERI sonucu en cok belirliyor —
boylece esikleri (RSI_OVERSOLD, VOL carpani, vb.) o yonde sikilastirip kazanma
oranini %50 ustune cikarmaya calisabiliriz.

Sayfalar:
  * Overview         - calisma bilgisi, genel sonuc, ve her gosterge icin tek
                       satirlik karsilastirma (en ayirt edici gosterge en ustte).
  * Indicator Values - her gosterge icin, degeri ceyrek dilimlere (quantile)
                       bolunmus tablolar: araliga gore trade sayisi, kazanma %,
                       ortalama ve toplam P/L. Kazanma > %50 olan araliklar yesil.

Her trade, o gostergenin DEGERI ile yalnizca o gosterge sinyale KATILDIYSA
gorunur (katki yapan gostergenin olcumu anlamli olan tek deger budur).

Calistirma:
    python backtest_indicators.py                         # varsayilanlar
    python backtest_indicators.py --results X.json --output Y.ods
Internet gerektirmez; sadece onbellek JSON'unu okur.
================================================================================
"""

import argparse
import json
import os

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

# Dosyalar bu betigin yaninda durur.
HERE = os.path.dirname(os.path.abspath(__file__))
# Backtest artifacts live in a dedicated backtests/ folder (created on demand).
BACKTESTS_DIR = os.path.join(HERE, "backtests")
DEFAULT_RESULTS = os.path.join(BACKTESTS_DIR, "backtest_long30.json")
DEFAULT_OUTPUT = os.path.join(BACKTESTS_DIR, "backtest_indicators.ods")

# Renkler (backtest.py / backtest_report.py ile ayni aile gorunsun diye).
HEADER_BG = "#1F3864"   # lacivert baslik, beyaz kalin yazi
TITLE_BG = "#2E5496"    # bolum-basligi bandi
TOTAL_BG = "#DDEBF7"    # acik mavi "ALL" satiri
GREEN_FG = "#107C41"    # kazanc
RED_FG = "#C00000"      # kayip

# Gostergeler ve degerlerinin ne anlama geldigi (Overview'da kucuk bir aciklama
# olarak gosterilir; analyze_symbol'un detay bicimleriyle eslesir).
INDICATOR_ORDER = ["RSI", "MACD", "BB", "EMA", "VOL", "VWAP"]
INDICATOR_UNITS = {
    "RSI": "RSI level (low = oversold; BUY fires when oversold)",
    "MACD": "histogram, % of price",
    "BB": "distance past the band, % of price",
    "EMA": "fast-slow EMA gap, % of price",
    "VOL": "volume multiple (x its average)",
    "VWAP": "distance from VWAP, % of price",
}


# ----------------------------------------------------------------------------
# AGGREGATION
# ----------------------------------------------------------------------------
def stats(trades):
    """Bir trade listesini ozet metriklere indirger."""
    n = len(trades)
    if n == 0:
        return dict(n=0, win_rate=0.0, avg=0.0, total=0.0)
    pnls = [t["pnl"] for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    return dict(n=n, win_rate=wins / n * 100,
                avg=sum(pnls) / n, total=sum(pnls))


def value_pairs(trades, indicator):
    """O gostergenin sinyale katildigi trade'leri (deger, trade) olarak dondurur.

    Degeri olmayan (parse edilemeyen) ya da gostergenin katilmadigi trade'ler
    atlanir."""
    out = []
    for t in trades:
        v = (t.get("values") or {}).get(indicator)
        if v is not None:
            out.append((float(v), t))
    return out


def quantile_buckets(pairs):
    """(deger, trade) ciftlerini degere gore sirali ceyrek dilimlere boler.

    Kova sayisini veri buyuklugune gore secer (her kovada ~8+ trade hedeflenir,
    en fazla 5 kova), boylece her kova dolu olur ve esik tahmini gerekmez.
    Her kova icin (alt_deger, ust_deger, trade_listesi) dondurur."""
    pairs = sorted(pairs, key=lambda p: p[0])
    n = len(pairs)
    if n == 0:
        return []
    q = max(1, min(5, n // 8))
    buckets = []
    for b in range(q):
        lo_i = b * n // q
        hi_i = (b + 1) * n // q
        chunk = pairs[lo_i:hi_i]
        if not chunk:
            continue
        buckets.append((chunk[0][0], chunk[-1][0], [t for _, t in chunk]))
    return buckets


def _fmt_val(v):
    if v is None:
        return ""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s or "0"


# ----------------------------------------------------------------------------
# STYLING
# ----------------------------------------------------------------------------
def _style_factory(doc):
    """Her gorunum icin tek bir tablo-hucre stili uretip onbellekler."""
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


def _title_row(st, text, span):
    """span sutuna yayilan bir bolum-basligi bandi."""
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


def _header_row(st, titles):
    row = TableRow()
    style = st(align="center", background=HEADER_BG, colour="#FFFFFF", bold=True)
    for t in titles:
        row.addElement(_scell(t, style))
    return row


def _win_fg(win_rate):
    # %50 esigi hedefimiz: gecen araliklari yesil, gecemeyeni kirmizi yap.
    return GREEN_FG if win_rate >= 50 else RED_FG


def _metric_cells(st, s, *, bg=None):
    """Bir kova/satir icin: Trades, Win %, Avg P/L %, Total P/L %."""
    def g(colour=None, bold=False):
        return st(align="center", background=bg, colour=colour, bold=bold)
    return [
        _ncell(s["n"], str(s["n"]), g()),
        _ncell(s["win_rate"], f"{s['win_rate']:.1f}", g(colour=_win_fg(s["win_rate"]), bold=True)),
        _ncell(s["avg"], f"{s['avg']:+.2f}", g(colour=GREEN_FG if s["avg"] >= 0 else RED_FG, bold=True)),
        _ncell(s["total"], f"{s['total']:+.1f}", g(colour=GREEN_FG if s["total"] >= 0 else RED_FG)),
    ]


# ----------------------------------------------------------------------------
# SHEETS
# ----------------------------------------------------------------------------
METRIC_HEADERS = ["Trades", "Win %", "Avg P/L %", "Total P/L %"]


def _overview_sheet(doc, st, meta, trades):
    table = Table(name="Overview")
    cols = ["3cm", "2cm", "2cm", "2.2cm", "3.4cm", "2.6cm", "2.2cm", "7cm"]
    _add_columns(doc, table, cols, "ov")
    span = len(cols)

    overall = stats(trades)
    key = st(align="left", bold=True)
    val = st(align="left")

    table.addElement(_title_row(st, "Backtest run (long-only)", span))
    facts = [
        ("Timeframe", str(meta.get("timeframe", ""))),
        ("History (days)", str(meta.get("days", ""))),
        ("Higher-timeframe filter", "ON" if meta.get("use_htf") else "OFF"),
        ("Skipped coins", ", ".join(meta.get("skipped", [])) or "-"),
        ("Total long trades", str(overall["n"])),
        ("Overall win rate %", f"{overall['win_rate']:.1f}"),
        ("Overall avg P/L %", f"{overall['avg']:+.2f}"),
    ]
    for k, v in facts:
        row = TableRow()
        row.addElement(_scell(k, key))
        cell = TableCell(valuetype="string", numbercolumnsspanned=span - 1,
                         numberrowsspanned=1, stylename=val)
        cell.addElement(P(text=v))
        row.addElement(cell)
        for _ in range(span - 2):
            row.addElement(TableCell())
        table.addElement(row)

    table.addElement(TableRow())
    table.addElement(_title_row(st, "Which indicator's VALUE matters most? "
                                "(biggest win-rate spread across its value range = most decisive)", span))
    headers = ["Indicator", "Trades", "Win %", "Avg P/L %",
               "Best value range", "its Win %", "Spread", "What the value means"]
    table.addElement(_header_row(st, headers))

    rows = []
    for ind in INDICATOR_ORDER:
        pairs = value_pairs(trades, ind)
        if not pairs:
            continue
        s = stats([t for _, t in pairs])
        buckets = quantile_buckets(pairs)
        bstats = [(lo, hi, stats(trs)) for lo, hi, trs in buckets]
        best = max(bstats, key=lambda x: x[2]["win_rate"])
        spread = (max(b[2]["win_rate"] for b in bstats)
                  - min(b[2]["win_rate"] for b in bstats)) if bstats else 0.0
        rows.append((ind, s, best, spread))

    rows.sort(key=lambda r: r[3], reverse=True)   # en ayirt edici en ustte
    label = st(align="left", bold=True)
    for ind, s, best, spread in rows:
        lo, hi, bs = best
        row = TableRow()
        row.addElement(_scell(ind, label))
        row.addElement(_ncell(s["n"], str(s["n"]), st(align="center")))
        row.addElement(_ncell(s["win_rate"], f"{s['win_rate']:.1f}",
                              st(align="center", colour=_win_fg(s["win_rate"]), bold=True)))
        row.addElement(_ncell(s["avg"], f"{s['avg']:+.2f}",
                              st(align="center", colour=GREEN_FG if s["avg"] >= 0 else RED_FG)))
        row.addElement(_scell(f"{_fmt_val(lo)} - {_fmt_val(hi)}", st(align="center")))
        row.addElement(_ncell(bs["win_rate"], f"{bs['win_rate']:.1f}",
                              st(align="center", colour=_win_fg(bs["win_rate"]), bold=True)))
        row.addElement(_ncell(spread, f"{spread:.0f}", st(align="center", bold=True)))
        row.addElement(_scell(INDICATOR_UNITS.get(ind, ""), st(align="left")))
        table.addElement(row)

    doc.spreadsheet.addElement(table)


def _buckets_sheet(doc, st, trades):
    table = Table(name="Indicator Values")
    cols = ["4.5cm"] + ["2.2cm", "2cm", "2.4cm", "2.6cm"]
    _add_columns(doc, table, cols, "bv")
    span = len(cols)

    first = True
    for ind in INDICATOR_ORDER:
        pairs = value_pairs(trades, ind)
        if not pairs:
            continue
        if not first:
            table.addElement(TableRow())
        first = False
        table.addElement(_title_row(
            st, f"{ind}  -  {INDICATOR_UNITS.get(ind, '')}", span))
        table.addElement(_header_row(st, ["Value range"] + METRIC_HEADERS))

        label = st(align="left", bold=True)
        for lo, hi, trs in quantile_buckets(pairs):
            row = TableRow()
            row.addElement(_scell(f"{_fmt_val(lo)} - {_fmt_val(hi)}", label))
            for cell in _metric_cells(st, stats(trs)):
                row.addElement(cell)
            table.addElement(row)
        # O gosterge icin "ALL" referans satiri.
        row = TableRow()
        row.addElement(_scell("ALL (this indicator)",
                              st(align="left", background=TOTAL_BG, bold=True)))
        for cell in _metric_cells(st, stats([t for _, t in pairs]), bg=TOTAL_BG):
            row.addElement(cell)
        table.addElement(row)

    doc.spreadsheet.addElement(table)


# ----------------------------------------------------------------------------
# BUILD
# ----------------------------------------------------------------------------
def build_report(results_path, output_path):
    with open(results_path, encoding="utf-8") as fh:
        cache = json.load(fh)
    meta = cache.get("meta", {})
    trades = cache.get("trades", [])

    if not trades:
        raise SystemExit("No trades in the results cache - run backtest.py first.")
    if not any(t.get("values") for t in trades):
        raise SystemExit(
            "These results have no per-trade indicator values. Re-run backtest.py "
            "(it now records a 'values' dict per trade), then retry.")
    # Long-only beklenir; bir uyari basalim ama yine de uretelim.
    shorts = sum(1 for t in trades if t.get("type") == "SHORT")
    if shorts:
        print(f"Note: {shorts} short trades found; this report studies all trades "
              f"but the strategy is meant to be long-only now.")

    doc = OpenDocumentSpreadsheet()
    st = _style_factory(doc)
    _overview_sheet(doc, st, meta, trades)
    _buckets_sheet(doc, st, trades)
    doc.save(output_path)
    return meta, trades


def _print_console(meta, trades):
    """Dosyayi acmadan da gozden gecirilebilsin diye kisa bir metin ozeti."""
    s = stats(trades)
    print(f"\nIndicator value impact  (long-only, {meta.get('timeframe')}, "
          f"{meta.get('days')}d)")
    print("-" * 72)
    print(f"  {s['n']} long trades   overall win {s['win_rate']:.1f}%   "
          f"avg {s['avg']:+.2f}%")
    print("  best value range per indicator (win % in that range):")
    rows = []
    for ind in INDICATOR_ORDER:
        pairs = value_pairs(trades, ind)
        if not pairs:
            continue
        buckets = quantile_buckets(pairs)
        bstats = [(lo, hi, stats(trs)) for lo, hi, trs in buckets]
        if not bstats:
            continue
        lo, hi, bs = max(bstats, key=lambda x: x[2]["win_rate"])
        spread = max(b[2]["win_rate"] for b in bstats) - min(b[2]["win_rate"] for b in bstats)
        rows.append((spread, ind, lo, hi, bs))
    for spread, ind, lo, hi, bs in sorted(rows, reverse=True):
        print(f"    {ind:<5} {_fmt_val(lo):>6} - {_fmt_val(hi):<6}  "
              f"win {bs['win_rate']:>5.1f}%  (n={bs['n']:>3})  "
              f"spread {spread:>4.0f}pts")


def main():
    parser = argparse.ArgumentParser(
        description="Build an indicator-value-impact .ods from the backtest cache.")
    parser.add_argument("--results", default=DEFAULT_RESULTS,
                        help=f"results JSON (default: {os.path.basename(DEFAULT_RESULTS)})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"output .ods (default: {os.path.basename(DEFAULT_OUTPUT)})")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    meta, trades = build_report(args.results, args.output)
    _print_console(meta, trades)
    print("-" * 72)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
