"""
行情数据层
==========
数据源：腾讯财经公开行情接口（无需 Key）
  · 名称/代码联想  https://smartbox.gtimg.cn/s3/
  · 实时快照      https://qt.gtimg.cn/q=
  · 日线 K 线     https://web.ifzq.gtimg.cn/appstock/app/fqkline/get

职责：代码归一化、股票搜索、前复权日线抓取（自动分页）、实时报价解析、
      短 TTL 内存缓存，避免频繁请求。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://gu.qq.com/",
}

KLINE_URLS = (
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
)
QUOTE_URL = "https://qt.gtimg.cn/q="
SEARCH_URL = "https://smartbox.gtimg.cn/s3/"

# 腾讯单次最多返回约 640 根，分页向前回溯
MAX_BARS_PER_CALL = 640


class MarketError(Exception):
    """行情获取失败（网络异常或代码无效）。"""


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: str, ttl: float) -> Any | None:
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        expire, value = item
        if expire < time.time():
            _cache.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: Any, ttl: float) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


# --------------------------------------------------------------------------- #
# HTTP 基础
# --------------------------------------------------------------------------- #
def _http_get(url: str, encoding: str = "utf-8", retries: int = 3,
              timeout: float = 12.0) -> str:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, "ignore")
        except Exception as exc:  # noqa: BLE001 - 网络层统一兜底
            last_err = exc
            time.sleep(0.6 * (attempt + 1))
    raise MarketError(f"网络请求失败：{last_err}") from last_err


def _unescape(text: str) -> str:
    """腾讯搜索接口返回 \\uXXXX 转义，转回中文。"""
    if "\\u" not in text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape")
    except Exception:  # noqa: BLE001
        return text


# --------------------------------------------------------------------------- #
# 代码解析
# --------------------------------------------------------------------------- #
@dataclass
class Security:
    symbol: str        # sh600519 / usAAPL
    code: str          # 600519 / AAPL
    name: str
    market: str        # sh / sz / bj / us
    kind: str = "A股"

    def as_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "code": self.code, "name": self.name,
                "market": self.market, "kind": self.kind}


# 常用美股 / 商品 ETF 的中文别名
US_ALIASES = {
    "苹果": "AAPL", "苹果公司": "AAPL", "特斯拉": "TSLA", "英伟达": "NVDA",
    "微软": "MSFT", "亚马逊": "AMZN", "谷歌": "GOOGL", "奈飞": "NFLX",
    "脸书": "META", "台积电": "TSM", "阿里巴巴": "BABA", "拼多多": "PDD",
    "京东": "JD", "百度": "BIDU", "蔚来": "NIO", "小鹏": "XPEV",
    "黄金": "GLD", "原油": "USO", "石油": "USO", "白银": "SLV", "天然气": "UNG",
    "标普500": "SPY", "纳斯达克": "QQQ", "道琼斯": "DIA",
}

# 黄金期货连续合约（新浪历史 + 报价）
GOLD_ALIASES = {
    "黄金连续": "au0", "沪金连续": "au0", "沪金": "au0", "au0": "au0",
    "纽约黄金": "gc", "comex黄金": "gc", "COMEX黄金": "gc", "gc": "gc",
}
US_DISPLAY = {
    "GLD": "黄金ETF-SPDR", "USO": "美国原油ETF", "SLV": "白银ETF-iShares",
    "UNG": "美国天然气ETF", "SPY": "标普500ETF", "QQQ": "纳指100ETF",
    "DIA": "道琼斯ETF",
}
US_KIND = {
    "GLD": "商品ETF", "USO": "商品ETF", "SLV": "商品ETF", "UNG": "商品ETF",
    "SPY": "指数ETF", "QQQ": "指数ETF", "DIA": "指数ETF",
}


def _us_security(ticker: str, name: str = "") -> Security:
    ticker = ticker.upper()
    return Security(f"us{ticker}", ticker,
                    name or US_DISPLAY.get(ticker, ticker), "us",
                    US_KIND.get(ticker, "美股"))


def _infer_market(code: str) -> str:
    if code.startswith(("60", "68", "69", "5", "11", "90")):
        return "sh"
    if code.startswith(("00", "30", "20", "15", "16", "12", "39")):
        return "sz"
    if code.startswith(("43", "83", "87", "92", "4", "8")):
        return "bj"
    return "sh"


def normalize_symbol(query: str) -> str | None:
    """把 '600519' / 'sh600519' / '600519.SH' / 'AAPL.US' 归一为内部代码。"""
    q = (query or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(sh|sz|bj)[.\-]?(\d{6})", q)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = re.fullmatch(r"(\d{6})[.\-]?(sh|sz|bj)?", q)
    if m:
        code = m.group(1)
        return f"{m.group(2) or _infer_market(code)}{code}"
    m = re.fullmatch(r"us[.\-]?([a-z]{1,6})", q) or re.fullmatch(r"([a-z]{1,6})[.\-]?us", q)
    if m:
        return f"us{m.group(1).upper()}"
    return None


def _parse_search(text: str) -> list[Security]:
    """解析 smartbox 返回：v_hint="sh~600519~贵州茅台~gzmt~GP-A^sz~000858~五粮液~..."。"""
    text = _unescape(text)
    body = text.split('"', 2)[1] if text.count('"') >= 2 else text
    out: list[Security] = []
    for item in body.split("^"):
        parts = item.split("~")
        if len(parts) < 4:
            continue
        market, code, name, _pinyin = parts[0], parts[1], parts[2], parts[3]
        raw_type = parts[4] if len(parts) > 4 else ""
        if market == "us":
            ticker = code.split(".")[0].upper()
            if re.fullmatch(r"[A-Z]{1,6}", ticker):
                out.append(_us_security(ticker, name))
            continue
        if market not in ("sh", "sz", "bj"):
            continue
        if not re.fullmatch(r"\d{6}", code):
            continue
        if raw_type.startswith("GP-A"):
            kind = "科创板" if code.startswith("68") else "A股"
        elif raw_type.startswith("ETF"):
            kind = "ETF"
        elif raw_type.startswith("LOF"):
            kind = "LOF"
        elif raw_type.startswith("ZS"):
            kind = "指数"
        elif raw_type.startswith("GP-B"):
            kind = "B股"
        else:
            kind = raw_type or "证券"
        out.append(Security(f"{market}{code}", code, name, market, kind))
    return out


def search(query: str, limit: int = 8) -> list[dict[str, str]]:
    """按名称、拼音首字母或代码搜索证券。"""
    q = (query or "").strip()
    if not q:
        return []

    key = f"search:{q}"
    cached = _cache_get(key, 120)
    if cached is not None:
        return cached[:limit]

    url = f"{SEARCH_URL}?q={urllib.parse.quote(q)}&t=all"
    try:
        text = _http_get(url, encoding="gbk", retries=2)
        found = _parse_search(text)
    except MarketError:
        found = []

    # 中文别名（苹果 / 黄金 / 原油 …）置顶
    alias = US_ALIASES.get(q)
    if alias:
        found = [_us_security(alias, q)] + [s for s in found if s.symbol != f"us{alias}"]

    # 纯 6 位数字但搜索无结果时，按代码规则兜底
    if not found:
        sym = normalize_symbol(q)
        if sym:
            if sym.startswith("us"):
                found = [_us_security(sym[2:])]
            else:
                found = [Security(sym, sym[2:], q, sym[:2], "证券")]

    # 形如 NVDA 的美股代码，搜索无结果时直接按美股处理
    if not found and re.fullmatch(r"[A-Za-z]{1,6}", q):
        found = [_us_security(q.upper())]

    items = [s.as_dict() for s in found]
    _cache_put(key, items, 120)
    return items[:limit]


def resolve(query: str) -> Security:
    """把用户输入（名称/代码）解析为唯一证券；支持 A 股、美股与商品 ETF。"""
    q = (query or "").strip()
    if not q:
        raise MarketError("请输入股票名称或代码")

    gold_key = GOLD_ALIASES.get(q) or GOLD_ALIASES.get(q.lower())
    if gold_key:
        name, code = ("黄金连续", "AU0") if gold_key == "au0" else ("纽约黄金", "GC")
        return Security(gold_key, code, name, "gold", "期货")

    alias = US_ALIASES.get(q)
    if alias:
        return _us_security(alias, q)

    # 美股指数：us.INX / us.IXIC / us.DJI
    m = re.fullmatch(r"us\.([A-Za-z]{1,6})", q, re.IGNORECASE)
    if m:
        ticker = m.group(1).upper()
        names = {"INX": "标普500", "IXIC": "纳斯达克", "DJI": "道琼斯"}
        return Security(f"us.{ticker}", f".{ticker}",
                        names.get(ticker, ticker), "us", "指数")

    sym = normalize_symbol(q)
    if sym and sym.startswith("us"):
        ticker = sym[2:]
        name = US_DISPLAY.get(ticker, ticker)
        for item in search(q, limit=10):
            if item["symbol"] == sym:
                name = item["name"]
                break
        return Security(sym, ticker, name, "us", US_KIND.get(ticker, "美股"))

    if sym:
        code = sym[2:]
        name = code
        # 尝试补全名称（失败不影响分析）
        for item in search(q, limit=10):
            if item["code"] == code:
                name = item["name"]
                break
        kind = "指数" if code.startswith("000") and sym.startswith(("sh000", "sz399")) else "证券"
        if re.fullmatch(r"(000|399)\d{3}", code):
            kind = "指数"
        return Security(sym, code, name, sym[:2], kind)

    items = search(q, limit=20)
    if not items:
        raise MarketError(f"未找到与「{q}」匹配的股票，请检查名称或输入代码")

    # 名称精确匹配优先，其次 A 股/科创板/美股，再 ETF，最后指数
    def rank(item: dict[str, str]) -> tuple[int, int]:
        exact = 0 if item["name"] == q else (1 if q in item["name"] else 2)
        order = {"A股": 0, "科创板": 0, "美股": 0, "商品ETF": 0,
                 "ETF": 1, "LOF": 1, "指数ETF": 1, "B股": 2, "指数": 3}
        return (exact, order.get(item["kind"], 2))

    best = sorted(items, key=rank)[0]
    return Security(best["symbol"], best["code"], best["name"], best["market"], best["kind"])


# --------------------------------------------------------------------------- #
# 基准指数
# --------------------------------------------------------------------------- #
_BENCHMARKS = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
}


def benchmark_for(code: str, market: str = "") -> dict[str, str]:
    if market == "gold":
        return {"symbol": "usGLD", "code": "GLD", "name": "黄金ETF-SPDR"}
    if market == "us":
        return {"symbol": "usSPY", "code": "SPY", "name": "标普500"}
    if code.startswith(("688", "689")):
        sym = "sh000688"
    elif code.startswith(("300", "301")):
        sym = "sz399006"
    elif code.startswith(("60", "68", "51", "56", "58", "11", "90")):
        sym = "sh000001"
    elif code.startswith(("00", "15", "16", "12")):
        sym = "sz399001"
    else:
        sym = "sh000300"
    return {"symbol": sym, "code": sym[2:], "name": _BENCHMARKS.get(sym, sym)}


# --------------------------------------------------------------------------- #
# 日线
# --------------------------------------------------------------------------- #
_host_block: dict[str, float] = {}


def _host_available(host: str) -> bool:
    return _host_block.get(host, 0.0) < time.time()


def _block_host(host: str, seconds: float = 300.0) -> None:
    _host_block[host] = time.time() + seconds


def _fetch_kline_page(symbol: str, begin: str, end: str) -> list[list[Any]]:
    """按主机列表依次尝试，避开单个域名被 WAF 限流。"""
    errors: list[str] = []
    hosts = [h for h in KLINE_URLS if _host_available(h)] or list(KLINE_URLS)
    for host in hosts:
        url = (f"{host}?param={symbol},day,{begin},{end},"
               f"{MAX_BARS_PER_CALL},qfq")
        try:
            text = _http_get(url, retries=1, timeout=10.0)
            payload = json.loads(text)
        except MarketError as exc:
            errors.append(f"{host.split('/')[2]}: {exc}")
            if "501" in str(exc) or "403" in str(exc):
                _block_host(host)
            continue
        except json.JSONDecodeError:
            errors.append(f"{host.split('/')[2]}: 返回格式异常")
            continue

        data = payload.get("data") or {}
        node = data.get(symbol) or {}
        rows = node.get("qfqday") or node.get("day") or []
        if not rows:
            # 指数没有复权价，接口会放在 day 字段；个别代码返回在其它键下
            for value in data.values():
                if isinstance(value, dict):
                    rows = value.get("qfqday") or value.get("day") or rows
                    if rows:
                        break
        if rows:
            return rows

    detail = errors[-1] if errors else "接口无数据"
    raise MarketError(f"腾讯日线接口不可用（{detail}）")


def _fetch_sina_daily(symbol: str) -> list[dict[str, Any]]:
    """备用数据源之一：新浪财经日线（不复权，最多约 1023 根）。"""
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={symbol}&scale=240&ma=no&datalen=1023")
    text = _http_get(url, retries=2, timeout=12.0)
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarketError("新浪日线接口返回格式异常") from exc
    out = []
    for row in rows:
        try:
            out.append({
                "date": str(row["day"])[:10],
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                # 新浪成交量单位为股，统一折算为「手」
                "volume": float(row["volume"]) / 100.0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        raise MarketError(f"新浪日线接口未返回 {symbol} 数据")
    return out


# 雪球：前复权，需要首页 Cookie
_xq_state: dict[str, Any] = {"opener": None, "ts": 0.0}


def _xueqiu_opener():
    opener = _xq_state.get("opener")
    if opener is not None and time.time() - float(_xq_state.get("ts", 0)) < 3600:
        return opener
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(UA.items())
    opener.open("https://xueqiu.com/", timeout=12).read()
    _xq_state.update({"opener": opener, "ts": time.time()})
    return opener


def _xq_get(url: str, retries: int = 2) -> str:
    last: Exception | None = None
    for _ in range(retries):
        try:
            opener = _xueqiu_opener()
            with opener.open(urllib.request.Request(url, headers=UA), timeout=15) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            _xq_state["opener"] = None
            time.sleep(0.4)
    raise MarketError(f"雪球接口请求失败：{last}") from last


def _fetch_xueqiu_daily(symbol: str, count: int = 3000,
                        begin_ms: int | None = None) -> list[dict[str, Any]]:
    """雪球日线（前复权），单次最多 3000 根，可用 begin 向前翻页。"""
    begin = int(begin_ms if begin_ms is not None else time.time() * 1000)
    api = ("https://stock.xueqiu.com/v5/stock/chart/kline.json"
           f"?symbol={symbol.upper()}&begin={begin}"
           f"&period=day&type=before&count=-{int(count)}&indicator=kline")
    text = _xq_get(api)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MarketError("雪球日线接口返回格式异常") from exc

    data = payload.get("data") or {}
    items = data.get("item") or []
    cols = data.get("column") or []
    pos = {name: i for i, name in enumerate(cols)}
    need = ("timestamp", "open", "high", "low", "close", "volume")
    if not items or any(k not in pos for k in need):
        raise MarketError(f"雪球日线接口未返回 {symbol} 数据")

    out = []
    for row in items:
        try:
            ts = int(row[pos["timestamp"]]) / 1000.0
            out.append({
                "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                "open": float(row[pos["open"]]),
                "close": float(row[pos["close"]]),
                "high": float(row[pos["high"]]),
                "low": float(row[pos["low"]]),
                "volume": float(row[pos["volume"]]) / 100.0,
            })
        except (IndexError, TypeError, ValueError):
            continue
    if not out:
        raise MarketError(f"雪球日线接口未解析出 {symbol} 数据")
    return out


def _fetch_xueqiu_range(symbol: str, start: date) -> list[dict[str, Any]]:
    """从雪球按 3000 根一页向前翻，尽可能覆盖 start 至今。"""
    recs: list[dict[str, Any]] = []
    begin: int | None = None
    for _ in range(8):
        try:
            chunk = _fetch_xueqiu_daily(symbol, count=3000, begin_ms=begin)
        except MarketError:
            # 已翻到上市之前，保留已取到的数据
            if recs:
                break
            raise
        if not chunk:
            break
        recs = chunk + recs
        earliest = min(r["date"] for r in chunk)
        if earliest <= start.isoformat():
            break
        try:
            begin = int(datetime.strptime(earliest, "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            break
        time.sleep(0.2)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in recs:
        key = row["date"]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


def _to_frame(recs: list[dict[str, Any]], start: date, end: date) -> pd.DataFrame:
    df = pd.DataFrame(recs)
    if df.empty:
        raise MarketError("日线数据解析失败")
    df["date"] = pd.to_datetime(df["date"])
    df = df.drop_duplicates("date").sort_values("date").set_index("date")
    df = df[df.index >= pd.Timestamp(start)]
    df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        raise MarketError("所选区间内没有交易数据")
    return df


def fetch_daily(symbol: str, start: date, end: date | None = None) -> pd.DataFrame:
    """抓取 [start, end] 日线（优先前复权），按日期升序返回。

    腾讯接口需分页（单次 640 根）；若腾讯整体不可用（WAF/限流），
    自动降级到新浪日线（不复权），并在 attrs 中标记来源。
    """
    if end is None:
        end = date.today()
    if start > end:
        start, end = end, start

    key = f"kline:{symbol}:{start.isoformat()}:{end.isoformat()}"
    cached = _cache_get(key, 900)
    if cached is not None:
        return cached.copy()

    if symbol in ("au0", "gc"):
        import gold as gold_mod

        recs = gold_mod.fetch_daily(symbol)
        df = pd.DataFrame(recs)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        if df.empty:
            raise MarketError("黄金在该区间没有数据")
        df.attrs["source"] = "sina_futures"
        df.attrs["adjusted"] = "连续合约"
        _cache_put(key, df, 900)
        return df.copy()

    # 美股 / 商品 ETF：腾讯只有最新 1-2 根日线，历史统一走雪球
    # 超过 6 年的 A 股区间也走雪球，避免腾讯多次分页触发限流
    if symbol.startswith("us") or (end - start).days > 365 * 6:
        ticker = symbol[2:] if symbol.startswith("us") else symbol
        recs = _fetch_xueqiu_range(ticker, start)
        df = _to_frame(recs, start, end)
        df.attrs["source"] = "xueqiu"
        df.attrs["adjusted"] = "前复权"
        _cache_put(key, df, 900)
        return df.copy()

    source = "tencent"
    all_rows: list[list[Any]] = []
    try:
        cursor_end = end
        seen: set[str] = set()
        for _ in range(12):
            page = _fetch_kline_page(symbol, start.isoformat(), cursor_end.isoformat())
            new_rows = [r for r in page if r and str(r[0]) not in seen]
            if not new_rows:
                break
            for r in new_rows:
                seen.add(str(r[0]))
            all_rows = new_rows + all_rows
            earliest = min(str(r[0]) for r in new_rows)
            if earliest <= start.isoformat():
                break
            prev_day = datetime.strptime(earliest, "%Y-%m-%d").date() - timedelta(days=1)
            if prev_day <= start:
                break
            cursor_end = prev_day
            time.sleep(0.15)
        if not all_rows:
            raise MarketError("腾讯接口无数据")
        recs = []
        for row in all_rows:
            if len(row) < 6:
                continue
            try:
                recs.append({
                    "date": str(row[0])[:10],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]),
                })
            except (TypeError, ValueError):
                continue
        df = _to_frame(recs, start, end)
    except MarketError as tencent_err:
        source = ""
        errors = [str(tencent_err)]

        def _xq_range(sym: str) -> list[dict[str, Any]]:
            return _fetch_xueqiu_range(sym[2:] if sym.startswith("us") else sym, start)

        for name, fetcher, src, adj in (
            ("雪球", _xq_range, "xueqiu", "前复权"),
            ("新浪", _fetch_sina_daily, "sina", "不复权"),
        ):
            try:
                recs = fetcher(symbol)
                df = _to_frame(recs, start, end)
                source, adjusted = src, adj
                break
            except MarketError as exc:
                errors.append(f"{name}：{exc}")
        else:
            raise MarketError("行情获取失败：" + "；".join(errors))
        df.attrs["adjusted"] = adjusted

    df.attrs["source"] = source
    if "adjusted" not in df.attrs:
        df.attrs["adjusted"] = "前复权" if source == "tencent" else "不复权"
    _cache_put(key, df, 900)
    return df.copy()


# --------------------------------------------------------------------------- #
# 实时报价
# --------------------------------------------------------------------------- #
def fetch_quote(symbol: str) -> dict[str, Any]:
    """解析腾讯实时快照。字段位置按接口文档固定。"""
    if symbol in ("au0", "gc"):
        import gold as gold_mod

        return gold_mod.fetch_quote(symbol)
    key = f"quote:{symbol}"
    cached = _cache_get(key, 4)
    if cached is not None:
        return dict(cached)

    text = _http_get(f"{QUOTE_URL}{symbol}", encoding="gbk", retries=2)
    if '"' not in text:
        raise MarketError("实时报价接口无返回")
    body = text.split('"')[1]
    f = body.split("~")
    if len(f) < 40:
        raise MarketError("实时报价字段不完整")

    def num(idx: int) -> float | None:
        try:
            v = float(f[idx])
            return v
        except (IndexError, ValueError):
            return None

    ts = f[30] if len(f) > 30 else ""
    quote_time = ""
    if "-" in ts:
        quote_time = ts[:19]
    elif len(ts) >= 14:
        quote_time = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"

    is_us = symbol.startswith("us")
    currency = f[35].strip() if len(f) > 35 and f[35].strip() in ("USD", "CNY", "HKD") else (
        "USD" if is_us else "CNY")
    amount_raw = num(37)
    amount = amount_raw if is_us else (amount_raw * 1e4 if amount_raw is not None else None)

    out = {
        "name": f[1],
        "code": f[2],
        "price": num(3),
        "prev_close": num(4),
        "open": num(5),
        "volume": num(6),                 # 手（A股）/ 股（美股）
        "time": quote_time,
        "change": num(31),
        "change_pct": num(32),
        "high": num(33),
        "low": num(34),
        "amount": amount,                 # 基础货币（元 / 美元）
        "turnover": num(38),              # %
        "pe": num(39),
        "amplitude": num(43),             # %
        "float_mcap": num(44),            # 亿
        "total_mcap": num(45),            # 亿
        "pb": num(46) if not is_us else None,
        "volume_ratio": num(49) if not is_us else None,
        "currency": currency,
    }
    _cache_put(key, out, 4)
    return dict(out)


def add_today_bar(df: pd.DataFrame, quote: dict[str, Any]) -> pd.DataFrame:
    """若当日行情尚未进入日线，用快照补一根。"""
    if not quote.get("time") or quote.get("price") is None:
        return df
    qday = pd.Timestamp(quote["time"][:10])
    if qday <= df.index[-1]:
        return df
    price = float(quote["price"])
    row = {
        "open": float(quote.get("open") or price),
        "close": price,
        "high": float(quote.get("high") or price),
        "low": float(quote.get("low") or price),
        "volume": float(quote.get("volume") or 0.0),
    }
    merged = pd.concat([df, pd.DataFrame([row], index=pd.DatetimeIndex([qday]))])
    merged.attrs = dict(df.attrs)
    return merged.sort_index()


def source_links(symbol: str, code: str, market: str = "") -> list[dict[str, str]]:
    """数据来源与外部行情网页（前端直接可点）。"""
    if market == "gold":
        if code == "AU0":
            return [
                {"label": "新浪期货", "url": "https://finance.sina.com.cn/futures/quotes/AU0.shtml",
                 "note": "沪金连续 K 线 / 持仓"},
                {"label": "上海黄金交易所", "url": "https://www.sge.com.cn/",
                 "note": "现货报价 / 库存"},
            ]
        return [
            {"label": "新浪期货", "url": "https://finance.sina.com.cn/futures/quotes/GC.shtml",
             "note": "COMEX 黄金 K 线"},
            {"label": "COMEX", "url": "https://www.cmegroup.com/markets/metals/precious/gold.html",
             "note": "合约规格 / 持仓"},
        ]
    if market == "us":
        return [
            {"label": "雪球", "url": f"https://xueqiu.com/S/{code}",
             "note": "行情 / 讨论"},
            {"label": "腾讯自选股", "url": f"https://gu.qq.com/us{code}/gp",
             "note": "实时行情"},
            {"label": "东方财富", "url": f"https://quote.eastmoney.com/us/{code}.html",
             "note": "资料 / 财报"},
            {"label": "英为财情", "url": f"https://cn.investing.com/search/?q={code}",
             "note": "新闻 / 分析"},
        ]
    return [
        {"label": "腾讯自选股", "url": f"https://gu.qq.com/{symbol}/gp",
         "note": "实时行情 / 资金流"},
        {"label": "东方财富", "url": f"https://quote.eastmoney.com/{symbol}.html",
         "note": "F10 资料 / 研报"},
        {"label": "新浪财经", "url": f"https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml",
         "note": "新闻 / 公告"},
        {"label": "同花顺", "url": f"https://stockpage.10jqka.com.cn/{code}/",
         "note": "财报 / 概念"},
        {"label": "雪球", "url": f"https://xueqiu.com/S/{symbol.upper()}",
         "note": "讨论 / 组合"},
    ]
