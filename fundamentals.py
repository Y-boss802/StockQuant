"""
基本面与事件数据
================
股票池的行业、估值、盈利与事件情绪：

  · 行业 / 概念：东方财富 f100 / f103
  · 估值：PE(f9)、PB(f23)、总市值(f20)
  · 盈利：ROE(f37)、营收同比(f41)、净利同比(f46)
  · 事件：新闻标题情绪 + 与股价反应的预期差（复用 factors）

结果缓存 30 分钟。免费数据存在缺失，缺失字段显示为空。
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

import factors
import scoreboard

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_CACHE: dict[str, tuple[float, Any]] = {}
_FIELDS = "f12,f14,f9,f23,f20,f21,f37,f41,f46,f100,f103"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", "ignore")


def _secid(market_key: str, code: str) -> str:
    if market_key == "cn":
        return ("1." if code.startswith("6") else "0.") + code
    return "105." + code


def _fetch(secids: list[str]) -> dict[str, dict[str, Any]]:
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
           f"&secids={','.join(secids)}&fields={_FIELDS}")
    try:
        payload = json.loads(_get(url))
        rows = (payload.get("data") or {}).get("diff") or []
        return {str(r.get("f12")): r for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def industry_map(market_key: str, items: list[dict[str, Any]]) -> dict[str, str]:
    """只取行业，不抓新闻。"""
    secids = [_secid(market_key, it["code"]) for it in items]
    data = _fetch(secids)
    if market_key == "us":
        missing = [it for it in items if it["code"] not in data]
        for prefix in ("106", "107"):
            if not missing:
                break
            extra = _fetch([f"{prefix}.{it['code']}" for it in missing])
            data.update(extra)
            missing = [it for it in missing if it["code"] not in extra]
    return {it["code"]: (data.get(it["code"], {}).get("f100") or "其他") for it in items}


def get_fundamentals(market_key: str = "cn", size: int = 20) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    size = max(5, min(int(size), 40))
    key = f"fundamentals:{market_key}:{size}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    board = scoreboard.get_scoreboard(market_key)
    if board.get("status") != "ok":
        return {"status": "computing", "market": market_key,
                "progress": board.get("progress", 0), "total": board.get("total", 0)}
    items = (board.get("items") or [])[:size]

    secids = [_secid(market_key, it["code"]) for it in items]
    data = _fetch(secids)

    # 美股市场号可能是 105/106/107，缺失的再补查
    if market_key == "us":
        missing = [it for it in items if it["code"] not in data]
        for prefix in ("106", "107"):
            if not missing:
                break
            extra = _fetch([f"{prefix}.{it['code']}" for it in missing])
            data.update(extra)
            missing = [it for it in missing if it["code"] not in extra]

    rows = []
    for it in items:
        raw = data.get(it["code"], {})
        pe = raw.get("f9")
        pe = float(pe) if isinstance(pe, (int, float)) else None
        rows.append({
            "symbol": it["symbol"],
            "code": it["code"],
            "name": it["name"],
            "score": it.get("score"),
            "industry": raw.get("f100") or "—",
            "concepts": (raw.get("f103") or "")[:60],
            "market_cap": round(float(raw.get("f20") or 0) / 1e8, 1) if raw.get("f20") else None,
            "pe": round(pe, 1) if pe is not None else None,
            "pb": round(float(raw.get("f23")), 2) if isinstance(raw.get("f23"), (int, float)) else None,
            "roe": round(float(raw.get("f37")), 2) if isinstance(raw.get("f37"), (int, float)) else None,
            "rev_yoy": round(float(raw.get("f41")), 2) if market_key == "cn" and isinstance(raw.get("f41"), (int, float)) else None,
            "profit_yoy": round(float(raw.get("f46")), 2) if market_key == "cn" and isinstance(raw.get("f46"), (int, float)) else None,
        })

    # 事件情绪与预期差：只对前 10 只抓新闻
    news_items = items[:10]
    news_map = factors.news_batch(market_key, news_items)
    for row in rows:
        news = news_map.get(row["symbol"])
        if news:
            pct = next(((x.get("change_pct") or 0) for x in items
                        if x["symbol"] == row["symbol"]), 0)
            gap = factors.expectation_gap(news, float(pct))
            row["news_score"] = news.get("score")
            row["news_title"] = (news.get("titles") or [""])[0]
            row["gap"] = gap["gap"]
            row["gap_note"] = gap["note"]

    industries: dict[str, int] = {}
    for row in rows:
        industries[row["industry"]] = industries.get(row["industry"], 0) + 1

    result = {
        "status": "ok",
        "market": market_key,
        "rows": rows,
        "industries": [{"name": k, "count": v} for k, v in
                       sorted(industries.items(), key=lambda kv: kv[1], reverse=True)],
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE[key] = (time.time() + 1800, result)
    return result
