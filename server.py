"""
量析 StockQuant — 本地 Web 服务
==============================
零第三方依赖，只用 Python 标准库 + pandas/numpy。

启动：python server.py [--port 8765] [--open]
接口：
  GET /api/search?q=贵州茅台        名称/代码联想
  GET /api/analyze?q=600519&years=3  完整量化分析
  GET /api/quote?q=600519            实时快照
  GET /api/health                    健康检查
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analysis  # noqa: E402
import fundamentals  # noqa: E402
import market  # noqa: E402
import monitor  # noqa: E402
import overview  # noqa: E402
import paper  # noqa: E402
import portfolio  # noqa: E402
import predict  # noqa: E402
import quality  # noqa: E402
import rank  # noqa: E402
import research  # noqa: E402
import scoreboard  # noqa: E402
import simulate  # noqa: E402
import strategy  # noqa: E402
import track  # noqa: E402
import tracking  # noqa: E402


def _resource(rel: str) -> str:
    """PyInstaller 打包后静态资源位于 _MEIPASS，源码运行时位于脚本目录。"""
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, rel)


WEB_DIR = _resource("web")
DEFAULT_PORT = 8765
LOG_PATH = os.path.join(HERE, "data", "server.log")
TOKEN_FILE = os.path.join(HERE, "data", "access_token.txt")


def _load_access_token() -> str:
    """局域网访问口令，保存在 data/access_token.txt，重启不变。"""
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            token = fh.read().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(6)
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
            fh.write(token)
    except OSError:
        pass
    return token


ACCESS_TOKEN = _load_access_token()

_qr_cache: dict[str, bytes] = {}


def log_error(message: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except Exception:  # noqa: BLE001
        pass


def lan_ip() -> str:
    """取本机在局域网中的地址（不会真的发包）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def qr_png(url: str) -> bytes:
    cached = _qr_cache.get(url)
    if cached:
        return cached
    import qrcode

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#0b1220", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()
    _qr_cache[url] = data
    return data
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# JSON 清洗
# --------------------------------------------------------------------------- #
def sanitize(obj):
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return round(v, 6) if math.isfinite(v) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.strftime("%Y-%m-%d")
    if obj is pd.NaT:
        return None
    return obj


def _cache_get(key: str, ttl: float):
    with _cache_lock:
        item = _cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        if item:
            _cache.pop(key, None)
    return None


def _cache_put(key: str, value, ttl: float) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


# --------------------------------------------------------------------------- #
# 业务逻辑
# --------------------------------------------------------------------------- #
def run_analysis(query: str, years: int = 3, fast: int = 20, slow: int = 60,
                 force: bool = False) -> dict:
    sec = market.resolve(query)
    years = max(1, min(30, int(years)))
    key = f"analysis:{sec.symbol}:{years}:{fast}:{slow}"
    if not force:
        cached = _cache_get(key, 300)
        if cached:
            payload = dict(cached)
            payload["meta"] = dict(cached["meta"], cached=True)
            return payload

    end = date.today()
    start = end - timedelta(days=365 * years + 45)
    bench = market.benchmark_for(sec.code, sec.market)
    if sec.kind == "指数":
        # 指数本身作为标的时，用上证指数（或沪深300）作参照
        if sec.symbol == "sh000001":
            bench = {"symbol": "sh000300", "code": "000300", "name": "沪深300"}
        else:
            bench = {"symbol": "sh000001", "code": "000001", "name": "上证指数"}

    df = market.fetch_daily(sec.symbol, start, end)
    bdf = market.fetch_daily(bench["symbol"], start, end)

    source = df.attrs.get("source", "tencent")
    adjusted = df.attrs.get("adjusted", "前复权")

    quote: dict = {}
    try:
        quote = market.fetch_quote(sec.symbol)
        df = market.add_today_bar(df, quote)
    except market.MarketError:
        quote = {}

    # 行情日期与快照名称对齐
    if quote.get("name"):
        sec.name = quote["name"]

    result = analysis.analyze(
        df, bdf,
        security=sec.as_dict(), bench_meta=bench,
        fast=int(fast), slow=int(slow), quote=quote, adjusted=adjusted,
    )
    currency = quote.get("currency") or ("USD" if sec.market == "us" else "CNY")
    result["meta"]["currency"] = currency
    result["meta"]["links"] = market.source_links(sec.symbol, sec.code, sec.market)
    result["meta"]["source"] = {
        "tencent": "腾讯财经（前复权日线 + 实时快照）",
        "xueqiu": "雪球（前复权日线 · 备用数据源）",
        "sina": "新浪财经（不复权日线 · 备用数据源）",
    }.get(source, "公开行情接口")
    if sec.market == "us":
        result["meta"]["source"] = "雪球（前复权日线 + 腾讯实时快照）"
    result["meta"]["fallback"] = source != "tencent" and sec.market != "us"
    result["meta"]["cached"] = False
    clean = sanitize(result)
    _cache_put(key, clean, 300)
    return clean


# --------------------------------------------------------------------------- #
# HTTP 处理
# --------------------------------------------------------------------------- #
class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 下 SO_REUSEADDR 允许重复绑定同一端口，需关闭才能检测占用
    allow_reuse_address = os.name != "nt"


class Handler(SimpleHTTPRequestHandler):
    server_version = "StockQuant/1.0"
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".webmanifest": "application/manifest+json",
    }

    _grant_token = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def _is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _authorized(self) -> bool:
        if self._is_local():
            return True
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001
            pass
        if cookies.get("sq_token") and cookies["sq_token"].value == ACCESS_TOKEN:
            return True
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if query.get("k", [""])[0] == ACCESS_TOKEN:
            self._grant_token = True
            return True
        return False

    def _unauthorized(self, path: str) -> None:
        if path.startswith("/api/"):
            return self._send_json({"ok": False, "error": "需要访问口令：请从电脑端「手机」扫码访问"}, 401)
        body = ("<!doctype html><meta charset='utf-8'><title>需要访问口令</title>"
                "<body style='background:#070b16;color:#dfe7f8;font-family:sans-serif;padding:40px'>"
                "<h2>需要访问口令</h2>"
                "<p>这是本机局域网服务，未授权设备无法访问。</p>"
                "<p>请在电脑上点「手机」按钮，用手机扫描二维码进入。</p></body>")
        raw = body.encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def list_directory(self, path):  # noqa: D401
        self.send_error(404)
        return None

    # 静默默认日志
    def log_message(self, fmt: str, *args) -> None:  # noqa: D401
        if os.environ.get("SQ_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    # 静态资源禁缓存，避免浏览器混用旧版 JS/CSS
    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'",
        )
        if getattr(self, "_grant_token", False):
            self.send_header(
                "Set-Cookie",
                f"sq_token={ACCESS_TOKEN}; Path=/; HttpOnly; SameSite=Lax; Max-Age=31536000",
            )
            self._grant_token = False
        path = urllib.parse.urlparse(self.path).path
        if path.endswith((".html", ".js", ".css")):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def _send_zip(self) -> None:
        """下载免安装包（仅源码运行时存在）。"""
        zip_path = os.path.join(HERE, "dist", "StockQuant-win64.zip")
        if not os.path.isfile(zip_path):
            return self._error("安装包不存在，请先在源码目录运行 build_exe.ps1", 404)
        size = os.path.getsize(zip_path)
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition",
                         'attachment; filename="StockQuant-win64.zip"')
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(zip_path, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, 1024 * 256)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if not self._authorized():
                return self._unauthorized(path)
            if path in ("/download/app", "/download/StockQuant-win64.zip"):
                return self._send_zip()
            if path == "/api/live":
                raw = (query.get("symbols") or [""])[0]
                symbols = [s for s in raw.split(",") if s][:80]
                data = {}
                for sym in symbols:
                    try:
                        q = market.fetch_quote(sym)
                        data[sym] = {"price": q.get("price"),
                                     "change_pct": q.get("change_pct"),
                                     "time": q.get("time")}
                    except Exception:  # noqa: BLE001
                        continue
                return self._send_json({"ok": True, "data": data})
            if path == "/api/health":
                return self._send_json({"ok": True, "service": "stockquant",
                                        "time": datetime.now().isoformat(timespec="seconds")})
            if path == "/api/simulate":
                key = (query.get("market") or ["cn"])[0]
                try:
                    amount = float((query.get("amount") or ["100000"])[0])
                    days = int(float((query.get("days") or ["30"])[0]))
                except ValueError:
                    return self._error("金额或天数格式不正确", 400)
                risk = (query.get("risk") or ["balanced"])[0]
                return self._send_json({"ok": True, "data": simulate.simulate(key, amount, days, risk)})
            if path == "/api/monitor":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(monitor.run_monitor(key))})
            if path == "/api/quality":
                key = (query.get("market") or ["cn"])[0]
                try:
                    size = int((query.get("size") or ["20"])[0])
                except ValueError:
                    size = 20
                return self._send_json({"ok": True, "data": sanitize(quality.run_quality(key, size))})
            if path == "/api/paper/stream":
                key = (query.get("market") or ["cn"])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                for _ in range(120):
                    try:
                        payload = json.dumps({"ok": True, "data": sanitize(paper.get_state(key))}, ensure_ascii=False)
                        self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(5)
                return
            if path == "/api/paper":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(paper.get_state(key))})
            if path == "/api/fundamentals":
                key = (query.get("market") or ["cn"])[0]
                try:
                    size = int((query.get("size") or ["20"])[0])
                except ValueError:
                    size = 20
                return self._send_json({"ok": True, "data": sanitize(fundamentals.get_fundamentals(key, size))})
            if path == "/api/portfolio":
                key = (query.get("market") or ["cn"])[0]
                method = (query.get("method") or ["max_sharpe"])[0]
                try:
                    size = int((query.get("size") or ["20"])[0])
                    cap = float((query.get("cap") or ["0.25"])[0])
                except ValueError:
                    size, cap = 20, 0.25
                return self._send_json({"ok": True, "data": sanitize(portfolio.run_portfolio(key, method, size, cap))})
            if path == "/api/tracking":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(tracking.get(key))})
            if path == "/api/strategy":
                key = (query.get("market") or ["cn"])[0]
                try:
                    lookback = int((query.get("lookback") or ["60"])[0])
                    top = int((query.get("top") or ["10"])[0])
                except ValueError:
                    lookback, top = 60, 10
                return self._send_json({"ok": True, "data": sanitize(strategy.run_strategy(key, lookback, top))})
            if path == "/api/research":
                key = (query.get("market") or ["cn"])[0]
                factor = (query.get("factor") or ["mom20"])[0]
                try:
                    size = int((query.get("size") or ["40"])[0])
                except ValueError:
                    size = 40
                return self._send_json({"ok": True, "data": sanitize(research.run_research(key, factor, size))})
            if path == "/api/scoreboard":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(scoreboard.get_scoreboard(key))})
            if path == "/api/overview":
                return self._send_json({"ok": True, "data": sanitize(overview.get_overview())})
            if path == "/api/network":
                ip = lan_ip()
                port = int(self.server.server_address[1])
                url = f"http://{ip}:{port}/?k={ACCESS_TOKEN}"
                return self._send_json({"ok": True, "data": {
                    "ip": ip, "port": port, "url": url}})
            if path == "/api/qr.png":
                ip = lan_ip()
                port = int(self.server.server_address[1])
                data = qr_png(f"http://{ip}:{port}/?k={ACCESS_TOKEN}")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/search":
                q = (query.get("q") or [""])[0]
                return self._send_json({"ok": True, "matches": market.search(q, limit=8)})
            if path == "/api/quote":
                q = (query.get("q") or [""])[0]
                sym = market.normalize_symbol(q)
                if not sym:
                    return self._error("代码无效")
                return self._send_json({"ok": True, "quote": market.fetch_quote(sym)})
            if path == "/api/predict":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(predict.get_prediction(key))})
            if path == "/api/review":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(track.get_review(key))})
            if path == "/api/rank":
                key = (query.get("market") or ["cn"])[0]
                return self._send_json({"ok": True, "data": sanitize(rank.get_rank(key))})
            if path == "/api/analyze":
                q = (query.get("q") or [""])[0].strip()
                years = int((query.get("years") or ["3"])[0])
                fast = int((query.get("fast") or ["20"])[0])
                slow = int((query.get("slow") or ["60"])[0])
                force = (query.get("force") or ["0"])[0] == "1"
                if not q:
                    return self._error("请输入股票名称或 6 位代码")
                payload = run_analysis(q, years, fast, slow, force)
                return self._send_json({"ok": True, "data": payload})
            if path == "/api/refresh":
                q = (query.get("q") or [""])[0].strip()
                if not q:
                    return self._error("缺少参数 q")
                return self._send_json({"ok": True,
                                        "data": run_analysis(q, int((query.get("years") or ["3"])[0]),
                                                             int((query.get("fast") or ["20"])[0]),
                                                             int((query.get("slow") or ["60"])[0]),
                                                             force=True)})
            if path in ("/", "/index.html"):
                self.path = "/index.html"
                return super().do_GET()
            return super().do_GET()
        except market.MarketError as exc:
            log_error(f"{self.path} -> {exc}")
            return self._error(str(exc), 502)
        except ValueError as exc:
            return self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            log_error(traceback.format_exc())
            traceback.print_exc()
            return self._error(f"服务内部错误：{exc}", 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/api/paper/trade", "/api/paper/reset",
                               "/api/tracking/create", "/api/tracking/update",
                               "/api/tracking/reset"):
            return self._error("接口不存在", 404)
        if not self._authorized():
            return self._unauthorized(parsed.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            return self._error("请求体不是合法 JSON", 400)
        market_key = str(body.get("market") or "cn")
        if parsed.path.startswith("/api/tracking/"):
            action = parsed.path.rsplit("/", 1)[-1]
            if action == "create":
                data = tracking.create(market_key, int(body.get("lookback") or 60),
                                      int(body.get("top") or 10))
            elif action == "update":
                data = tracking.update(market_key)
            else:
                data = tracking.reset(market_key)
            return self._send_json({"ok": True, "data": sanitize(data)})
        if parsed.path.endswith("/trade"):
            data = paper.trade(market_key, str(body.get("q") or ""),
                               str(body.get("side") or "buy"),
                               float(body.get("qty") or 0))
        else:
            data = paper.reset(market_key, body.get("cash"))
        return self._send_json({"ok": True, "data": sanitize(data)})


def main() -> None:
    parser = argparse.ArgumentParser(description="量析 StockQuant 本地服务")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    if getattr(sys, "frozen", False) and not args.no_open:
        args.open = True

    os.makedirs(WEB_DIR, exist_ok=True)
    httpd = None
    chosen = args.port
    for offset in range(10):
        port = args.port + offset
        try:
            httpd = Server((args.host, port), Handler)
            chosen = port
            break
        except OSError:
            continue
    if httpd is None:
        print(f"端口 {args.port}-{args.port + 9} 均被占用，请用 --port 指定其它端口。")
        sys.exit(1)

    url = f"http://{args.host}:{chosen}"
    print("=" * 58)
    print("  量析 StockQuant · 量化分析工作台")
    print(f"  服务地址：{url}")
    if args.host in ("0.0.0.0", ""):
        print(f"  手机访问：http://{lan_ip()}:{chosen}/ （需扫码口令）")
    print("  数据来源：腾讯财经 / 雪球 / 新浪（自动降级）", flush=True)
    print("  按 Ctrl+C 停止")
    print("=" * 58)
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    # 预热市场概览，避免首屏等待
    threading.Thread(target=overview.get_overview, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
