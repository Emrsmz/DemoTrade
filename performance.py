#!/usr/bin/env python3
"""
================================================================================
 PERFORMANCE REPORT  -  WIN-RATE AND PROFIT STATS FROM THE TRADE LOG
================================================================================

This module reads the .ods trade log written by trade_logger.py and turns the
closed trades into a performance report, so the strategy can be judged on its
actual results rather than gut feeling.

WHAT IT REPORTS
---------------
  * How many trades are closed vs still open.
  * Win rate: the share of closed trades that ended in profit.
  * Total and average profit (as a percentage), plus the average win and the
    average loss separately.
  * The single best and worst trade.
  * A Long-vs-Short breakdown, so it is clear which direction works better.
  * A per-coin table ranked by total profit.

HOW PROFIT IS MEASURED
----------------------
Profit is recomputed from the entry and close prices (not parsed from the
"Result" text), using the trade's direction:
    Long  profit% = (close - entry) / entry * 100
    Short profit% = (entry - close) / entry * 100
Totals SUM these percentages, which assumes the same position size on every
trade - a simple, position-size-independent way to compare the signals.

WHERE IT IS USED
----------------
  * Terminal:  python signal_analyzer.py --stats
  * Telegram:  the /istatistik command of the --bot mode.

DISCLAIMER: For educational purposes only. Past performance is not indicative of
future results.
================================================================================
"""

from trade_logger import _read_records


# ----------------------------------------------------------------------------
# CORE COMPUTATION
# ----------------------------------------------------------------------------
def _profit_pct(record):
    """Return a closed trade's profit as a percentage, signed by direction."""
    entry = record.get("entry")
    close = record.get("close")
    if not entry or close is None:
        return None
    if record.get("position") == "Short":
        return (entry - close) / entry * 100
    return (close - entry) / entry * 100


def _direction_stats(trades):
    """Aggregate count, wins, win-rate and total profit for a list of trades."""
    count = len(trades)
    profits = [_profit_pct(t) for t in trades]
    wins = sum(1 for p in profits if p is not None and p >= 0)
    total = sum(p for p in profits if p is not None)
    return {
        "count": count,
        "wins": wins,
        "losses": count - wins,
        "win_rate": (wins / count * 100) if count else 0.0,
        "total_profit": total,
        "avg_profit": (total / count) if count else 0.0,
    }


def compute_stats(records):
    """Turn raw trade records into a structured performance summary dict."""
    closed = [r for r in records if _profit_pct(r) is not None]
    open_count = len(records) - len(closed)

    profits = [(_profit_pct(r), r) for r in closed]
    wins = [p for p, _ in profits if p >= 0]
    losses = [p for p, _ in profits if p < 0]
    total_profit = sum(p for p, _ in profits)

    def label(record):
        return f"{record['base']}/{record['quote']} {record['position']}"

    best = max(profits, key=lambda pr: pr[0], default=None)
    worst = min(profits, key=lambda pr: pr[0], default=None)

    # Per-coin aggregation, ranked by total profit (most profitable first).
    by_coin = {}
    for profit, record in profits:
        by_coin.setdefault(record["base"], []).append(profit)
    per_coin = [
        {
            "base": base,
            "count": len(ps),
            "wins": sum(1 for p in ps if p >= 0),
            "win_rate": sum(1 for p in ps if p >= 0) / len(ps) * 100,
            "total_profit": sum(ps),
        }
        for base, ps in by_coin.items()
    ]
    per_coin.sort(key=lambda c: c["total_profit"], reverse=True)

    return {
        "total": len(records),
        "closed": len(closed),
        "open": open_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closed) * 100) if closed else 0.0,
        "total_profit": total_profit,
        "avg_profit": (total_profit / len(closed)) if closed else 0.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "best": ({"label": label(best[1]), "pct": best[0]} if best else None),
        "worst": ({"label": label(worst[1]), "pct": worst[0]} if worst else None),
        "long": _direction_stats([r for r in closed if r.get("position") == "Long"]),
        "short": _direction_stats([r for r in closed if r.get("position") == "Short"]),
        "per_coin": per_coin,
    }


def load_stats(path):
    """Read the trade log at `path` and return its performance stats dict."""
    return compute_stats(_read_records(path))


# ----------------------------------------------------------------------------
# FORMATTING
# ----------------------------------------------------------------------------
def _sign(value):
    """Format a percentage with an explicit sign, e.g. '+1.23%' / '-0.45%'."""
    return f"{value:+.2f}%"


def format_text(stats, path=""):
    """Build a coloured terminal report. colorama must already be initialised."""
    from colorama import Fore, Style

    if stats["closed"] == 0:
        head = f"{Style.BRIGHT}{Fore.CYAN}PERFORMANCE REPORT{Style.RESET_ALL}"
        return (
            f"{head}\n"
            f"No closed trades yet ({stats['open']} still open). "
            f"Stats appear once trades reach their exit time."
        )

    def pct_colour(value):
        return Fore.GREEN if value >= 0 else Fore.RED

    lines = []
    src = f"  ({path})" if path else ""
    lines.append(f"{Style.BRIGHT}{Fore.CYAN}PERFORMANCE REPORT{src}{Style.RESET_ALL}")
    lines.append(
        f"Trades   : {stats['closed']} closed, {stats['open']} open"
    )
    lines.append(
        f"Win rate : {Fore.GREEN}{stats['wins']} W{Style.RESET_ALL} / "
        f"{Fore.RED}{stats['losses']} L{Style.RESET_ALL}  "
        f"({stats['win_rate']:.0f}%)"
    )
    tp = stats["total_profit"]
    lines.append(
        f"Total P/L: {pct_colour(tp)}{_sign(tp)}{Style.RESET_ALL}  "
        f"(avg {_sign(stats['avg_profit'])} per trade)"
    )
    lines.append(
        f"Avg win  : {Fore.GREEN}{_sign(stats['avg_win'])}{Style.RESET_ALL}   "
        f"Avg loss : {Fore.RED}{_sign(stats['avg_loss'])}{Style.RESET_ALL}"
    )
    if stats["best"]:
        b = stats["best"]
        lines.append(
            f"Best     : {Fore.GREEN}{b['label']} {_sign(b['pct'])}{Style.RESET_ALL}"
        )
    if stats["worst"]:
        w = stats["worst"]
        lines.append(
            f"Worst    : {Fore.RED}{w['label']} {_sign(w['pct'])}{Style.RESET_ALL}"
        )

    for name in ("long", "short"):
        d = stats[name]
        if d["count"]:
            lines.append(
                f"{name.capitalize():<9}: {d['count']} trades, "
                f"{d['win_rate']:.0f}% win, "
                f"{pct_colour(d['total_profit'])}{_sign(d['total_profit'])}"
                f"{Style.RESET_ALL}"
            )

    if stats["per_coin"]:
        lines.append(f"{Style.DIM}By coin (top profit first):{Style.RESET_ALL}")
        for coin in stats["per_coin"]:
            lines.append(
                f"  {coin['base']:<6} {coin['count']:>2} trades  "
                f"{coin['win_rate']:>3.0f}% win  "
                f"{pct_colour(coin['total_profit'])}{_sign(coin['total_profit'])}"
                f"{Style.RESET_ALL}"
            )
    return "\n".join(lines)


def format_telegram(stats):
    """Build a Turkish HTML report for Telegram."""
    if stats["closed"] == 0:
        return (
            "\U0001F4CA <b>PERFORMANS RAPORU</b>\n"
            f"Henuz kapanmis islem yok ({stats['open']} acik). "
            "Istatistikler islemler cikis saatine ulasinca olusur."
        )

    def emoji(value):
        return "\U0001F7E2" if value >= 0 else "\U0001F534"  # green/red circle

    tp = stats["total_profit"]
    lines = [
        "\U0001F4CA <b>PERFORMANS RAPORU</b>",
        f"\U0001F4DD <b>Islem:</b> {stats['closed']} kapali, {stats['open']} acik",
        f"\U0001F3AF <b>Basari:</b> {stats['wins']} W / {stats['losses']} L "
        f"(%{stats['win_rate']:.0f})",
        f"{emoji(tp)} <b>Toplam K/Z:</b> {_sign(tp)} "
        f"(islem basi ort. {_sign(stats['avg_profit'])})",
        f"\U0001F4C8 <b>Ort. kazanc:</b> {_sign(stats['avg_win'])}   "
        f"\U0001F4C9 <b>Ort. kayip:</b> {_sign(stats['avg_loss'])}",
    ]
    if stats["best"]:
        b = stats["best"]
        lines.append(f"\U0001F3C6 <b>En iyi:</b> {b['label']} {_sign(b['pct'])}")
    if stats["worst"]:
        w = stats["worst"]
        lines.append(f"\U0001F9CA <b>En kotu:</b> {w['label']} {_sign(w['pct'])}")

    for name, tr in (("Long", stats["long"]), ("Short", stats["short"])):
        if tr["count"]:
            lines.append(
                f"{emoji(tr['total_profit'])} <b>{name}:</b> {tr['count']} islem, "
                f"%{tr['win_rate']:.0f} basari, {_sign(tr['total_profit'])}"
            )

    if stats["per_coin"]:
        lines.append("\n<b>Coin bazli (en karli ustte):</b>")
        for coin in stats["per_coin"]:
            lines.append(
                f"{emoji(coin['total_profit'])} {coin['base']}: {coin['count']} islem, "
                f"%{coin['win_rate']:.0f}, {_sign(coin['total_profit'])}"
            )
    return "\n".join(lines)


# A quick manual check: `python performance.py [trade_log.ods]` prints the report.
if __name__ == "__main__":
    import sys
    from colorama import init

    init(autoreset=True)
    log_path = sys.argv[1] if len(sys.argv) > 1 else "trade_log.ods"
    print(format_text(load_stats(log_path), path=log_path))
