"""
首页市场概览
============
8 个指数/商品 + A 股与美股涨平跌家数。

数据：
  · A 股指数、美股指数：腾讯快照
  · 黄金连续（沪金 99）、纽约黄金：新浪
  · A 股涨平跌：新浪全市场分页统计（约 5600 只）
  · 美股涨平跌：纳斯达克全市场清单（约 7000 只）
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import market
import rank

_CACHE: dict[str, tuple[float, Any]] = {}


def _sina(symbols: str) -> str:
    req = urllib.request.Request(
        "https://hq.sinajs.cn/list=" + symbols,
        headers={**market.UA, "Referer": "https://finance.sina.com.cn/"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return resp.read().decode("gbk", "ignore")


def _gold_items() -> list[dict[str, Any]]:
    out = []
    try:
        text = _sina("nf_AU0,hf_GC")
    except Exception:  # noqa: BLE001
        return out
    for line in text.split(";"):
        try:
            if "hq_str_nf_AU0" in line:
                f = line.split('"')[1].split(",")
                price = float(f[5])
                if price <= 0:  # 夜盘刚开盘时最新价可能为 0
                    for idx in (8, 6, 7):
                        try:
                            price = float(f[idx])
                            if price > 0:
                                break
                        except (IndexError, ValueError):
                            continue
                prev = float(f[10]) if float(f[10] or 0) > 0 else float(f[9] or price)
                pct = (price / prev - 1) * 100 if prev else 0
                out.append({
                    "key": "au9999", "name": "黄金连续",
                    "sub": "AU0", "value": round(price, 2),
                    "change_pct": round(pct, 2), "currency": "CNY",
                    "symbol": "au0",
                })
            elif "hq_str_hf_GC" in line:
                f = line.split('"')[1].split(",")
                price = float(f[0])
                prev = float(f[7])
                pct = (price / prev - 1) * 100 if prev else 0
                out.append({
                    "key": "gc", "name": "纽约黄金",
                    "sub": "COMEX", "value": round(price, 2),
                    "change_pct": round(pct, 2), "currency": "USD",
                    "symbol": "gc",
                })
        except (IndexError, ValueError):
            continue
    return out


CN_BREADTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "breadth_cn.json")
_CN_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}


def _cn_breadth() -> dict[str, Any] | None:
    """A 股全市场涨 / 平 / 跌家数：新浪分页统计（约 5400 只），缓存 5 分钟。"""
    now = time.time()
    if _CN_CACHE["data"] and now - _CN_CACHE["ts"] < 300:
        return _CN_CACHE["data"]

    if not _CN_CACHE["data"]:
        try:
            with open(CN_BREADTH_FILE, encoding="utf-8") as fh:
                saved = json.load(fh)
            if now - float(saved.get("ts", 0)) < 1800:
                _CN_CACHE.update({"ts": float(saved["ts"]), "data": saved["data"]})
                return saved["data"]
        except Exception:  # noqa: BLE001
            pass

    headers = {**market.UA, "Referer": "https://finance.sina.com.cn/"}
    try:
        count_url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                     "json_v2.php/Market_Center.getHQNodeStockCount?node=hs_a")
        req = urllib.request.Request(count_url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            total = int(resp.read().decode("utf-8", "ignore").strip().strip('"'))
        pages = min(70, (total + 99) // 100)

        def fetch(page: int) -> list[dict[str, Any]]:
            url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   f"Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent"
                   "&asc=0&node=hs_a")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("gbk", "ignore")) or []

        with ThreadPoolExecutor(max_workers=6) as pool:
            chunks = list(pool.map(fetch, range(1, pages + 1)))

        up = down = flat = 0
        for rows in chunks:
            for row in rows:
                try:
                    pct = float(row.get("changepercent") or 0)
                except (TypeError, ValueError):
                    continue
                if pct > 0:
                    up += 1
                elif pct < 0:
                    down += 1
                else:
                    flat += 1
        valid = up + down + flat
        if valid < 1000:
            raise ValueError("sina breadth incomplete")

        result = {"up": up, "down": down, "flat": flat,
                  "total": valid, "sample": False}
        _CN_CACHE.update({"ts": time.time(), "data": result})
        try:
            os.makedirs(os.path.dirname(CN_BREADTH_FILE), exist_ok=True)
            with open(CN_BREADTH_FILE, "w", encoding="utf-8") as fh:
                json.dump({"ts": time.time(), "data": result}, fh)
        except Exception:  # noqa: BLE001
            pass
        return result
    except Exception:  # noqa: BLE001
        return _CN_CACHE["data"]


_US_BREADTH: dict[str, tuple[float, Any]] = {}


def _us_breadth() -> dict[str, Any] | None:
    """美股全市场涨跌家数（Nasdaq 全市场清单，约 7000 只）。"""
    cached = _US_BREADTH.get("v")
    if cached and cached[0] > time.time():
        return cached[1]
    url = ("https://api.nasdaq.com/api/screener/stocks"
           "?tableonly=true&limit=10000&offset=0")
    headers = {
        **market.UA,
        "Accept": "application/json",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
        rows = (payload.get("data") or {}).get("table", {}).get("rows") or []
        up = down = flat = valid = 0
        for row in rows:
            raw = str(row.get("pctchange", "")).replace("%", "").replace(",", "").strip()
            try:
                pct = float(raw)
            except ValueError:
                continue
            valid += 1
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
        if valid > 1000:
            result = {"up": up, "down": down, "flat": flat,
                      "total": valid, "sample": False}
            _US_BREADTH["v"] = (time.time() + 300, result)
            return result
    except Exception:  # noqa: BLE001
        pass

    # 兜底：主要美股池样本
    try:
        quotes = rank._us_quote_batch(rank.US_UNIVERSE)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    if not quotes:
        return None
    up = sum(1 for q in quotes if q["change_pct"] > 0)
    down = sum(1 for q in quotes if q["change_pct"] < 0)
    flat = len(quotes) - up - down
    return {"up": up, "down": down, "flat": flat,
            "total": len(quotes), "sample": True}


def get_overview() -> dict[str, Any]:
    cached = _CACHE.get("overview")
    if cached and cached[0] > time.time():
        return cached[1]

    indices: list[dict[str, Any]] = []
    for symbol in ("sh000001", "sz399001", "sz399006",
                   "us.INX", "us.IXIC", "us.DJI"):
        try:
            q = market.fetch_quote(symbol)
            indices.append({
                "key": symbol,
                "name": q.get("name") or symbol,
                "sub": "指数",
                "value": round(float(q.get("price") or 0), 2),
                "change_pct": round(float(q.get("change_pct") or 0), 2),
                "currency": q.get("currency") or ("USD" if symbol.startswith("us") else "CNY"),
                "symbol": symbol,
            })
        except market.MarketError:
            continue
    indices.extend(_gold_items())

    data = {
        "indices": indices,
        "breadth": {"cn": _cn_breadth(), "us": _us_breadth()},
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _CACHE["overview"] = (time.time() + 30, data)
    return data
