"""
数据质量体检
============
对股票池逐只检查：K 线长度、缺失率、零成交、极端波动、数据滞后、行业缺失。

评分 0–100，扣分项公开；结果缓存 30 分钟。用于在研究和回测前发现脏数据。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

import fundamentals
import research

_CACHE: dict[str, tuple[float, Any]] = {}
_BENCH = {"cn": "sh000001", "us": "usSPY"}


def _bench_frame(market_key: str) -> pd.DataFrame | None:
    return research._hist(_BENCH[market_key])  # noqa: SLF001


def run_quality(market_key: str = "cn", size: int = 20) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    size = max(5, min(int(size), 40))
    key = f"quality:{market_key}:{size}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    items, block = research._universe(market_key, size)  # noqa: SLF001
    if block:
        return {"status": "computing", "market": market_key,
                "progress": block.get("progress", 0), "total": block.get("total", 0)}
    bench = _bench_frame(market_key)
    if bench is None:
        return {"status": "error", "error": "基准数据不可用"}
    bench_dates = bench.index

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

    industries = fundamentals.industry_map(market_key, items)
    rows = []
    for it in items:
        df = frames.get(it["symbol"])
        issues: list[str] = []
        penalty = 0
        if df is None or len(df) < 200:
            rows.append({
                "symbol": it["symbol"], "code": it["code"], "name": it["name"],
                "score": 0, "bars": 0 if df is None else len(df),
                "missing": None, "zero": None, "extreme": None,
                "industry": industries.get(it["code"], "其他"),
                "issues": ["历史数据不足"], "last": "",
            })
            continue

        bars = len(df)
        if bars < 250:
            penalty += 20
            issues.append(f"K 线仅 {bars} 根")
        last = str(df.index[-1].date())
        bench_last = str(bench_dates[-1].date())
        if last < bench_last:
            penalty += 15
            issues.append(f"数据滞后（{last} vs 基准 {bench_last}）")

        common = bench_dates[(bench_dates >= df.index[0]) & (bench_dates <= df.index[-1])]
        missing = max(0.0, 1 - len(df.index.intersection(common)) / max(1, len(common)))
        if missing > 0.05:
            penalty += 15
            issues.append(f"缺失率 {missing * 100:.1f}%")

        zero = float((df["volume"] <= 0).mean()) if "volume" in df else 0.0
        if zero > 0.05:
            penalty += 10
            issues.append(f"零成交 {zero * 100:.1f}%")

        ret = df["close"].pct_change()
        threshold = 0.11 if market_key == "cn" else 0.2
        extreme = int((ret.abs() > threshold).sum())
        if extreme > 0:
            penalty += min(15, extreme * 5)
            issues.append(f"极端波动 {extreme} 次")

        industry = industries.get(it["code"], "其他")
        if industry in ("其他", "—", ""):
            penalty += 5
            issues.append("行业缺失")

        rows.append({
            "symbol": it["symbol"], "code": it["code"], "name": it["name"],
            "score": max(0, 100 - penalty), "bars": bars,
            "missing": round(missing * 100, 2), "zero": round(zero * 100, 2),
            "extreme": extreme, "industry": industry,
            "issues": issues or ["正常"], "last": last,
        })

    rows.sort(key=lambda r: r["score"])
    result = {
        "status": "ok", "market": market_key, "rows": rows,
        "avg_score": round(sum(r["score"] for r in rows) / len(rows), 1) if rows else 0,
        "bad": sum(1 for r in rows if r["score"] < 70),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE[key] = (time.time() + 1800, result)
    return result
