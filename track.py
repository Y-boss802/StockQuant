"""
预测存档与复盘
==============
预测在收盘后生成，按「基准交易日」存档；复盘读取存档，取预测日之后第一个
交易日的完整 K 线，计算实际涨跌，并给出：
  · 差异原因（大盘、跳空、日内反转、波动超区间）
  · 次日变化估测（收盘位置、5 日动量、ATR 推演）
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import market
import session

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data", "predictions")

_cache: dict[str, dict[str, Any]] = {}


def save_prediction(market_key: str, data: dict[str, Any],
                    date: str | None = None) -> None:
    """把一次预测结果落盘，同一天同市场只保留第一次。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        day = date or str(data.get("updated", ""))[:10]
        if not day:
            return
        path = os.path.join(DATA_DIR, f"{day}_{market_key}.json")
        if os.path.exists(path):
            return
        items = []
        for side in ("up", "down"):
            for x in data.get(side, []):
                items.append({
                    "symbol": x["symbol"], "code": x["code"], "name": x["name"],
                    "side": side, "price": x.get("price"),
                    "pred_pct": x.get("pred_pct"), "score": x.get("score"),
                    "reason": x.get("reason"), "basis": x.get("basis"),
                })
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"date": day, "market": market_key,
                       "label": data.get("label", market_key),
                       "updated": data.get("updated"), "items": items},
                      fh, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001
        pass


def latest_snapshot(market_key: str, before: str | None = None) -> dict[str, Any] | None:
    for name in _files(market_key):
        day = name[:10]
        if before and day > before:
            continue
        try:
            with open(os.path.join(DATA_DIR, name), encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001
            continue
    return None


def _files(market_key: str) -> list[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    files = [f for f in os.listdir(DATA_DIR) if f.endswith(f"_{market_key}.json")]
    files.sort(reverse=True)
    return files


def _bar(symbol: str, after: str) -> dict[str, Any] | None:
    """预测日之后第一根完整日线，以及前收盘与近期序列。"""
    key = f"{symbol}:{after}"
    if key in _cache:
        return _cache[key]
    ticker = symbol[2:] if symbol.startswith("us") else symbol
    try:
        recs = market._fetch_xueqiu_daily(ticker, count=80)  # noqa: SLF001
    except market.MarketError:
        return None
    for i, row in enumerate(recs):
        if row["date"] > after:
            prev = float(recs[i - 1]["close"]) if i > 0 else float(row["close"])
            result = {
                "date": row["date"],
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume") or 0),
                "prev_close": prev,
                "recent": recs[max(0, i - 30): i + 1],
            }
            _cache[key] = result
            return result
    return None


def _bench_pct(market_key: str, day: str) -> float | None:
    key = f"bench:{market_key}:{day}"
    if key in _cache:
        return _cache[key]
    ticker = "SH000001" if market_key == "cn" else "SPY"
    try:
        recs = market._fetch_xueqiu_daily(ticker, count=30)  # noqa: SLF001
    except market.MarketError:
        return None
    for i, row in enumerate(recs):
        if row["date"] == day and i > 0:
            prev = float(recs[i - 1]["close"])
            pct = (float(row["close"]) / prev - 1) * 100 if prev else None
            if pct is not None:
                _cache[key] = pct
            return pct
    return None


def _reason(hit: bool, gap: float, intraday: float, bench: float | None,
            amp: float, pred_range: float) -> str:
    parts: list[str] = []
    if bench is not None and abs(bench) >= 0.8:
        parts.append(f"大盘{'上涨' if bench > 0 else '下跌'} {bench:+.2f}%")
    if gap > 0.6 and intraday < -0.5:
        parts.append(f"高开 {gap:+.1f}% 后回落 {intraday:+.1f}%")
    elif gap < -0.6 and intraday > 0.5:
        parts.append(f"低开 {gap:+.1f}% 后回升 {intraday:+.1f}%")
    if pred_range > 0 and amp > pred_range * 2.2:
        parts.append(f"振幅 {amp:.1f}% 超出预测区间 ±{pred_range:.1f}%")
    if hit:
        return "方向一致" + ("：" + "；".join(parts) if parts else "") + "。"
    if not parts:
        parts.append("日内路径与形态信号相反")
    return "方向相反：" + "；".join(parts) + "。"


def _next_estimate(bar: dict[str, Any]) -> dict[str, Any]:
    recs = bar["recent"]
    closes = [float(r["close"]) for r in recs]
    atrs = []
    for i in range(1, len(recs)):
        r = recs[i]
        prev = float(recs[i - 1]["close"]) or float(r["close"])
        atrs.append((float(r["high"]) - float(r["low"])) / prev * 100)
    atr = sum(atrs[-14:]) / max(1, len(atrs[-14:])) if atrs else 2.0
    rng = max(0.3, bar["high"] - bar["low"])
    pos = (bar["close"] - bar["low"]) / rng if rng > 0 else 0.5
    mom5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0
    bias = 0.6 * (pos - 0.5) + 0.4 * max(-1.0, min(1.0, mom5 / 5.0))
    est = bias * atr * 1.2
    est = max(-atr * 1.5, min(atr * 1.5, est))
    tone = "偏多" if est > 0.2 else "偏空" if est < -0.2 else "震荡"
    return {
        "pct": round(est, 1),
        "range": round(atr * 0.6, 1),
        "tone": tone,
        "pos": round(pos * 100, 1),
        "mom5": round(mom5, 2),
        "atr": round(atr, 2),
        "text": f"次日{tone}，预计 {est:+.1f}%（±{atr * 0.6:.1f}%）",
    }


def get_review(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    files = _files(market_key)
    if not files:
        return {"status": "empty", "market": market_key,
                "message": "还没有预测存档，先到「预测」页生成一次。"}

    state = session.state(market_key)
    for name in files:  # 从最新日期往前找
        try:
            with open(os.path.join(DATA_DIR, name), encoding="utf-8") as fh:
                snap = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        records = []
        for item in snap.get("items", []):
            bar = _bar(item["symbol"], snap["date"])
            price = float(item.get("price") or 0)
            if not bar or price <= 0:
                continue
            # 盘中当日 K 线不完整，只对比已收盘的交易日
            if state["state"] == "open" and bar["date"] == state["now"][:10]:
                continue
            actual_pct = (bar["close"] / price - 1) * 100
            pred = float(item.get("pred_pct") or 0)
            hit = (pred > 0 and actual_pct > 0) or (pred < 0 and actual_pct < 0)
            gap = (bar["open"] / bar["prev_close"] - 1) * 100 if bar["prev_close"] else 0.0
            intraday = (bar["close"] / bar["open"] - 1) * 100 if bar["open"] else 0.0
            amp = (bar["high"] - bar["low"]) / bar["prev_close"] * 100 if bar["prev_close"] else 0.0
            bench = _bench_pct(market_key, bar["date"])
            records.append({
                "symbol": item["symbol"], "code": item["code"],
                "name": item["name"], "side": item["side"],
                "pred_pct": round(pred, 2),
                "actual_pct": round(actual_pct, 2),
                "error": round(actual_pct - pred, 2),
                "hit": hit,
                "date": bar["date"],
                "price": price,
                "close": round(bar["close"], 2),
                "gap": round(gap, 2),
                "intraday": round(intraday, 2),
                "amp": round(amp, 2),
                "bench": round(bench, 2) if bench is not None else None,
                "reason": _reason(hit, gap, intraday, bench, amp,
                                  float(item.get("plan_range") or 0)
                                  or max(0.8, abs(pred) * 0.35 + 0.4)),
                "next": _next_estimate(bar),
            })
        if not records:
            continue

        total = len(records)
        hits = sum(1 for r in records if r["hit"])
        ups = [r for r in records if r["side"] == "up"]
        downs = [r for r in records if r["side"] == "down"]
        nxt = [r["next"]["pct"] for r in records]
        return {
            "status": "ok",
            "market": market_key,
            "label": snap.get("label", market_key),
            "pred_date": snap["date"],
            "review_date": records[0]["date"],
            "total": total,
            "hits": hits,
            "hit_rate": round(hits / total * 100, 1) if total else 0,
            "avg_actual": round(sum(r["actual_pct"] for r in records) / total, 2),
            "avg_abs_error": round(sum(abs(r["error"]) for r in records) / total, 2),
            "up_avg": round(sum(r["actual_pct"] for r in ups) / len(ups), 2) if ups else None,
            "down_avg": round(sum(r["actual_pct"] for r in downs) / len(downs), 2) if downs else None,
            "next_avg": round(sum(nxt) / len(nxt), 2) if nxt else 0,
            "next_bias": sum(1 for x in nxt if x > 0.2),
            "next_bear": sum(1 for x in nxt if x < -0.2),
            "next_flat": sum(1 for x in nxt if -0.2 <= x <= 0.2),
            "records": records,
        }

    return {"status": "pending", "market": market_key,
            "message": "已有预测存档，等待下一交易日收盘后生成对比。"}
