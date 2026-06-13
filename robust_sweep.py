#!/usr/bin/env python3
"""Parameter-neighbourhood robustness sweep for the P1A3 live config.

Re-runs backtest.py over the SAME cached 90d/15m candles while varying ONE
entry-gate threshold at a time around the P1A3 centre, holding the htf exit
constant. Goal: decide whether P1A3's positive net expectancy is a broad,
trustworthy plateau or a single knife-edge point (overfit).

Each combo is run in a FRESH subprocess because signal_analyzer reads its gate
thresholds from the environment at import time. All runs replay the identical
candle cache (no network), so differences are purely the gate change.

Usage:
    python3 robust_sweep.py
    python3 robust_sweep.py --cache backtests/candles_90d_15m.pkl
"""
import argparse
import collections
import json
import os
import subprocess
import sys

# P1A3 centre — the validated live config (matches signal_analyzer defaults).
CENTER = {
    "CT_REVERSION_LONG": "0",
    "CT_EXIT_MODE": "htf",
    "CT_VWAP_MIN": "1.5",
    "CT_MACD_MIN": "0.08",
    "CT_VOL_MULT": "3.8",
    "CT_MIN_AGREE": "3",
}

# One-at-a-time neighbourhood: vary each gate across nearby values, others fixed.
AXES = {
    "CT_VWAP_MIN":  ["1.0", "1.25", "1.5", "1.75", "2.0"],
    "CT_MACD_MIN":  ["0.05", "0.065", "0.08", "0.10", "0.12"],
    "CT_VOL_MULT":  ["3.0", "3.4", "3.8", "4.2", "4.6"],
    "CT_MIN_AGREE": ["2", "3", "4"],
}

TMP_ODS = "backtests/_sweep_tmp.ods"
TMP_JSON = "backtests/_sweep_tmp.json"


def run_combo(env_overrides, cache):
    """Run backtest.py once with the given gate env, return computed metrics."""
    env = dict(os.environ)
    env.update(env_overrides)
    # Quiet the per-coin progress; we only need the JSON it writes.
    subprocess.run(
        [sys.executable, "backtest.py",
         "--candle-cache", cache,
         "--output", TMP_ODS],
        env=env, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with open(TMP_JSON) as fh:
        data = json.load(fh)

    trades = data["trades"]
    summaries = data["summaries"]
    n = len(trades)
    if n == 0:
        return {"n": 0, "avg": 0.0, "total": 0.0, "win": 0.0,
                "beat": 0, "ntested": len(summaries),
                "ex_top_avg": 0.0, "top3_share": 0.0}

    pnls = [t["pnl"] for t in trades]
    total = sum(pnls)
    avg = total / n
    wins = sum(1 for p in pnls if p > 0)
    win = wins / n * 100.0
    beat = sum(1 for s in summaries if s["diff"] > 0)

    # Single-coin dependence: drop the coin contributing the most total pnl,
    # recompute avg over the rest. If that flips strongly negative, the edge
    # leans on one coin.
    bycoin = collections.defaultdict(lambda: [0, 0.0])
    for t in trades:
        bycoin[t["symbol"]][0] += 1
        bycoin[t["symbol"]][1] += t["pnl"]
    ranked = sorted(bycoin.items(), key=lambda kv: -kv[1][1])
    top1 = ranked[0]
    n_ex = n - top1[1][0]
    ex_top_avg = (total - top1[1][1]) / n_ex if n_ex else 0.0
    top3_sum = sum(v[1] for _, v in ranked[:3])
    top3_share = (top3_sum / total * 100.0) if total else 0.0

    return {"n": n, "avg": avg, "total": total, "win": win,
            "beat": beat, "ntested": len(summaries),
            "ex_top_avg": ex_top_avg, "top3_share": top3_share,
            "top1_sym": top1[0]}


def fmt_row(label, m, is_center=False):
    mark = " *" if is_center else "  "
    return (f"{mark}{label:<14} "
            f"n={m['n']:>3}  "
            f"avg={m['avg']:+.3f}%  "
            f"tot={m['total']:+7.2f}%  "
            f"win={m['win']:4.1f}%  "
            f"beatBH={m['beat']:>2}/{m['ntested']}  "
            f"exTop={m['ex_top_avg']:+.3f}%  "
            f"top3={m['top3_share']:5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="backtests/candles_90d_15m.pkl")
    args = ap.parse_args()

    if not os.path.exists(args.cache):
        sys.exit(f"candle cache not found: {args.cache}")

    print("Robustness sweep around P1A3 (htf exit, 90d/15m, net of fees).")
    print("'avg' = net pnl per trade; cost is already baked in (positive = edge).")
    print("'exTop' = avg/trade after dropping the single best-summing coin.")
    print("'top3' = share of total pnl from the 3 best coins (high = fragile).\n")

    # Centre once.
    center_metrics = run_combo(CENTER, args.cache)
    print("CENTRE (P1A3):")
    print(fmt_row("VWAP1.5/M.08/V3.8/A3", center_metrics, is_center=True))
    print()

    for param, values in AXES.items():
        print(f"=== vary {param} (others at P1A3) ===")
        for v in values:
            if str(v) == str(CENTER[param]):
                print(fmt_row(f"{param}={v}", center_metrics, is_center=True))
                continue
            env = dict(CENTER)
            env[param] = str(v)
            m = run_combo(env, args.cache)
            print(fmt_row(f"{param}={v}", m))
        print()

    # Clean up temp artefacts.
    for f in (TMP_ODS, TMP_JSON):
        try:
            os.remove(f)
        except OSError:
            pass


if __name__ == "__main__":
    main()
