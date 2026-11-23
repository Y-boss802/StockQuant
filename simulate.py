"""
资金模拟
========
用户输入金额、持有天数、风险偏好与市场，给出：
  · 资金档位与对应股民心态建议
  · 该市场的交易规则
  · 按评分 + 波动 + 可买性选出的标的与买入股数
  · 用历史日收益自助抽样模拟持有期末资金分布

A 股：T+1、100 股/手、涨跌停、印花税
美股：T+0、1 股起、无涨跌停、佣金多为 0
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

import market
import scoreboard

_LOT = {"cn": 100, "us": 1}
_CURRENCY = {"cn": "CNY", "us": "USD"}
_SYMBOL = {"cn": "¥", "us": "$"}


def _tier(market_key: str, amount: float) -> dict[str, Any]:
    if market_key == "cn":
        if amount < 10_000:
            return {"label": "小资金", "count": (1, 2), "cap": 0.5, "reserve": 0.12,
                    "mood": "本金少时最容易想一把梭、追热点。建议先练手：只买 1–2 只，"
                            "单票不超过 50%，留 10% 以上现金，不要加杠杆。"}
        if amount < 100_000:
            return {"label": "中等资金", "count": (3, 4), "cap": 0.30, "reserve": 0.08,
                    "mood": "有一定承受力，容易频繁换股。建议 3–4 只分散，单票 ≤30%，"
                            "按周复盘而不是每天盯盘。"}
        return {"label": "较大资金", "count": (5, 6), "cap": 0.22, "reserve": 0.06,
                "mood": "更应关注回撤与流动性，而不是弹性。建议 5–6 只、单票 ≤22%，"
                        "优先低波动高分标的，分批建仓。"}
    if amount < 2_000:
        return {"label": "小资金", "count": (1, 2), "cap": 0.5, "reserve": 0.12,
                "mood": "小账户容易重仓单票、追逐热门股。建议 1–2 只、留 10% 现金，"
                        "先熟悉 T+0 与盘前盘后波动。"}
    if amount < 20_000:
        return {"label": "中等资金", "count": (3, 4), "cap": 0.32, "reserve": 0.08,
                "mood": "可承受一定波动，但别把分散做成“买一堆熟悉的名字”。"
                        "建议 3–4 只、单票 ≤32%，关注汇率与盘前跳空。"}
    return {"label": "较大资金", "count": (5, 6), "cap": 0.22, "reserve": 0.06,
            "mood": "大资金更看重回撤、流动性与税费。建议 5–6 只、单票 ≤22%，"
                    "优先低波动蓝筹，用限价单分批成交。"}


def _rules(market_key: str) -> list[str]:
    if market_key == "cn":
        return [
            "T+1：当日买入，下一交易日才能卖出",
            "100 股/手，必须按整手买入",
            "涨跌停：主板 ±10%，ST ±5%，创业板/科创板 ±20%",
            "卖出印花税 0.05%，佣金约 0.025%（最低 5 元），过户费 0.001%",
        ]
    return [
        "T+0：同一交易日可反复买卖",
        "1 股起买，部分券商支持碎股",
        "无涨跌停，个股有波动熔断",
        "主流券商佣金 0，卖出有 SEC/TAF 等监管费",
    ]


def _weights(scores: list[float], cap: float) -> list[float]:
    raw = [max(1.0, s) for s in scores]
    total = sum(raw)
    w = [x / total for x in raw]
    for _ in range(30):
        excess = sum(max(0.0, x - cap) for x in w)
        if excess < 1e-6:
            break
        w = [min(x, cap) for x in w]
        free = [i for i, x in enumerate(w) if x < cap - 1e-9]
        if not free:
            break
        add = excess / len(free)
        for i in free:
            w[i] += add
    total = sum(w)
    return [x / total for x in w]


def _returns(symbol: str) -> pd.Series:
    ticker = symbol[2:] if symbol.startswith("us") else symbol
    recs = market._fetch_xueqiu_daily(ticker.upper(), count=260)  # noqa: SLF001
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df["close"].pct_change()


def _fetch_board(market_key: str) -> dict[str, Any]:
    return scoreboard.get_scoreboard(market_key)


def simulate(market_key: str = "cn", amount: float = 100_000,
             days: int = 30, risk: str = "balanced") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    risk = risk if risk in ("conservative", "balanced", "aggressive") else "balanced"
    amount = float(max(100.0, min(amount, 1e9)))
    days = int(max(1, min(days, 250)))

    board = _fetch_board(market_key)
    if board.get("status") != "ok":
        return {"status": "computing", "market": market_key,
                "progress": board.get("progress", 0), "total": board.get("total", 0)}

    items = board.get("items") or []
    if not items:
        return {"status": "error", "error": "评分板暂无数据，请稍后重试"}

    tier = _tier(market_key, amount)
    count = list(tier["count"])
    cap = tier["cap"]
    reserve = tier["reserve"]
    if risk == "conservative":
        count = [max(1, count[0] - 1), max(1, count[1] - 1)]
        cap *= 0.8
        reserve = min(0.25, reserve + 0.05)
    elif risk == "aggressive":
        count = [count[0] + 1, count[1] + 1]
        cap = min(0.6, cap * 1.15)
        reserve = max(0.02, reserve - 0.04)
    count[0] = max(1, min(count[0], 8))
    count[1] = max(count[0], min(count[1], 8))

    lot = _LOT[market_key]
    invest_budget = amount * (1 - reserve)

    def adjustable(item: dict[str, Any]) -> float:
        vol = float(item.get("vol") or 30)
        score = float(item.get("score") or 0)
        if risk == "conservative":
            return score - 0.30 * vol
        if risk == "aggressive":
            return score + 0.12 * vol
        return score

    affordable = [x for x in items if float(x["price"]) * lot <= invest_budget * cap]
    if not affordable:
        return {"status": "error", "error": f"金额 {amount:.0f} 不足：按该档位"
                f"最多单票 {cap*100:.0f}% 仓位，买不起最小一手，请增加金额或提高风险偏好。"}
    affordable.sort(key=adjustable, reverse=True)
    picks = affordable[:count[1]]
    if len(picks) < count[0]:
        picks = affordable[:count[0]]

    weights = _weights([float(x["score"]) for x in picks], cap)

    rows = []
    invested = 0.0
    for item, weight in zip(picks, weights):
        price = float(item["price"])
        budget = invest_budget * weight
        shares = int(budget // (price * lot)) * lot
        if shares <= 0:
            continue
        cost = shares * price
        invested += cost
        rows.append({
            "symbol": item["symbol"], "code": item["code"], "name": item["name"],
            "price": round(price, 2), "score": item["score"], "vol": item.get("vol"),
            "weight": round(cost / amount, 4), "shares": shares, "cost": round(cost, 2),
        })

    if not rows:
        return {"status": "error", "error": "资金不足以按当前档位买入任何一手，请增加金额。"}

    # 整手取整后会剩现金，按单票上限继续加仓，减少空置
    cash = amount - invested
    for _ in range(4):
        moved = False
        for row in rows:
            price = row["price"]
            room = amount * cap - row["cost"]
            if room < price * lot:
                continue
            extra = int(min(room, cash) // (price * lot)) * lot
            if extra > 0:
                row["shares"] += extra
                row["cost"] = round(row["cost"] + extra * price, 2)
                invested += extra * price
                cash -= extra * price
                moved = True
        if not moved:
            break
    for row in rows:
        row["weight"] = round(row["cost"] / amount, 4)
    cash = amount - invested
    fees = invested * ((0.00025 + 0.00001) * 2 + 0.0005) if market_key == "cn" else invested * 0.00003

    # 用历史日收益自助抽样模拟组合路径
    rets = {}
    for row in rows:
        try:
            rets[row["symbol"]] = _returns(row["symbol"])
        except Exception:  # noqa: BLE001
            continue
    sim_stats = None
    if len(rets) >= 1:
        ret_df = pd.DataFrame(rets).dropna()
        if len(ret_df) >= 80:
            weights_arr = np.array([row["cost"] / invested for row in rows if row["symbol"] in ret_df.columns])
            cols = [row["symbol"] for row in rows if row["symbol"] in ret_df.columns]
            port = (ret_df[cols] * (weights_arr / weights_arr.sum())).sum(axis=1)
            rng = np.random.default_rng(42)
            samples = rng.choice(port.to_numpy(), size=(3000, days), replace=True)
            paths = np.cumprod(1 + samples, axis=1)
            finals = cash + invested * paths[:, -1]
            peaks = np.maximum.accumulate(paths, axis=1)
            dds = 1 - paths / peaks
            sim_stats = {
                "p05": round(float(np.percentile(finals, 5)), 2),
                "p25": round(float(np.percentile(finals, 25)), 2),
                "p50": round(float(np.percentile(finals, 50)), 2),
                "p75": round(float(np.percentile(finals, 75)), 2),
                "p95": round(float(np.percentile(finals, 95)), 2),
                "prob_profit": round(float((finals > amount).mean() * 100), 1),
                "median_mdd": round(float(np.median(dds[:, -1]) * 100), 2),
                "worst_p95": round(float(np.percentile(finals, 5) - amount), 2),
            }

    return {
        "status": "ok",
        "market": market_key,
        "currency": _CURRENCY[market_key],
        "symbol": _SYMBOL[market_key],
        "amount": round(amount, 2),
        "days": days,
        "risk": risk,
        "tier": {"label": tier["label"], "mood": tier["mood"],
                 "count": len(rows), "cap": round(cap, 2), "reserve": round(reserve, 2)},
        "rules": _rules(market_key),
        "picks": rows,
        "invested": round(invested, 2),
        "cash": round(cash, 2),
        "fees": round(fees, 2),
        "sim": sim_stats,
        "updated": board.get("updated"),
    }
