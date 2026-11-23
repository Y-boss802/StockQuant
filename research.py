"""
横截面因子研究
==============
在一个股票池上按固定周期调仓，检验因子的横截面预测能力：

  · IC / RankIC：每期因子值与下一期收益的 Spearman 相关
  · ICIR：IC 均值 / IC 标准差
  · 五分层：按因子值分 5 组，等权持有，统计年化收益
  · 多空：Q5 − Q1，含换手与单边 12bp 成本
  · walk-forward：前 60% 样本内 / 后 40% 样本外 IC

股票池取评分板前 N（默认 40），数据为雪球最近 500 根日线。
结果缓存 1 小时。存在幸存者偏差，仅用于研究。
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pandas as pd

import market
import scoreboard

_CACHE: dict[str, tuple[float, Any]] = {}
_REBAL = 21
_COST = 0.0012

FACTORS = {
    "mom20": "20 日动量",
    "mom60": "60 日动量",
    "rev5": "5 日反转",
    "vol20": "低波动",
    "liq": "流动性",
}


def _universe(market_key: str, size: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    board = scoreboard.get_scoreboard(market_key)
    if board.get("status") != "ok":
        return [], board
    return (board.get("items") or [])[:size], None


def _hist(symbol: str) -> pd.DataFrame | None:
    ticker = symbol[2:] if symbol.startswith("us") else symbol
    try:
        recs = market._fetch_xueqiu_daily(ticker.upper(), count=500)  # noqa: SLF001
    except market.MarketError:
        return None
    if len(recs) < 200:
        return None
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()[["close", "volume"]]


def _factor_series(df: pd.DataFrame, key: str) -> pd.Series:
    close = df["close"]
    ret = close.pct_change()
    if key == "mom20":
        return close / close.shift(20) - 1
    if key == "mom60":
        return close / close.shift(60) - 1
    if key == "rev5":
        return -(close / close.shift(5) - 1)
    if key == "vol20":
        return -ret.rolling(20).std()
    # 流动性：20 日均成交额对数
    return np.log1p((df["volume"] * close).rolling(20).mean())


def _ic(factor: pd.Series, forward: pd.Series) -> float | None:
    pair = pd.concat([factor, forward], axis=1).dropna()
    if len(pair) < 8:
        return None
    return float(pair.iloc[:, 0].rank().corr(pair.iloc[:, 1].rank()))


def run_research(market_key: str = "cn", factor_key: str = "mom20",
                 size: int = 40) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    factor_key = factor_key if factor_key in FACTORS else "mom20"
    size = max(10, min(int(size), 60))
    cache_key = f"research:{market_key}:{factor_key}:{size}"
    hit = _CACHE.get(cache_key)
    if hit and hit[0] > time.time():
        return hit[1]

    items, block = _universe(market_key, size)
    if block:
        return {"status": "computing", "market": market_key,
                "progress": block.get("progress", 0), "total": block.get("total", 0)}
    if len(items) < 10:
        return {"status": "error", "error": "股票池不足，请稍后重试"}

    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_hist, it["symbol"]): it for it in items}
        for fut, it in futures.items():
            df = fut.result()
            if df is not None:
                frames[it["symbol"]] = df

    if len(frames) < 10:
        return {"status": "error", "error": "历史数据不足，无法研究"}

    close = pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index()
    close = close.dropna(axis=1, thresh=int(len(close) * 0.8))
    dates = close.index
    factors_all = {s: _factor_series(df.reindex(dates), factor_key) for s, df in frames.items()}
    factor_df = pd.DataFrame(factors_all).reindex(dates)

    periods = []
    turnover_list = []
    prev_top: set[str] | None = None
    for i in range(70, len(dates) - _REBAL, _REBAL):
        f = factor_df.iloc[i].dropna()
        fwd = (close.iloc[i + _REBAL] / close.iloc[i] - 1).dropna()
        pair = pd.DataFrame({"f": f, "r": fwd}).dropna()
        if len(pair) < 8:
            continue
        ic = _ic(pair["f"], pair["r"])
        if ic is None:
            continue
        ranked = pair.sort_values("f", ascending=False)
        parts = np.array_split(np.arange(len(ranked)), 5)
        q_rets = [float(ranked["r"].iloc[p].mean()) for p in parts]
        ls = q_rets[0] - q_rets[-1]
        top = set(ranked.index[: max(1, len(ranked) // 5)])
        turnover = 1.0 if prev_top is None else len(top - prev_top) / max(1, len(top))
        prev_top = top
        turnover_list.append(turnover)
        periods.append({
            "date": str(dates[i].date()),
            "ic": round(ic, 4),
            "q": [round(x, 4) for x in q_rets],
            "ls": round(ls - turnover * 2 * _COST, 4),
            "turnover": round(turnover, 3),
        })

    if len(periods) < 6:
        return {"status": "error", "error": "有效调仓期不足，无法统计"}

    ics = np.array([p["ic"] for p in periods])
    ls_series = np.array([p["ls"] for p in periods])
    q_series = np.array([p["q"] for p in periods])
    split = max(3, int(len(periods) * 0.6))
    is_ic = float(ics[:split].mean())
    os_ic = float(ics[split:].mean()) if len(periods) - split >= 2 else None
    periods_per_year = 252 / _REBAL

    curves = []
    cum = np.ones(5)
    for p in periods:
        cum = cum * (1 + np.array(p["q"]))
        curves.append({"date": p["date"],
                       "q": [round(float(x) - 1, 4) for x in cum]})

    quant = []
    for q in range(5):
        avg = float(q_series[:, q].mean())
        ann = (1 + avg) ** periods_per_year - 1 if avg > -1 else -1
        quant.append({
            "group": f"Q{5 - q}" if False else f"Q{q + 1}",
            "avg": round(avg * 100, 2),
            "ann": round(ann * 100, 2),
        })
    # 因子值越高越好，Q1 为因子最高组
    quant = [
        {"group": "Q1(高)", "avg": round(float(q_series[:, 0].mean()) * 100, 2),
         "ann": round(((1 + float(q_series[:, 0].mean())) ** periods_per_year - 1) * 100, 2)},
        {"group": "Q2", "avg": round(float(q_series[:, 1].mean()) * 100, 2),
         "ann": round(((1 + float(q_series[:, 1].mean())) ** periods_per_year - 1) * 100, 2)},
        {"group": "Q3", "avg": round(float(q_series[:, 2].mean()) * 100, 2),
         "ann": round(((1 + float(q_series[:, 2].mean())) ** periods_per_year - 1) * 100, 2)},
        {"group": "Q4", "avg": round(float(q_series[:, 3].mean()) * 100, 2),
         "ann": round(((1 + float(q_series[:, 3].mean())) ** periods_per_year - 1) * 100, 2)},
        {"group": "Q5(低)", "avg": round(float(q_series[:, 4].mean()) * 100, 2),
         "ann": round(((1 + float(q_series[:, 4].mean())) ** periods_per_year - 1) * 100, 2)},
    ]

    ls_ann = ((1 + float(ls_series.mean())) ** periods_per_year - 1) * 100
    ls_sharpe = (float(ls_series.mean()) / float(ls_series.std()) * math.sqrt(periods_per_year)
                 if ls_series.std() > 0 else 0)

    result = {
        "status": "ok",
        "market": market_key,
        "factor": factor_key,
        "factor_name": FACTORS[factor_key],
        "n_stocks": len(close.columns),
        "n_periods": len(periods),
        "ic_mean": round(float(ics.mean()), 4),
        "ic_std": round(float(ics.std()), 4),
        "icir": round(float(ics.mean() / ics.std()), 2) if ics.std() > 0 else 0,
        "ic_pos": round(float((ics > 0).mean() * 100), 1),
        "is_ic": round(is_ic, 4),
        "os_ic": round(os_ic, 4) if os_ic is not None else None,
        "ls_ann": round(ls_ann, 2),
        "ls_sharpe": round(ls_sharpe, 2),
        "turnover": round(float(np.mean(turnover_list)) * 100, 1) if turnover_list else 0,
        "cost_bps": 12,
        "quantiles": quant,
        "curve": curves,
        "periods": periods[-12:],
        "universe": [{"symbol": s, "code": frames[s].attrs.get("code", s)}
                     for s in close.columns],
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE[cache_key] = (time.time() + 3600, result)
    return result
