"""
涨跌幅榜单
==========
· A 股：新浪全市场排名（hs_a），涨幅 / 跌幅各取前 N
· 美股：腾讯批量快照（主要美股池），按当日涨跌幅排序
· 黄金：新浪“黄金概念”板块 + A 股黄金 ETF

返回统一结构，供前端直接渲染；结果缓存 60 秒。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Any

import market

SINA_RANK = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData?page=1&num={num}&sort=changepercent"
             "&asc={asc}&node={node}")

# 主要美股池（市值大、成交活跃，覆盖科技/金融/消费/能源/ETF）
US_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL", "CRM",
    "ADBE", "NFLX", "AMD", "INTC", "CSCO", "QCOM", "TXN", "MU", "AMAT", "ASML",
    "TSM", "IBM", "NOW", "INTU", "PANW", "CRWD", "SNOW", "PLTR", "UBER", "ABNB",
    "COIN", "SHOP", "SQ", "PYPL", "SPOT", "SNAP", "PINS", "ROKU", "DDOG", "NET",
    "MDB", "ZS", "OKTA", "TWLO", "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV",
    "LI", "TME", "BILI", "JPM", "BAC", "WFC", "C", "GS", "MS",
    "SCHW", "BLK", "AXP", "COF", "USB", "PNC", "MET", "PRU", "ALL", "TRV",
    "BRK-B", "V", "MA", "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "ABT",
    "DHR", "TMO", "MRNA", "XOM", "CVX", "COP", "SLB", "OXY", "PSX", "KO",
    "PEP", "PG", "COST", "WMT", "HD", "LOW", "MCD", "SBUX", "NKE", "DIS",
    "CMCSA", "VZ", "T", "GE", "CAT", "BA", "F", "GM", "RIVN", "LCID",
    "SOFI", "HOOD", "GME", "AMC", "SPY", "QQQ", "DIA", "IWM", "TLT", "HYG",
    "GLD", "SLV", "USO", "UNG", "GDX", "GDXJ", "XLE", "XLF", "XLK", "XLV",
    "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE",
]

GOLD_NODE = "gn_hjgn"
GOLD_ETFS = ["sh518880", "sz159934", "sz159937", "sh518800", "sz159562"]

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float = 60):
    item = _cache.get(key)
    if item and item[0] > time.time():
        return item[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    _cache[key] = (time.time() + 60, value)


def _get(url: str, encoding: str = "gbk", retries: int = 2) -> str:
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=market.UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(0.4)
    raise market.MarketError(f"榜单接口请求失败：{last_err}") from last_err


def _sym_from_sina(symbol: str) -> tuple[str, str]:
    """sz300530 -> (sz300530, 深市)。"""
    sym = str(symbol).lower()
    if sym.startswith(("sh", "sz", "bj")):
        return sym, sym[:2]
    code = re.sub(r"\D", "", sym)
    return market.normalize_symbol(code) or f"sh{code}", ""


def _sina_items(node: str, asc: int, num: int) -> list[dict[str, Any]]:
    url = SINA_RANK.format(num=num, asc=asc, node=node)
    raw = _get(url)
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise market.MarketError("榜单接口返回格式异常") from exc
    out = []
    for row in rows or []:
        try:
            symbol, mkt = _sym_from_sina(row["symbol"])
            code = str(row["code"])
            out.append({
                "symbol": symbol,
                "code": code,
                "name": row["name"],
                "market": mkt,
                "price": float(row["trade"]),
                "change": float(row.get("pricechange") or 0),
                "change_pct": float(row.get("changepercent") or 0),
                "open": float(row.get("open") or 0),
                "high": float(row.get("high") or 0),
                "low": float(row.get("low") or 0),
                "prev_close": float(row.get("settlement") or 0),
                "amount": float(row.get("amount") or 0),
                "turnover": float(row.get("turnoverratio") or 0),
                "currency": "CNY",
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _us_quote_batch(symbols: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for start in range(0, len(symbols), 60):
        chunk = symbols[start:start + 60]
        query = ",".join(f"us{s}" for s in chunk)
        try:
            text = _get(f"https://qt.gtimg.cn/q={query}", encoding="gbk", retries=2)
        except market.MarketError:
            continue
        for line in text.split(";"):
            if '"' not in line:
                continue
            body = line.split('"')[1]
            f = body.split("~")
            if len(f) < 40:
                continue
            try:
                ticker = f[2].split(".")[0].upper()
                code = ticker if re.fullmatch(r"[A-Z\-]{1,6}", ticker) else f[2]
                price = float(f[3])
                pct = float(f[32])
                out.append({
                    "symbol": f"us{code}",
                    "code": code,
                    "name": f[1],
                    "market": "us",
                    "price": price,
                    "change": float(f[31] or 0),
                    "change_pct": pct,
                    "open": float(f[5] or 0),
                    "high": float(f[33] or 0),
                    "low": float(f[34] or 0),
                    "prev_close": float(f[4] or 0),
                    "amount": float(f[37] or 0),
                    "turnover": float(f[38] or 0),
                    "currency": "USD",
                })
            except (IndexError, ValueError):
                continue
    return out


def _sort(items: list[dict[str, Any]], asc: bool, limit: int) -> list[dict[str, Any]]:
    items = [x for x in items
             if x["price"] > 0 and x["change_pct"] is not None
             and "退" not in str(x.get("name", ""))]
    items.sort(key=lambda x: x["change_pct"], reverse=not asc)
    return items[:limit]


def _cn() -> dict[str, Any]:
    up = _sina_items("hs_a", 0, 50)
    down = _sina_items("hs_a", 1, 50)
    return {
        "market": "cn",
        "label": "A 股",
        "currency": "CNY",
        "universe": 5564,
        "up": up,
        "down": down,
        "limit": 50,
    }


def _us() -> dict[str, Any]:
    quotes = _us_quote_batch(US_UNIVERSE)
    return {
        "market": "us",
        "label": "美股",
        "currency": "USD",
        "universe": len(quotes),
        "up": _sort(quotes, False, 50),
        "down": _sort(quotes, True, 50),
        "limit": 50,
    }


def _gold() -> dict[str, Any]:
    items = _sina_items(GOLD_NODE, 0, 50)
    # 追加 A 股黄金 ETF
    try:
        text = _get("https://qt.gtimg.cn/q=" + ",".join(GOLD_ETFS), encoding="gbk", retries=1)
        for line in text.split(";"):
            if '"' not in line:
                continue
            f = line.split('"')[1].split("~")
            if len(f) < 40:
                continue
            try:
                code = f[2]
                if "黄金" not in f[1]:
                    continue
                prefix = line.split("=")[0].replace("v_", "").strip()
                sym = market.normalize_symbol(prefix) or market.normalize_symbol(code)
                price = float(f[3])
                items.append({
                    "symbol": sym or f"sh{code}",
                    "code": code,
                    "name": f[1],
                    "market": "sh",
                    "price": price,
                    "change": float(f[31] or 0),
                    "change_pct": float(f[32] or 0),
                    "amount": float(f[37] or 0) * 1e4,
                    "turnover": float(f[38] or 0),
                    "currency": "CNY",
                })
            except (IndexError, ValueError):
                continue
    except market.MarketError:
        pass

    items = [x for x in items if "ST" not in str(x.get("name", ""))]
    seen: set[str] = set()
    uniq = []
    for x in items:
        if x["symbol"] in seen:
            continue
        seen.add(x["symbol"])
        uniq.append(x)
    ranked_up = [x for x in _sort(uniq, False, 99) if x["change_pct"] > 0]
    ranked_down = [x for x in _sort(uniq, True, 99) if x["change_pct"] < 0]
    return {
        "market": "gold",
        "label": "黄金",
        "currency": "CNY",
        "universe": len(uniq),
        "up": ranked_up[:10],
        "down": ranked_down[:10],
        "limit": 10,
    }


def get_rank(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us", "gold") else "cn"
    cached = _cache_get(f"rank:{market_key}")
    if cached is not None:
        return cached
    if market_key == "cn":
        data = _cn()
    elif market_key == "us":
        data = _us()
    else:
        data = _gold()
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _cache_put(f"rank:{market_key}", data)
    return data
