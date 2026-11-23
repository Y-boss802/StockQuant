"""
策略跟踪
========
把量化策略冻结成一份可验证的纸面组合：

  · 创建时锁定参数（市场、动量窗口、持仓数）与初始持仓
  · 每次打开页面按最新收盘价更新净值
  · 每 21 个交易日调仓一次，扣单边 12bp
  · 记录每日净值与基准，便于和回测对比

数据保存在 data/tracking_<market>.json。仅为前向验证，不构成投资建议。
"""
from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import research

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
_REBAL = 21
_COST = 0.0012
_BENCH = {"cn": "sh000001", "us": "usSPY"}


def _path(market_key: str) -> str:
    return os.path.join(DATA, f"tracking_{market_key}.json")


def _load(market_key: str) -> dict[str, Any] | None:
    try:
        with open(_path(market_key), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _save(market_key: str, state: dict[str, Any]) -> None:
    os.makedirs(DATA, exist_ok=True)
    with open(_path(market_key), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def _panel(market_key: str):
    items, block = research._universe(market_key, 40)  # noqa: SLF001
    if block:
        return None, None, None, block
    frames = {}
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
        return None, None, None, None
    names = {it["symbol"]: it for it in items}
    return frames, names, items, None


def _holdings(frames, names, lookback: int, top: int) -> list[dict[str, Any]]:
    closes = {s: df["close"] for s, df in frames.items()}
    k = max(len(df) for df in frames.values())
    ranked = []
    for sym, close in closes.items():
        if len(close) < lookback + 5:
            continue
        mom = float(close.iloc[-1] / close.iloc[-lookback] - 1)
        ret = close.pct_change().iloc[-20:]
        vol = float(ret.std() * math.sqrt(252)) if len(ret) > 2 else 1.0
        ranked.append((mom - 0.5 * vol, sym, float(close.iloc[-1])))
    ranked.sort(reverse=True)
    out = []
    for _, sym, price in ranked[:top]:
        item = names.get(sym, {})
        out.append({"symbol": sym, "code": item.get("code", sym),
                    "name": item.get("name", sym), "base": price})
    return out


def _bench_close(market_key: str) -> float | None:
    df = research._hist(_BENCH[market_key])  # noqa: SLF001
    if df is None or df.empty:
        return None
    return float(df["close"].iloc[-1])


def create(market_key: str = "cn", lookback: int = 60, top: int = 10) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    frames, names, items, block = _panel(market_key)
    if block:
        return {"status": "computing", "market": market_key,
                "progress": block.get("progress", 0), "total": block.get("total", 0)}
    if not frames:
        return {"status": "error", "error": "数据不足"}
    holdings = _holdings(frames, names, lookback, top)
    if len(holdings) < 3:
        return {"status": "error", "error": "可用持仓不足"}
    weights = {h["symbol"]: 1.0 / len(holdings) for h in holdings}
    today = datetime.now().strftime("%Y-%m-%d")
    state = {
        "market": market_key, "lookback": lookback, "top": top,
        "start": today, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "holdings": holdings, "weights": weights,
        "bench_base": _bench_close(market_key),
        "points": [{"date": today, "nav": 1.0, "bench": 1.0}],
        "last_rebalance": 0,
    }
    _save(market_key, state)
    return get(market_key)


def get(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    state = _load(market_key)
    if not state:
        return {"status": "empty", "market": market_key,
                "message": "还没有跟踪中的策略，先在策略页点「开始跟踪」。"}
    points = state.get("points", [])
    nav = points[-1]["nav"] if points else 1.0
    bench = points[-1].get("bench", 1.0) if points else 1.0
    peak = 0.0
    maxdd = 0.0
    for p in points:
        peak = max(peak, p["nav"])
        if peak > 0:
            maxdd = min(maxdd, p["nav"] / peak - 1)
    holdings = []
    for h in state.get("holdings", []):
        w = state.get("weights", {}).get(h["symbol"], 0)
        holdings.append({**h, "weight": round(w * 100, 1)})
    return {
        "status": "ok", "market": market_key,
        "start": state.get("start"), "created": state.get("created"),
        "lookback": state.get("lookback"), "top": state.get("top"),
        "days": len(points),
        "nav": round(nav, 4),
        "bench": round(bench, 4),
        "return": round((nav - 1) * 100, 2),
        "excess": round((nav - bench) * 100, 2),
        "maxdd": round(maxdd * 100, 2),
        "points": points[-120:],
        "holdings": holdings,
        "updated": points[-1]["date"] if points else "",
    }


def reset(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    try:
        os.remove(_path(market_key))
    except OSError:
        pass
    return get(market_key)


def update(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    state = _load(market_key)
    if not state:
        return get(market_key)
    frames, names, items, block = _panel(market_key)
    if block or not frames:
        return get(market_key)
    today = datetime.now().strftime("%Y-%m-%d")
    points = state.get("points", [])
    if points and points[-1]["date"] == today:
        return get(market_key)

    prices = {}
    for h in state.get("holdings", []):
        df = frames.get(h["symbol"])
        if df is not None and len(df):
            prices[h["symbol"]] = float(df["close"].iloc[-1])
    if not prices:
        return get(market_key)

    total = 0.0
    weight_sum = 0.0
    for h in state.get("holdings", []):
        sym = h["symbol"]
        if sym not in prices:
            continue
        w = state.get("weights", {}).get(sym, 0)
        base = float(h.get("base") or prices[sym])
        total += w * (prices[sym] / base - 1) if base else 0
        weight_sum += w
    if weight_sum <= 0:
        return get(market_key)
    port_ret = total / weight_sum

    bench_now = _bench_close(market_key)
    last = points[-1]
    bench_ret = 0.0
    if bench_now and state.get("bench_base"):
        bench_ret = bench_now / state["bench_base"] - 1
    nav = float(last["nav"]) * (1 + port_ret)
    bench = float(last.get("bench", 1.0)) * (1 + bench_ret)

    # 21 个跟踪日调仓
    if len(points) - int(state.get("last_rebalance", 0)) >= _REBAL:
        new_holdings = _holdings(frames, names, int(state.get("lookback", 60)),
                                 int(state.get("top", 10)))
        if len(new_holdings) >= 3:
            nav *= 1 - _COST * 2
            state["holdings"] = new_holdings
            state["weights"] = {h["symbol"]: 1.0 / len(new_holdings) for h in new_holdings}
            state["last_rebalance"] = len(points)
    else:
        state["holdings"] = [
            {**h, "base": prices.get(h["symbol"], h.get("base"))}
            for h in state.get("holdings", [])
        ]
    state["bench_base"] = bench_now or state.get("bench_base")
    points.append({"date": today, "nav": round(nav, 6), "bench": round(bench, 6)})
    state["points"] = points
    _save(market_key, state)
    return get(market_key)
