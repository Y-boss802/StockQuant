"""
量化评分板
==========
分别对 A 股与美股计算五维评分，按分数排序取前 100。

候选池：
  · A 股：新浪成交额前 200（剔除 ST/退市/低价）
  · 美股：纳斯达克全市场按市值前 200（剔除 ETF）

评分直接复用 analysis 的技术面、相对基准与五维评分模型，
日线用雪球最近 260 根，结果缓存 10 分钟，后台线程计算。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd

import analysis
import market
import rank

_POOL = 200
_TOP = 100
_TTL = 600

_CACHE: dict[str, dict[str, Any]] = {}
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_HEADERS = {**market.UA, "Referer": "https://finance.sina.com.cn/"}


def _cn_candidates(limit: int = _POOL) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pages = (limit + 99) // 100
    for page in range(1, pages + 1):
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"Market_Center.getHQNodeData?page={page}&num=100&sort=amount"
               "&asc=0&node=hs_a")
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                rows.extend(json.loads(resp.read().decode("gbk", "ignore")) or [])
        except Exception:  # noqa: BLE001
            continue

    out: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").split(" Common Stock")[0].strip()
        symbol = str(row.get("symbol") or "").lower()
        if not symbol or "ST" in name or "退" in name:
            continue
        try:
            price = float(row.get("trade") or 0)
            pct = float(row.get("changepercent") or 0)
        except (TypeError, ValueError):
            continue
        if price < 2:
            continue
        out.append({"symbol": symbol, "code": str(row.get("code") or ""),
                    "name": name, "price": price, "change_pct": pct})
        if len(out) >= limit:
            break
    return out


def _us_candidates(limit: int = _POOL) -> list[dict[str, Any]]:
    url = ("https://api.nasdaq.com/api/screener/stocks"
           "?tableonly=true&limit=10000&offset=0")
    headers = {**market.UA, "Accept": "application/json",
               "Origin": "https://www.nasdaq.com",
               "Referer": "https://www.nasdaq.com/"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8", "ignore"))
        rows = (payload.get("data") or {}).get("table", {}).get("rows") or []
    except Exception:  # noqa: BLE001
        return []

    def cap(row: dict[str, Any]) -> float:
        try:
            return float(str(row.get("marketCap", "0")).replace(",", ""))
        except ValueError:
            return 0.0

    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=cap, reverse=True):
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name") or "").split(" Common Stock")[0].strip()
        if not symbol.isalpha() or "ETF" in name.upper():
            continue
        try:
            price = float(str(row.get("lastsale", "")).replace("$", "").replace(",", ""))
            pct = float(str(row.get("pctchange", "")).replace("%", "").replace(",", ""))
        except ValueError:
            continue
        if price < 3 or not name:
            continue
        out.append({"symbol": f"us{symbol}", "code": symbol, "name": name,
                    "price": price, "change_pct": pct})
        if len(out) >= limit:
            break

    # 腾讯批量快照补中文名（美股）
    names: dict[str, str] = {}
    codes = [item["code"] for item in out]
    for start in range(0, len(codes), 60):
        try:
            for quote in rank._us_quote_batch(codes[start:start + 60]):  # noqa: SLF001
                if quote.get("name"):
                    names[quote["code"]] = quote["name"]
        except Exception:  # noqa: BLE001
            continue
    for item in out:
        chinese = names.get(item["code"])
        if chinese:
            item["name"] = chinese
    return out


def _frame(symbol: str) -> pd.DataFrame:
    ticker = symbol[2:] if symbol.startswith("us") else symbol
    recs = market._fetch_xueqiu_daily(ticker.upper(), count=260)  # noqa: SLF001
    if len(recs) < 120:
        raise ValueError("数据不足")
    df = pd.DataFrame(recs)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def _benchmark(market_key: str) -> pd.DataFrame:
    return _frame("sh000001" if market_key == "cn" else "usSPY")


def _score_one(item: dict[str, Any], bench: pd.DataFrame,
               bench_ret: pd.Series, bench_perf: dict[str, Any]) -> dict[str, Any] | None:
    try:
        df = _frame(item["symbol"])
        tech = analysis.technical_snapshot(df)
        ret = df["close"].pct_change()
        perf = analysis.perf_stats(ret)
        rel = analysis.relative_stats(ret, bench_ret)
        sc = analysis.score_model(tech, rel, df["close"], bench["close"], perf, bench_perf)
        close = df["close"]
        n = min(252, len(close) - 1)
        annual_1y = (((float(close.iloc[-1]) / float(close.iloc[-1 - n])) ** (252.0 / n)) - 1) * 100 if n > 0 else 0.0
        return {
            "symbol": item["symbol"],
            "code": item["code"],
            "name": item["name"],
            "price": round(float(df["close"].iloc[-1]), 2),
            "change_pct": round(float(df["close"].pct_change().iloc[-1] * 100), 2),
            "annual_1y": round(annual_1y, 2),
            "vol": round(float(perf["annual_vol"]) * 100, 1),
            "score": round(float(sc["total"]), 1),
            "grade": sc["grade"],
            "signal": sc["signal"],
            "tone": sc["tone"],
        }
    except Exception:  # noqa: BLE001
        return None


def _build(market_key: str) -> None:
    started = time.time()
    with _LOCK:
        _JOBS[market_key] = {"done": 0, "total": 0, "started": started}
    try:
        candidates = _cn_candidates() if market_key == "cn" else _us_candidates()
        bench = _benchmark(market_key)
        bench_ret = bench["close"].pct_change()
        bench_perf = analysis.perf_stats(bench_ret)
        with _LOCK:
            _JOBS[market_key]["total"] = len(candidates)

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_score_one, item, bench, bench_ret, bench_perf): item
                       for item in candidates}
            for fut in as_completed(futures):
                row = fut.result()
                if row:
                    results.append(row)
                with _LOCK:
                    _JOBS[market_key]["done"] += 1

        # 财报质量因子：ROE / PE 轻量调整评分（C 阶段）
        try:
            import fundamentals
            data = {}
            for start in range(0, len(results), 80):
                chunk = results[start:start + 80]
                data.update(fundamentals._fetch(
                    [fundamentals._secid(market_key, r["code"]) for r in chunk]))
            for r in results:
                raw = data.get(str(r["code"]), {})
                roe = raw.get("f37")
                pe = raw.get("f9")
                bonus = 0.0
                if isinstance(roe, (int, float)):
                    bonus += max(-2.0, min(3.0, float(roe) / 10.0))
                if isinstance(pe, (int, float)):
                    bonus += 1.0 if 0 < float(pe) < 30 else (-1.0 if float(pe) < 0 else 0.0)
                r["score"] = round(max(0.0, min(100.0, r["score"] + bonus)), 1)
                r["roe"] = roe
                r["pe"] = pe
        except Exception:  # noqa: BLE001
            pass

        results.sort(key=lambda r: r["score"], reverse=True)
        _CACHE[market_key] = {
            "ts": time.time(),
            "items": results[:_TOP],
            "scanned": len(results),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        with _LOCK:
            _JOBS.pop(market_key, None)


def get_scoreboard(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    cached = _CACHE.get(market_key)
    if cached and time.time() - cached["ts"] < _TTL:
        return {"status": "ok", "market": market_key, **cached}

    with _LOCK:
        running = market_key in _JOBS
        if not running:
            _JOBS[market_key] = {"done": 0, "total": 0, "started": time.time()}
    if not running:
        threading.Thread(target=_build, args=(market_key,), daemon=True).start()

    with _LOCK:
        job = dict(_JOBS.get(market_key, {}))
    return {
        "status": "computing",
        "market": market_key,
        "progress": job.get("done", 0),
        "total": job.get("total", 0),
        "partial": (cached or {}).get("items", []),
    }
