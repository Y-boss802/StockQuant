"""
组合优化与风险模型
==================
在评分板股票池上做长仓组合优化：

  · 协方差：Ledoit-Wolf 收缩（对角目标），年化
  · 预期收益：历史年化均值向横截面中位数收缩
  · 方法：最大夏普 / 最小方差 / 风险平价 / 最大分散
  · 约束：仅做多、单票上限、权重和为 1

输出权重、风险贡献、组合预期收益/波动/夏普、分散化比率与有效持仓数。
结果缓存 30 分钟。预期收益估计误差大，仅供研究。
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
import pandas as pd

import fundamentals
import research

_CACHE: dict[str, tuple[float, Any]] = {}
_RF = 0.015

METHODS = {
    "max_sharpe": "最大夏普",
    "min_var": "最小方差",
    "risk_parity": "风险平价",
    "max_div": "最大分散",
}


def _project(weights: np.ndarray, cap: float) -> np.ndarray:
    w = np.clip(np.asarray(weights, dtype=float), 0, cap)
    if w.sum() <= 0:
        w = np.ones(len(w)) / len(w)
    for _ in range(60):
        w = w / w.sum()
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        free = ~over
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def _project_industry(weights: np.ndarray, cap: float,
                      industries: list[str], ind_cap: float = 0.35) -> np.ndarray:
    w = _project(weights, cap)
    for _ in range(60):
        sums: dict[str, float] = {}
        for i, ind in enumerate(industries):
            sums[ind] = sums.get(ind, 0.0) + w[i]
        over = [ind for ind, s in sums.items() if s > ind_cap + 1e-9]
        if not over:
            break
        for ind in over:
            idx = [i for i, x in enumerate(industries) if x == ind]
            scale = ind_cap / sums[ind]
            for i in idx:
                w[i] *= scale
        excess = 1.0 - w.sum()
        under = [i for i, ind in enumerate(industries) if sums.get(ind, 0) < ind_cap - 1e-9]
        if not under or excess <= 1e-9:
            w = _project(w, cap)
            break
        w[under] += excess / len(under)
        w = _project(w, cap)
    return w


def _shrink_cov(returns: np.ndarray) -> tuple[np.ndarray, float]:
    x = returns - returns.mean(axis=0)
    t, n = x.shape
    sample = x.T @ x / t
    target = np.diag(np.diag(sample))
    pi = float(((x ** 2).T @ (x ** 2) / t - sample ** 2).sum())
    gamma = float(((sample - target) ** 2).sum())
    delta = max(0.0, min(1.0, (pi / gamma) / t)) if gamma > 0 else 0.0
    return (delta * target + (1 - delta) * sample) * 252, delta


def _risk_parity(sigma: np.ndarray, cap: float) -> np.ndarray:
    n = len(sigma)
    w = np.ones(n) / n
    for _ in range(800):
        mrc = sigma @ w
        rc = w * mrc
        target = rc.mean()
        w = w * np.sqrt(target / np.maximum(rc, 1e-14))
        w = _project(w, cap)
    return w


def _max_div(sigma: np.ndarray, cap: float) -> np.ndarray:
    n = len(sigma)
    vol = np.sqrt(np.diag(sigma))
    w = np.ones(n) / n
    for _ in range(1200):
        var = float(w @ sigma @ w)
        if var <= 0:
            break
        pvol = math.sqrt(var)
        dr = float(w @ vol) / pvol
        grad = vol / pvol - (float(w @ vol) / (var ** 1.5)) * (sigma @ w)
        w = _project(w + 0.03 * grad, cap)
    return w


def run_portfolio(market_key: str = "cn", method: str = "max_sharpe",
                  size: int = 20, cap: float = 0.25) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    method = method if method in METHODS else "max_sharpe"
    size = max(8, min(int(size), 40))
    cap = max(0.1, min(float(cap), 0.6))
    if cap * size < 1:
        cap = min(0.6, 1.05 / size)

    key = f"portfolio:{market_key}:{method}:{size}:{cap:.2f}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    items, block = research._universe(market_key, size)  # noqa: SLF001
    if block:
        return {"status": "computing", "market": market_key,
                "progress": block.get("progress", 0), "total": block.get("total", 0)}
    if len(items) < 6:
        return {"status": "error", "error": "股票池不足"}

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
    if len(frames) < 6:
        return {"status": "error", "error": "历史数据不足"}

    close = pd.DataFrame({s: df["close"] for s, df in frames.items()}).sort_index()
    rets = close.pct_change()
    rets = rets.dropna(axis=1, thresh=int(len(rets) * 0.8)).dropna()
    if len(rets) < 120:
        return {"status": "error", "error": "收益样本不足"}

    symbols = list(rets.columns)
    names = {it["symbol"]: it for it in items}
    r = rets.to_numpy()
    sigma, delta = _shrink_cov(r)

    mu = r.mean(axis=0) * 252
    mu = 0.7 * mu + 0.3 * float(np.median(mu))
    mu = np.clip(mu, -0.5, 1.0)

    n = len(symbols)
    if method == "min_var":
        raw = np.linalg.pinv(sigma) @ np.ones(n)
    elif method == "risk_parity":
        raw = _risk_parity(sigma, cap)
    elif method == "max_div":
        raw = _max_div(sigma, cap)
    else:
        excess = mu - _RF
        raw = np.linalg.pinv(sigma) @ excess
        if raw.sum() <= 0:
            raw = np.linalg.pinv(sigma) @ np.ones(n)
    weights = _project(raw, cap)
    ind_map = fundamentals.industry_map(market_key, items)
    industries = [ind_map.get(names.get(s, {}).get("code", ""), "其他") for s in symbols]
    weights = _project_industry(weights, cap, industries)

    port_ret = float(weights @ mu)
    port_var = float(weights @ sigma @ weights)
    port_vol = math.sqrt(max(port_var, 0))
    sharpe = (port_ret - _RF) / port_vol if port_vol > 0 else 0.0
    mrc = sigma @ weights
    rc = weights * mrc
    rc_pct = rc / rc.sum() if rc.sum() > 0 else np.zeros(n)
    stock_vol = np.sqrt(np.diag(sigma))
    div_ratio = float(weights @ stock_vol) / port_vol if port_vol > 0 else 0.0
    hhi = float((weights ** 2).sum())

    rows = []
    for i, sym in enumerate(symbols):
        item = names.get(sym, {})
        rows.append({
            "symbol": sym,
            "code": item.get("code", sym),
            "name": item.get("name", sym),
            "weight": round(float(weights[i]) * 100, 2),
            "risk": round(float(rc_pct[i]) * 100, 2),
            "industry": industries[i],
        })
    rows.sort(key=lambda x: x["weight"], reverse=True)

    result = {
        "status": "ok",
        "market": market_key,
        "method": method,
        "method_name": METHODS[method],
        "n_stocks": n,
        "cap": round(cap, 2),
        "shrinkage": round(delta, 3),
        "industry_cap": 0.35,
        "stats": {
            "expected_return": round(port_ret * 100, 2),
            "volatility": round(port_vol * 100, 2),
            "sharpe": round(sharpe, 2),
            "diversification": round(div_ratio, 2),
            "effective_n": round(1 / hhi, 1) if hhi > 0 else 0,
            "max_weight": round(float(weights.max()) * 100, 1),
        },
        "weights": rows,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE[key] = (time.time() + 1800, result)
    return result
