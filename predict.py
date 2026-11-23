"""
隔日预测（多因子规则模型）
==========================
收盘后基于当日数据，给次日方向与幅度打分：

  预测分 = 0.40×形态 + 0.20×资金 + 0.15×消息 + 0.15×行为 + 0.10×情绪

  · 形态：收盘位置、超跌/涨幅透支、换手（原量价模型）
  · 资金：A 股主力净流入占比；美股无公开主力口径，取中性 50
  · 消息：新闻标题关键词情绪，并计算与股价反应的预期差
  · 行为：换手率/成交额分位、振幅、收盘位置
  · 情绪：指数当日涨跌幅映射

样本：A 股涨跌幅前 100；美股主要股票池。输出各前 10。
仅供研究，不构成投资建议。
"""
from __future__ import annotations

import time
from typing import Any

import factors
import rank
import session
import track

_cache: dict[str, tuple[float, Any]] = {}
_W = {"form": 0.40, "fund": 0.20, "news": 0.15, "behavior": 0.15, "sentiment": 0.10}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _metrics(item: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    close = float(item.get("price") or 0)
    high = float(item.get("high") or close)
    low = float(item.get("low") or close)
    prev = float(item.get("prev_close") or close)
    rng = high - low
    pos = (close - low) / rng if rng > 0 else 0.5
    pos = max(0.0, min(1.0, pos))
    pct = float(item.get("change_pct") or 0)
    turn = min(abs(float(item.get("turnover") or 0)), 20.0) / 20.0
    amp = ((high - low) / prev * 100) if prev > 0 else abs(pct) * 1.2
    amp = max(0.8, min(12.0, amp))

    up = 0.50 * pos + 0.30 * min(1.0, max(0.0, -pct / 5.0)) + 0.20 * turn
    down = 0.50 * (1 - pos) + 0.30 * min(1.0, max(0.0, pct / 5.0)) + 0.20 * turn
    if pct <= -9.5:
        up -= 0.15
    if pct >= 9.5:
        down -= 0.15
    return up, down, pos, pct, turn, amp


def _reason_up(pos: float, pct: float, turn: float) -> str:
    if pct < -2 and pos > 0.6:
        return "大幅回调后收盘拉回，超跌反弹"
    if pct < 0 and pos > 0.55:
        return "下影线偏长，尾盘承接明显"
    if pos > 0.8:
        return "收于日内高位，强势有望延续"
    if turn > 0.5 and pos > 0.55:
        return "换手活跃且收盘偏强"
    return "收盘位置中性偏强，短线占优"


def _reason_down(pos: float, pct: float, turn: float) -> str:
    if pct > 5 and pos < 0.35:
        return "冲高回落收于低位，获利盘回吐"
    if pct > 2 and pos < 0.4:
        return "上影线偏长，上方抛压明显"
    if pct < 0 and pos < 0.3:
        return "弱势收盘，次日仍有承压"
    if turn > 0.5 and pos < 0.45:
        return "高换手且收盘走弱"
    return "收盘位置偏低，短线偏弱"


def _behavior(item: dict[str, Any], market_key: str, amount_rank: float) -> float:
    _, _, pos, pct, _, amp = _metrics(item)
    if market_key == "cn":
        turnover = min(abs(float(item.get("turnover") or 0)) / 10.0, 1.0) * 100
    else:
        turnover = amount_rank * 100
    return round(0.35 * turnover + 0.35 * min(amp / 8.0, 1.0) * 100 + 0.30 * pos * 100, 1)


def _fund_score(market_key: str, item: dict[str, Any],
                flows: dict[str, dict[str, float]]) -> tuple[float, str]:
    if market_key == "cn":
        data = flows.get(str(item["code"]))
        if data is None:
            return 50.0, "资金数据缺失（中性）"
        ratio = float(data.get("ratio") or 0)
        net = float(data.get("net") or 0)
        score = _clamp(50 + ratio * 5, 5, 95)
        if abs(net) >= 1e8:
            note = f"主力净{'流入' if net > 0 else '流出'} {abs(net)/1e8:.2f} 亿（占比 {ratio:+.1f}%）"
        else:
            note = f"主力净{'流入' if net > 0 else '流出'} {abs(net)/1e4:.0f} 万（占比 {ratio:+.1f}%）"
        return round(score, 1), note
    return 50.0, "美股无公开主力资金口径（中性）"


def _pack(item: dict[str, Any], side: str, score: float, base: float,
          pos: float, pct: float, turn: float, amp: float,
          fund: float, news: dict[str, Any], sentiment: float,
          behavior: float, fund_note: str) -> dict[str, Any]:
    score = _clamp(score)
    strength = max(0.0, min(1.0, (score - 40) / 45))
    magnitude = max(0.8, min(7.0, 0.7 + 3.0 * strength + 0.15 * amp))
    pred = magnitude if side == "up" else -magnitude
    news_score = float(news.get("score") or 50)
    gap = factors.expectation_gap(news, pct)

    factor_list = [
        ("形态", round(base * 100, 1), 40),
        ("资金", fund, 20),
        ("消息", news_score, 15),
        ("行为", behavior, 15),
        ("情绪", sentiment, 10),
    ]
    factor_note = []
    if fund_note:
        factor_note.append(fund_note)
    if news.get("titles"):
        factor_note.append(gap["note"])
    return {
        "symbol": item["symbol"],
        "code": item["code"],
        "name": item["name"],
        "market": item["market"],
        "price": round(float(item.get("price") or 0), 2),
        "change_pct": round(pct, 2),
        "currency": item.get("currency", "CNY"),
        "score": round(score, 1),
        "pred_pct": round(pred, 2),
        "pred_range": round(magnitude * 0.35, 2),
        "reason": _reason_up(pos, pct, turn) if side == "up" else _reason_down(pos, pct, turn),
        "basis": {
            "factors": factor_list,
            "formula": "0.40×形态 + 0.20×资金 + 0.15×消息 + 0.15×行为 + 0.10×情绪",
            "amp": round(amp, 1),
            "news": {"score": news_score, "titles": news.get("titles") or [],
                     "gap": gap["gap"], "note": gap["note"]},
            "fund_note": fund_note,
            "factor_note": " · ".join(factor_note),
        },
    }


def get_prediction(market: str = "cn") -> dict[str, Any]:
    market = market if market in ("cn", "us") else "cn"
    state = session.state(market)
    if state["state"] == "open":
        last = track.latest_snapshot(market, before=state["session_date"])
        return {
            "status": "wait_close", "market": market,
            "label": "A 股" if market == "cn" else "美股",
            "session_date": state["session_date"],
            "message": f"盘中不生成预测：以收盘价为准，{state['close_label']} 收盘后更新。",
            "last": last,
        }
    key = f"predict:{market}"
    hit = _cache.get(key)
    if hit and hit[0] > time.time() and hit[1].get("session_date") == state["session_date"]:
        return hit[1]

    if market == "cn":
        items = rank._sina_items("hs_a", 0, 100) + rank._sina_items("hs_a", 1, 100)
        label = "A 股"
    else:
        items = rank._us_quote_batch(rank.US_UNIVERSE)
        label = "美股"

    uniq: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name", ""))
        if item.get("price", 0) < 2 or "ST" in name or "退" in name:
            continue
        amount = float(item.get("amount") or 0)
        if amount < (5e7 if market == "cn" else 1e7):
            continue
        uniq[item["symbol"]] = item

    base: dict[str, tuple[Any, ...]] = {}
    for item in uniq.values():
        base[item["symbol"]] = (item, *_metrics(item))

    tradable = [v for v in base.values() if -9.5 < v[4] < 9.5]
    pool_up = sorted(tradable, key=lambda x: x[1], reverse=True)[:18]
    pool_down = sorted(tradable, key=lambda x: x[2], reverse=True)[:18]
    candidates = {x[0]["symbol"]: x for x in pool_up + pool_down}
    cand_items = [x[0] for x in candidates.values()]

    # 资金 / 消息 / 情绪因子
    flows = factors.fund_flows(cand_items) if market == "cn" else {}
    sentiment = factors.sentiment(market)
    news_map = factors.news_batch(market, cand_items)

    amounts = sorted((float(x[0].get("amount") or 0) for x in candidates.values()))
    rank_of = {v: i / max(1, len(amounts) - 1) for i, v in enumerate(amounts)}

    scored = []
    for sym, data in candidates.items():
        item, up, down, pos, pct, turn, amp = data
        fund, fund_note = _fund_score(market, item, flows)
        news = news_map.get(sym, {"score": 50.0, "titles": [], "positive": 0, "negative": 0})
        news_score = float(news.get("score") or 50)
        amount_rank = rank_of.get(float(item.get("amount") or 0), 0.5)
        behavior = _behavior(item, market, amount_rank)
        sent = sentiment["score"]

        up_c = (0.40 * up * 100 + 0.20 * fund + 0.15 * news_score
                + 0.15 * behavior + 0.10 * sent)
        down_c = (0.40 * down * 100 + 0.20 * (100 - fund) + 0.15 * (100 - news_score)
                  + 0.15 * (100 - behavior) + 0.10 * (100 - sent))
        if pct <= -9.5:
            up_c -= 15
        if pct >= 9.5:
            down_c -= 15
        scored.append({
            "item": item, "up": up, "down": down, "pos": pos, "pct": pct,
            "turn": turn, "amp": amp, "up_c": up_c, "down_c": down_c,
            "fund": fund, "fund_note": fund_note, "news": news,
            "news_score": news_score, "behavior": behavior, "sent": sent,
        })

    up_pick = sorted(scored, key=lambda x: x["up_c"], reverse=True)[:10]
    up_syms = {x["item"]["symbol"] for x in up_pick}
    down_pick = [x for x in sorted(scored, key=lambda x: x["down_c"], reverse=True)
                 if x["item"]["symbol"] not in up_syms][:10]

    result = {
        "market": market,
        "session_date": state["session_date"],
        "label": label,
        "up": [_pack(x["item"], "up", x["up_c"], x["up"], x["pos"], x["pct"],
                     x["turn"], x["amp"], x["fund"], x["news"], x["sent"],
                     x["behavior"], x["fund_note"]) for x in up_pick],
        "down": [_pack(x["item"], "down", x["down_c"], x["down"], x["pos"], x["pct"],
                       x["turn"], x["amp"], x["fund"], x["news"], x["sent"],
                       x["behavior"], x["fund_note"]) for x in down_pick],
        "universe": len(uniq),
        "sentiment": sentiment,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": "形态+资金+消息+行为+情绪 · 规则模型",
    }
    try:
        track.save_prediction(market, result, date=state["session_date"])
    except Exception:  # noqa: BLE001
        pass
    _cache[key] = (time.time() + 300, result)
    return result
