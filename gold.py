"""
黄金数据
========
黄金连续（沪金 AU0）与纽约黄金（COMEX GC）的日线与实时报价。

日线：新浪期货历史接口（AU0 自 2008 年，GC 自 2016 年）
报价：AU0 用上海金 Au99.99 现货，GC 用 COMEX 期货快照
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
_CACHE: dict[str, tuple[float, Any]] = {}

_URLS = {
    "au0": ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_AU0=/"
            "InnerFuturesNewService.getDailyKLine?symbol=AU0"),
    "gc": ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_GC=/"
           "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=GC"),
}


class GoldError(Exception):
    pass


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("gbk", "ignore")


def fetch_daily(kind: str = "au0") -> list[dict[str, Any]]:
    kind = kind if kind in _URLS else "au0"
    cached = _CACHE.get(f"daily:{kind}")
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        text = _get(_URLS[kind])
        start = text.find("([")
        end = text.rfind("])")
        if start < 0 or end < 0:
            raise GoldError("黄金历史接口返回异常")
        rows = json.loads(text[start + 1:end + 1])
    except GoldError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GoldError(f"黄金历史获取失败：{exc}") from exc

    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            if kind == "au0":
                out.append({
                    "date": row["d"],
                    "open": float(row["o"]), "high": float(row["h"]),
                    "low": float(row["l"]), "close": float(row["c"]),
                    "volume": float(row.get("v") or 0) or 1.0,
                })
            else:
                out.append({
                    "date": row["date"],
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    # GC 日线成交量为 0，用 1 占位避免量比除零
                    "volume": float(row.get("volume") or 0) or 1.0,
                })
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise GoldError("黄金历史数据为空")
    out.sort(key=lambda r: r["date"])
    _CACHE[f"daily:{kind}"] = (time.time() + 900, out)
    return out


def fetch_quote(kind: str = "au0") -> dict[str, Any]:
    kind = kind if kind in _URLS else "au0"
    cached = _CACHE.get(f"quote:{kind}")
    if cached and cached[0] > time.time():
        return cached[1]

    try:
        text = _get("https://hq.sinajs.cn/list=nf_AU0,hf_GC")
    except Exception as exc:  # noqa: BLE001
        raise GoldError(f"黄金报价获取失败：{exc}") from exc

    quote: dict[str, Any] = {}
    for line in text.split(";"):
        try:
            if "hq_str_nf_AU0" in line and kind == "au0":
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
                raw_t = f[1]
                clock = f"{raw_t[:2]}:{raw_t[2:4]}:{raw_t[4:6]}" if len(raw_t) == 6 else raw_t
                quote = {
                    "symbol": "au0", "code": "AU0", "name": "黄金连续",
                    "price": price, "prev_close": prev, "open": float(f[2]),
                    "high": float(f[3]), "low": float(f[4]),
                    "change": price - prev,
                    "change_pct": (price / prev - 1) * 100 if prev else 0,
                    "currency": "CNY", "market": "gold",
                    "time": f"{f[17]} {clock}" if len(f) > 17 else "",
                }
            elif "hq_str_hf_GC" in line and kind == "gc":
                f = line.split('"')[1].split(",")
                price = float(f[0])
                prev = float(f[7])
                quote = {
                    "symbol": "gc", "code": "GC", "name": "纽约黄金",
                    "price": price, "prev_close": prev, "open": float(f[8]),
                    "high": float(f[4]), "low": float(f[5]),
                    "change": price - prev,
                    "change_pct": (price / prev - 1) * 100 if prev else 0,
                    "currency": "USD", "market": "gold",
                    "time": f"{f[12]} {f[6]}" if len(f) > 12 else "",
                }
        except (IndexError, ValueError):
            continue
    if not quote:
        raise GoldError("黄金报价解析失败")
    _CACHE[f"quote:{kind}"] = (time.time() + 10, quote)
    return quote
