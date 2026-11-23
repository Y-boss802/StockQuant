"""
量化策略：风险调整动量
======================
规则冻结、可复现：

  · 股票池：评分板前 40
  · 每月调仓（21 个交易日）
  · 因子：60 日动量 − 0.5 × 20 日年化波动（风险调整动量）
  · 选前 N 只等权持有
  · 单边成本 12bp，按换手扣减
  · 基准：上证指数 / 标普500

不做行业中性、不含滑点与冲击成本；结果为历史回测，不代表未来。
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pandas as pd

import research

_CACHE: dict[str, tuple[float, Any]] = {}


def run_strategy(market_key: str = "cn", lookback: int = 60,
                 top: int = 10, cost: float = 0.0012) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    lookback = max(20, min(int(lookback), 120))
    top = max(3, min(int(top), 20))
    key = f"strategy:{market_key}:{lookback}:{top}:{cost}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    items, block = research._universe(market_key, 40)  # noqa: SLF001
    if block:
        return {"status": "computing", "market": market_key,
                "progress": block.get("progress", 0), "total": block.get("total", 0)}

    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(research._hist, it["symbol"]): it for it in items}  # noqa: SLF001
        for fut, it in futures.items():
            try:
                df = fut.result()
            except Exception:  # noqa: BLE001
                df = None
            if df is not None:
                frames[it["symbol"]] = df
    if len(frames) < 10:
        return {"status": "error", "error": "历史数据不足"}

    close = pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index()
    close = close.dropna(axis=1, thresh=int(len(close) * 0.8))
    rets = close.pct_change()
    bench = research._hist("sh000001" if market_key == "cn" else "usSPY")  # noqa: SLF001
    if bench is None:
        return {"status": "error", "error": "基准数据不可用"}
    bench_close = bench["close"].reindex(close.index).ffill()
    bench_ret = bench_close.pct_change().fillna(0)

    names = {it["symbol"]: it for it in items}
    vol = rets.rolling(20).std() * math.sqrt(252)
    mom = close / close.shift(lookback) - 1
    factor = mom - 0.5 * vol

    dates = close.index
    start = max(70, lookback + 25)
    holdings: list[str] = []
    prev_w: dict[str, float] = {}
    daily: list[float] = []
    bench_daily: list[float] = []
    turnover_all: list[float] = []
    curve = []
    eq = 1.0
    beq = 1.0
    last_holdings: list[dict[str, Any]] = []
    rebalances = 0

    for i in range(start, len(dates)):
        if (i - start) % 21 == 0:
            row = factor.iloc[i].dropna()
            row = row[row.index.isin(close.columns)]
            if len(row) >= top:
                chosen = list(row.sort_values(ascending=False).index[:top])
                w = {s: 1.0 / len(chosen) for s in chosen}
                turnover = 0.5 * sum(abs(w.get(s, 0) - prev_w.get(s, 0))
                                     for s in set(w) | set(prev_w))
                cost_today = turnover * 2 * cost
                turnover_all.append(turnover)
                holdings = chosen
                prev_w = w
                rebalances += 1
                last_holdings = [
                    {"symbol": s, "code": names.get(s, {}).get("code", s),
                     "name": names.get(s, {}).get("name", s),
                     "weight": round(100 / len(chosen), 2),
                     "factor": round(float(row[s]), 4)}
                    for s in chosen
                ]
            else:
                cost_today = 0.0
        else:
            cost_today = 0.0

        if not holdings:
            daily.append(0.0)
            bench_daily.append(float(bench_ret.iloc[i]))
            continue
        r = float(rets.iloc[i][holdings].fillna(0).mean()) - cost_today
        daily.append(r)
        bench_daily.append(float(bench_ret.iloc[i]))
        eq *= 1 + r
        beq *= 1 + bench_daily[-1]
        curve.append({"date": str(dates[i].date()),
                      "s": round(eq, 4), "b": round(beq, 4)})

    if len(daily) < 60:
        return {"status": "error", "error": "回测区间不足"}

    arr = np.array(daily)
    total = float(np.prod(1 + arr) - 1)
    years = len(arr) / 252
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else 0
    vol_ann = float(arr.std() * math.sqrt(252))
    sharpe = (cagr - 0.015) / vol_ann if vol_ann > 0 else 0
    curve_arr = np.cumprod(1 + arr)
    peaks = np.maximum.accumulate(curve_arr)
    maxdd = float((curve_arr / peaks - 1).min())

    bars = np.array(bench_daily)
    btotal = float(np.prod(1 + bars) - 1)
    bcagr = (1 + btotal) ** (1 / years) - 1 if years > 0 else 0

    result = {
        "status": "ok", "market": market_key,
        "lookback": lookback, "top": top, "cost_bps": round(cost * 10000),
        "n_stocks": len(close.columns), "rebalances": rebalances,
        "stats": {
            "total": round(total * 100, 2),
            "cagr": round(cagr * 100, 2),
            "vol": round(vol_ann * 100, 2),
            "sharpe": round(sharpe, 2),
            "maxdd": round(maxdd * 100, 2),
            "bench_cagr": round(bcagr * 100, 2),
            "excess": round((cagr - bcagr) * 100, 2),
            "turnover": round(float(np.mean(turnover_all)) * 100, 1) if turnover_all else 0,
        },
        "curve": curve,
        "holdings": last_holdings,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE[key] = (time.time() + 1800, result)
    return result
