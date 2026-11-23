"""
监控与告警
==========
基于模拟盘持仓与数据质量生成告警：

  · 单票仓位 > 25%
  · 持仓浮亏 ≤ −8%
  · 总仓位 > 90%
  · 现金占比 < 5%
  · 数据质量分 < 70

接口 /api/monitor?market=cn，结果不缓存（读当前模拟盘与质量缓存）。
"""
from __future__ import annotations

import time
from typing import Any

import paper
import quality


def run_monitor(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    state = paper.get_state(market_key)
    equity = float(state.get("equity") or 0)
    base = equity if equity > 0 else 1.0
    alerts: list[dict[str, Any]] = []

    for p in state.get("positions", []):
        weight = p["value"] / base
        if weight > 0.25:
            alerts.append({
                "level": "medium",
                "title": f"{p['name']} 仓位集中",
                "detail": f"占净值 {weight * 100:.1f}%，超过 25% 上限",
            })
        if p["pnl_pct"] <= -8:
            alerts.append({
                "level": "high",
                "title": f"{p['name']} 浮亏 {p['pnl_pct']:.1f}%",
                "detail": f"现价 {p['price']}，成本 {p['cost']}，触发 −8% 止损提醒",
            })

    invested = float(state.get("market_value") or 0) / base
    if invested > 0.9:
        alerts.append({"level": "medium", "title": "总仓位过高",
                       "detail": f"持仓占净值 {invested * 100:.1f}%，建议留现金"})
    cash_ratio = float(state.get("cash") or 0) / base
    if cash_ratio < 0.05:
        alerts.append({"level": "medium", "title": "现金不足",
                       "detail": f"现金仅占 {cash_ratio * 100:.1f}%，应对波动能力弱"})

    q = quality.run_quality(market_key, 20)
    quality_avg = None
    if q.get("status") == "ok":
        quality_avg = q["avg_score"]
        for row in q["rows"]:
            if row["score"] < 70:
                alerts.append({
                    "level": "high",
                    "title": f"{row['name']} 数据质量 {row['score']} 分",
                    "detail": "；".join(row.get("issues") or [])[:80],
                })

    if not alerts:
        alerts.append({"level": "info", "title": "暂无异常",
                       "detail": "持仓、仓位与数据质量均在阈值内"})

    return {
        "status": "ok", "market": market_key, "alerts": alerts,
        "stats": {
            "equity": round(equity, 2),
            "cash": round(float(state.get("cash") or 0), 2),
            "invested": round(invested * 100, 1),
            "positions": len(state.get("positions", [])),
            "quality_avg": quality_avg,
        },
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
