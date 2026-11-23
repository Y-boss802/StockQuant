"""
预测因子
========
资金、情绪、消息/预期差、交易行为。

  · 资金流：东方财富批量主力净流入占比（A 股）；美股无公开主力口径，取中性 50
  · 情绪：指数当日涨跌幅映射到 0–100
  · 消息：东方财富 / 纳斯达克新闻标题关键词打分，并计算与股价反应的预期差
  · 行为：换手率、振幅、收盘位置（美股用成交额分位替代换手）
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import market

_EM_HEADERS = {**market.UA, "Referer": "https://quote.eastmoney.com/"}
_ND_HEADERS = {**market.UA, "Accept": "application/json",
               "Referer": "https://www.nasdaq.com/"}
_CACHE: dict[str, tuple[float, Any]] = {}

_POS = ("增长", "上涨", "涨停", "利好", "增持", "回购", "中标", "突破", "超预期",
        "盈利", "新高", "买入", "推荐", "上调", "beat", "raise", "upgrade",
        "surge", "rally", "record", "buyback", "growth", "profit", "wins")
_NEG = ("下跌", "跌停", "利空", "减持", "亏损", "处罚", "立案", "下调", "卖出",
        "风险", "退市", "诉讼", "违约", "miss", "downgrade", "lawsuit", "probe",
        "cut", "loss", "slump", "plunge", "warning", "recall", "delay", "resign", "fraud")


def _get(url: str, headers: dict[str, str], encoding: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode(encoding, "ignore")


def sentiment(market_key: str) -> dict[str, Any]:
    """市场情绪：指数涨跌幅映射。"""
    key = f"sentiment:{market_key}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    symbol = "sh000001" if market_key == "cn" else "us.INX"
    try:
        quote = market.fetch_quote(symbol)
        pct = float(quote.get("change_pct") or 0)
    except market.MarketError:
        pct = 0.0
    score = max(5.0, min(95.0, 50 + pct * 12))
    data = {"score": round(score, 1), "index_pct": round(pct, 2)}
    _CACHE[key] = (time.time() + 120, data)
    return data


def fund_flows(items: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """A 股批量主力资金流：f184 净流入占比、f62 净流入额。"""
    codes = [x["code"] for x in items if re.fullmatch(r"\d{6}", str(x["code"]))]
    if not codes:
        return {}
    secids = ",".join(("1." if c.startswith("6") else "0.") + c for c in codes)
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
           f"&secids={secids}&fields=f12,f184,f62")
    try:
        payload = json.loads(_get(url, _EM_HEADERS))
        rows = (payload.get("data") or {}).get("diff") or []
        return {
            str(r.get("f12")): {"ratio": float(r.get("f184") or 0),
                                "net": float(r.get("f62") or 0)}
            for r in rows
        }
    except Exception:  # noqa: BLE001
        return {}


def _em_titles(name: str) -> list[str]:
    param = {
        "uid": "", "keyword": name, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                       "pageIndex": 1, "pageSize": 8}},
    }
    query = urllib.parse.quote(json.dumps(param, ensure_ascii=False))
    text = _get(f"https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param={query}",
                _EM_HEADERS)
    start, end = text.find("("), text.rfind(")")
    payload = json.loads(text[start + 1:end])
    arts = ((payload.get("result") or {}).get("cmsArticleWebOld") or [])
    return [re.sub(r"<[^>]+>", "", str(a.get("title") or "")) for a in arts][:6]


def _nasdaq_titles(code: str) -> list[str]:
    url = ("https://api.nasdaq.com/api/news/topic/articlebysymbol"
           f"?symbol={code}&limit=8")
    payload = json.loads(_get(url, _ND_HEADERS))
    rows = (payload.get("data") or {}).get("rows") or []
    return [str(r.get("title") or "") for r in rows][:6]


def news_score(market_key: str, item: dict[str, Any]) -> dict[str, Any]:
    key = f"news:{market_key}:{item['code']}"
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    titles: list[str] = []
    try:
        if market_key == "cn":
            titles = _em_titles(item["name"])
        else:
            titles = _nasdaq_titles(item["code"])
    except Exception:  # noqa: BLE001
        titles = []
    # 只保留与个股相关的标题，避免用大盘新闻给个股打分
    tokens = {str(item.get("code", "")).lower()}
    tokens.update(w.lower() for w in re.split(r"[^A-Za-z]+", str(item.get("name", "")))
                  if len(w) > 2)
    relevant = [t for t in titles
                if any(tok and tok in t.lower() for tok in tokens)]
    if not relevant:
        data = {"score": 50.0, "titles": [], "positive": 0, "negative": 0}
        _CACHE[key] = (time.time() + 600, data)
        return data
    text = " ".join(relevant).lower()
    pos = sum(text.count(k.lower()) for k in _POS)
    neg = sum(text.count(k.lower()) for k in _NEG)
    score = max(5.0, min(95.0, 50 + 8 * (pos - neg)))
    data = {"score": round(score, 1), "titles": relevant[:3],
            "positive": pos, "negative": neg}
    _CACHE[key] = (time.time() + 600, data)
    return data


def news_batch(market_key: str,
               items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(news_score, market_key, it): it["symbol"] for it in items}
        for fut in as_completed(futures):
            try:
                out[futures[fut]] = fut.result()
            except Exception:  # noqa: BLE001
                continue
    return out


def expectation_gap(news: dict[str, Any], pct: float) -> dict[str, Any]:
    """消息与股价反应的预期差：消息偏多但股价没涨 = 正预期差。"""
    if not news.get("titles"):
        return {"gap": 0.0, "note": "暂无个股消息，消息因子中性"}
    price_score = max(0.0, min(100.0, 50 + pct * 6))
    gap = (news.get("score") or 50) - price_score
    score = float(news.get("score") or 50)
    if gap > 10:
        if score >= 55:
            note = f"消息偏多，股价反应不足（预期差 +{gap:.0f}）"
        elif score <= 45:
            note = f"消息偏空，但股价跌幅更大（预期差 +{gap:.0f}，偏超跌）"
        else:
            note = f"消息中性，股价偏弱（预期差 +{gap:.0f}）"
    elif gap < -10:
        if score <= 45:
            note = f"消息偏空，但股价偏强（预期差 {gap:.0f}）"
        elif score >= 55:
            note = f"消息偏多，股价已提前反应（预期差 {gap:.0f}）"
        else:
            note = f"消息中性，股价偏强（预期差 {gap:.0f}）"
    else:
        note = "消息与股价基本匹配"
    return {"gap": round(gap, 1), "note": note}
