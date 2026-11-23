"""
量化分析引擎
============
输入一段前复权日线，输出可直接喂给前端的结构化分析结果：
绩效指标、风险指标、技术指标、相对基准归因、多维度量化评分、
支撑阻力、均线策略回测 + 参数网格、蒙特卡洛路径模拟、月度收益矩阵。

所有收益/波动/回撤均为小数（0.1234 = 12.34%），
RSI、ATR%、量比、换手率等为原始数值（点 / % / 倍）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RISK_FREE = 0.015


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _r(x: Any, nd: int = 4) -> float | None:
    """转 float 并处理 NaN / inf，便于 JSON 序列化。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, nd)


def _safe(seq: Iterable[Any], nd: int = 4) -> list[float | None]:
    return [_r(v, nd) for v in seq]


def _interp(value: float | None, xs: Sequence[float], ys: Sequence[float],
            default: float = 50.0) -> float:
    if value is None or not np.isfinite(value):
        return default
    return float(np.interp(float(value), xs, ys))


def _clip(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(min(hi, max(lo, v)))


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, sig)
    hist = (dif - dea) * 2
    return dif, dea, hist


def kdj(df: pd.DataFrame, n: int = 9):
    low_n = df["low"].rolling(n).min()
    high_n = df["high"].rolling(n).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def boll(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std()
    return mid + k * std, mid, mid - k * std


# --------------------------------------------------------------------------- #
# 绩效 / 风险
# --------------------------------------------------------------------------- #
def perf_stats(ret: pd.Series, rf: float = RISK_FREE) -> dict[str, Any]:
    r = ret.dropna()
    if len(r) < 2:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / TRADING_DAYS
    total = float(eq.iloc[-1] - 1)
    cagr = float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan")
    vol = float(r.std() * np.sqrt(TRADING_DAYS))
    downside = float(r[r < 0].std() * np.sqrt(TRADING_DAYS))
    sharpe = float((r.mean() * TRADING_DAYS - rf) / vol) if vol > 0 else float("nan")
    sortino = float((r.mean() * TRADING_DAYS - rf) / downside) if downside > 0 else float("nan")
    ddi = max_drawdown(eq)
    var95 = float(np.percentile(r, 5))
    cvar95 = float(r[r <= var95].mean())
    return {
        "cum_return": total,
        "annual_return": cagr,
        "annual_vol": vol,
        "downside_vol": downside,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": float(cagr / abs(ddi["max_dd"])) if ddi["max_dd"] < 0 else float("nan"),
        "max_dd": ddi["max_dd"],
        "max_dd_start": ddi["start"],
        "max_dd_end": ddi["trough"],
        "max_dd_recover": ddi["recover"],
        "current_dd": ddi["current"],
        "daily_win_rate": float((r > 0).mean()),
        "skew": float(r.skew()),
        "kurt": float(r.kurt()),
        "var95": var95,
        "cvar95": cvar95,
        "best_day": float(r.max()),
        "worst_day": float(r.min()),
        "days": int(len(r)),
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
    }


def max_drawdown(equity: pd.Series) -> dict[str, Any]:
    peak = equity.cummax()
    dd = equity / peak - 1
    trough = dd.idxmin()
    peak_date = equity.loc[:trough].idxmax()
    peak_val = float(equity.loc[peak_date])
    after = equity.loc[trough:]
    recovered = after[after >= peak_val]
    recover = str(recovered.index[0].date()) if len(recovered) else None
    return {
        "max_dd": float(dd.min()),
        "start": str(peak_date.date()),
        "trough": str(trough.date()),
        "recover": recover,
        "current": float(dd.iloc[-1]),
        "series": dd,
    }


def omega_ratio(ret: pd.Series, threshold: float = 0.0) -> float:
    """Omega 比率：阈值以上收益之和 / 阈值以下损失之和。"""
    r = ret.dropna()
    gain = float((r[r > threshold] - threshold).sum())
    loss = float((threshold - r[r < threshold]).sum())
    return gain / loss if loss > 0 else float("nan")


def ulcer_index(equity: pd.Series) -> float:
    """溃疡指数：回撤平方均值的平方根，衡量回撤深度与持续性。"""
    dd = equity / equity.cummax() - 1
    return float(np.sqrt((dd ** 2).mean()))


def ewma_vol(ret: pd.Series, lam: float = 0.94) -> float:
    """RiskMetrics EWMA 波动率（年化）。"""
    r = ret.dropna().to_numpy()
    if len(r) < 20:
        return float("nan")
    var = float(np.var(r))
    for x in r:
        var = lam * var + (1 - lam) * x * x
    return float(np.sqrt(var * TRADING_DAYS))


def garch11_forecast(ret: pd.Series) -> dict[str, float]:
    """GARCH(1,1) 次日波动率，参数用网格近似极大似然估计。"""
    r = ret.dropna().to_numpy()
    if len(r) < 100:
        return {}
    eps = r - r.mean()
    var0 = float(np.var(eps))
    best: tuple[float, float, float, float, float] | None = None
    for alpha in np.arange(0.02, 0.26, 0.02):
        for beta in np.arange(0.60, 0.985, 0.04):
            if alpha + beta >= 0.999:
                continue
            omega = var0 * (1 - alpha - beta)
            s2 = var0
            ll = 0.0
            ok = True
            for e in eps:
                s2 = omega + alpha * e * e + beta * s2
                if s2 <= 1e-12:
                    ok = False
                    break
                ll += -0.5 * (np.log(2 * np.pi * s2) + e * e / s2)
            if not ok:
                continue
            if best is None or ll > best[0]:
                best = (ll, float(alpha), float(beta), float(omega), float(s2))
    if not best:
        return {}
    _, alpha, beta, omega, s2 = best
    forecast = omega + alpha * eps[-1] ** 2 + beta * s2
    return {
        "alpha": alpha,
        "beta": beta,
        "next_vol": float(np.sqrt(max(forecast, 1e-12) * TRADING_DAYS)),
        "persistence": alpha + beta,
    }


def advanced_metrics(ret: pd.Series, equity: pd.Series, perf: dict[str, Any],
                     rel: dict[str, Any], bench_perf: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "omega": _r(omega_ratio(ret)),
        "ulcer": _r(ulcer_index(equity)),
        "ewma_vol": _r(ewma_vol(ret)),
    }
    beta = rel.get("beta")
    if beta and abs(beta) > 1e-6:
        out["treynor"] = _r((perf.get("annual_return", 0) - RISK_FREE) / beta)
    if perf.get("annual_vol"):
        out["kelly"] = _r((perf.get("annual_return", 0) - RISK_FREE)
                          / (perf["annual_vol"] ** 2))
    if perf.get("sharpe") is not None and bench_perf.get("annual_vol"):
        out["m2"] = _r(RISK_FREE + perf["sharpe"] * bench_perf["annual_vol"])
    try:
        garch = garch11_forecast(ret)
        if garch:
            out["garch_vol"] = _r(garch["next_vol"])
            out["garch_persistence"] = _r(garch["persistence"])
    except Exception:  # noqa: BLE001
        pass
    return out


def period_returns(close: pd.Series) -> list[dict[str, Any]]:
    last = float(close.iloc[-1])

    def chg(days: int) -> float | None:
        if len(close) > days:
            return last / float(close.iloc[-days - 1]) - 1
        return None

    # 年初至今
    ytd = None
    prev_year = close[close.index.year < close.index[-1].year]
    if len(prev_year):
        ytd = last / float(prev_year.iloc[-1]) - 1

    items = [
        ("近1周", chg(5)), ("近1月", chg(21)), ("近3月", chg(63)),
        ("近6月", chg(126)), ("近1年", chg(252)), ("近3年", chg(756)),
        ("今年以来", ytd), ("全区间", last / float(close.iloc[0]) - 1),
    ]
    return [{"label": k, "value": _r(v)} for k, v in items if v is not None]


def relative_stats(ret: pd.Series, bench: pd.Series) -> dict[str, Any]:
    common = ret.dropna().index.intersection(bench.dropna().index)
    a, b = ret.loc[common], bench.loc[common]
    if len(a) < 30:
        return {}
    cov = np.cov(a, b)
    beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] > 0 else float("nan")
    rf_d = RISK_FREE / TRADING_DAYS
    alpha_d = float((a.mean() - rf_d) - beta * (b.mean() - rf_d))
    corr = float(np.corrcoef(a, b)[0, 1])
    excess = a - b
    te = float(excess.std() * np.sqrt(TRADING_DAYS))
    return {
        "beta": beta,
        "alpha_annual": alpha_d * TRADING_DAYS,
        "corr": corr,
        "tracking_error": te,
        "info_ratio": float(excess.mean() * TRADING_DAYS / te) if te > 0 else float("nan"),
        "excess_annual": float(excess.mean() * TRADING_DAYS),
    }


# --------------------------------------------------------------------------- #
# 技术面
# --------------------------------------------------------------------------- #
def technical_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    close = df["close"]
    ret = close.pct_change()
    last = float(close.iloc[-1])

    ma = {n: close.rolling(n).mean() for n in (5, 10, 20, 60, 120, 250)}
    dif, dea, hist = macd(close)
    k, d, j = kdj(df)
    r = rsi(close, 14)
    a = atr(df, 14)
    up, mid, low = boll(close)
    bb_width = (up - low) / mid.replace(0, np.nan)
    bb_pos = (close - low) / (up - low).replace(0, np.nan)

    hi52 = float(close.iloc[-252:].max()) if len(close) > 60 else float(close.max())
    lo52 = float(close.iloc[-252:].min()) if len(close) > 60 else float(close.min())
    vol20 = float(ret.iloc[-20:].std() * np.sqrt(TRADING_DAYS))
    vol_hist = float(ret.std() * np.sqrt(TRADING_DAYS))
    vol_series = ret.rolling(20).std().dropna()
    vol_pct = float((vol_series < ret.iloc[-20:].std()).mean() * 100) if len(vol_series) else None

    vol_ma5 = float(df["volume"].rolling(5).mean().iloc[-1])
    vol_ma60 = float(df["volume"].rolling(60).mean().iloc[-1]) if len(df) >= 60 else vol_ma5
    amount_proxy = df["volume"] * close
    amount_20 = float(amount_proxy.iloc[-20:].mean())
    amount_60 = float(amount_proxy.iloc[-60:].mean()) if len(df) >= 60 else amount_20

    ma_vals = {str(n): _r(ma[n].iloc[-1], 3) for n in (5, 10, 20, 60, 120, 250)}
    ma_dev = {}
    for n in (5, 20, 60, 250):
        v = ma[n].iloc[-1]
        ma_dev[str(n)] = _r(last / float(v) - 1) if pd.notna(v) else None

    bull_align = 0
    pairs = ((5, 10), (10, 20), (20, 60), (60, 120))
    for short, long in pairs:
        if pd.notna(ma[short].iloc[-1]) and pd.notna(ma[long].iloc[-1]) \
                and ma[short].iloc[-1] > ma[long].iloc[-1]:
            bull_align += 1

    ma20_slope = None
    if len(close) > 25:
        ma20_slope = float(ma[20].iloc[-1] / ma[20].iloc[-21] - 1)

    macd_cross = "无"
    if len(hist) > 2 and pd.notna(hist.iloc[-1]):
        if hist.iloc[-1] > 0 >= hist.iloc[-2]:
            macd_cross = "金叉"
        elif hist.iloc[-1] < 0 <= hist.iloc[-2]:
            macd_cross = "死叉"

    return {
        "last": last,
        "ma": ma_vals,
        "ma_dev": ma_dev,
        "ma_bull_count": bull_align,
        "ma20_slope": ma20_slope,
        "rsi14": _r(r.iloc[-1], 2),
        "rsi_zone": ("超买" if (r.iloc[-1] or 0) > 70 else
                     "超卖" if (r.iloc[-1] or 100) < 30 else "中性"),
        "macd": {"dif": _r(dif.iloc[-1], 3), "dea": _r(dea.iloc[-1], 3),
                 "hist": _r(hist.iloc[-1], 3), "cross": macd_cross},
        "kdj": {"k": _r(k.iloc[-1], 2), "d": _r(d.iloc[-1], 2), "j": _r(j.iloc[-1], 2)},
        "boll": {"up": _r(up.iloc[-1], 3), "mid": _r(mid.iloc[-1], 3),
                 "low": _r(low.iloc[-1], 3), "pos": _r(bb_pos.iloc[-1], 3),
                 "width": _r(bb_width.iloc[-1])},
        "atr14": _r(a.iloc[-1], 3),
        "atr_pct": _r(a.iloc[-1] / last, 4) if last else None,
        "vol_ratio": _r(vol_ma5 / vol_ma60, 2) if vol_ma60 else None,
        "amount_20": amount_20,
        "amount_trend": _r(amount_20 / amount_60 - 1) if amount_60 else None,
        "hi52": hi52,
        "lo52": lo52,
        "pos52": _r((last - lo52) / (hi52 - lo52), 3) if hi52 > lo52 else None,
        "vol20_ann": _r(vol20),
        "vol_hist_ann": _r(vol_hist),
        "vol_percentile": _r(vol_pct, 1),
    }


# --------------------------------------------------------------------------- #
# 支撑 / 阻力
# --------------------------------------------------------------------------- #
def _swing_points(close: pd.Series, left: int = 5, right: int = 5,
                  lookback: int = 200) -> tuple[list[float], list[float]]:
    seg = close.iloc[-lookback:]
    highs, lows = [], []
    for i in range(left, len(seg) - right):
        win = seg.iloc[i - left:i + right + 1]
        v = seg.iloc[i]
        if v >= win.max():
            highs.append(float(v))
        if v <= win.min():
            lows.append(float(v))
    return highs, lows


def levels(close: pd.Series, tech: dict[str, Any]) -> dict[str, Any]:
    last = float(close.iloc[-1])
    raw: list[tuple[float, str]] = []

    for name in ("5", "20", "60", "120", "250"):
        v = tech["ma"].get(name)
        if v:
            raw.append((float(v), f"MA{name}"))
    if tech["boll"]["up"]:
        raw.append((float(tech["boll"]["up"]), "布林上轨"))
    if tech["boll"]["low"]:
        raw.append((float(tech["boll"]["low"]), "布林下轨"))
    raw.append((tech["hi52"], "52周高"))
    raw.append((tech["lo52"], "52周低"))

    highs, lows = _swing_points(close)
    for h in highs[-14:]:
        raw.append((h, "前高"))
    for lo in lows[-14:]:
        raw.append((lo, "前低"))

    # 合并 1.2% 以内的价位，保留首次出现的标签
    raw.sort(key=lambda x: x[0])
    merged: list[tuple[float, str]] = []
    for price, label in raw:
        if not np.isfinite(price) or price <= 0:
            continue
        if merged and abs(price - merged[-1][0]) / merged[-1][0] < 0.012:
            continue
        merged.append((price, label))

    support = [{"price": _r(p, 2), "label": lab, "distance": _r(p / last - 1)}
               for p, lab in merged if p < last * 0.998][-3:][::-1]
    resistance = [{"price": _r(p, 2), "label": lab, "distance": _r(p / last - 1)}
                  for p, lab in merged if p > last * 1.002][:3]
    return {"support": support, "resistance": resistance}


# --------------------------------------------------------------------------- #
# 量化评分
# --------------------------------------------------------------------------- #
def score_model(tech: dict[str, Any], rel: dict[str, Any],
                close: pd.Series, bench: pd.Series,
                perf: dict[str, Any], bench_perf: dict[str, Any]) -> dict[str, Any]:
    last = tech["last"]
    ma = tech["ma"]

    # ---- 趋势 25% ----
    dev20 = tech["ma_dev"].get("20")
    s_dev20 = _interp(None if dev20 is None else dev20 * 100,
                      [-12, -4, 0, 4, 12], [8, 32, 52, 78, 96])
    slope20 = None if tech["ma20_slope"] is None else tech["ma20_slope"] * 100
    s_slope = _interp(slope20, [-8, -2, 0, 2, 8], [12, 38, 52, 76, 95])
    s_align = [0, 30, 55, 78, 96][tech["ma_bull_count"]]
    ma250, ma60 = ma.get("250"), ma.get("60")
    if ma250 and ma60:
        if last > ma250 and ma60 > ma250:
            s_long = 92
        elif last > ma250:
            s_long = 76
        elif last > ma60:
            s_long = 48
        else:
            s_long = 22
    else:
        s_long = 50
    trend = 0.30 * s_dev20 + 0.25 * s_slope + 0.25 * s_align + 0.20 * s_long

    # ---- 动量 20%（相对基准）----
    def ret_n(series: pd.Series, n: int) -> float | None:
        if len(series) > n:
            return float(series.iloc[-1] / series.iloc[-n - 1] - 1)
        return None

    r20, b20 = ret_n(close, 20), ret_n(bench, 20)
    r60, b60 = ret_n(close, 60), ret_n(bench, 60)
    ex20 = None if (r20 is None or b20 is None) else (r20 - b20) * 100
    ex60 = None if (r60 is None or b60 is None) else (r60 - b60) * 100
    s_ex20 = _interp(ex20, [-12, -3, 0, 3, 12], [10, 36, 52, 74, 94])
    s_ex60 = _interp(ex60, [-20, -5, 0, 5, 20], [10, 36, 52, 74, 94])
    rsi_v = tech["rsi14"]
    s_rsi = _interp(rsi_v, [20, 30, 45, 55, 68, 80], [30, 35, 58, 78, 72, 40])
    momentum = 0.35 * s_ex20 + 0.35 * s_ex60 + 0.30 * s_rsi

    # ---- 风险 20% ----
    vol = (perf.get("annual_vol") or 0) * 100
    s_vol = _interp(vol, [12, 20, 28, 38, 50, 70], [96, 90, 76, 58, 38, 18])
    mdd = (perf.get("max_dd") or 0) * 100
    s_mdd = _interp(mdd, [-60, -45, -32, -20, -10, 0], [18, 34, 55, 76, 90, 96])
    cur = (perf.get("current_dd") or 0) * 100
    s_cur = _interp(cur, [-45, -30, -18, -8, 0], [20, 40, 62, 82, 95])
    risk = 0.40 * s_vol + 0.35 * s_mdd + 0.25 * s_cur

    # ---- 相对强度 20% ----
    alpha = (rel.get("alpha_annual") or 0) * 100
    s_alpha = _interp(alpha, [-25, -8, 0, 8, 25], [12, 34, 52, 76, 94])
    ir = rel.get("info_ratio") or 0
    s_ir = _interp(ir, [-1.0, -0.3, 0, 0.3, 1.0], [12, 34, 52, 76, 94])
    rs = (close / close.iloc[0]) / (bench / bench.iloc[0])
    rs_slope = float(rs.iloc[-1] / rs.iloc[-61] - 1) * 100 if len(rs) > 61 else 0.0
    s_rs = _interp(rs_slope, [-15, -4, 0, 4, 15], [12, 36, 52, 74, 94])
    relative = 0.35 * s_alpha + 0.35 * s_ir + 0.30 * s_rs

    # ---- 量能 15% ----
    vr = tech.get("vol_ratio") or 1.0
    s_vr = _interp(vr, [0.4, 0.7, 1.0, 1.6, 2.6], [30, 48, 62, 82, 70])
    amt = (tech.get("amount_trend") or 0) * 100
    s_amt = _interp(amt, [-30, -10, 0, 10, 30], [30, 44, 55, 72, 88])
    amt20 = tech.get("amount_20") or 0
    s_liq = 90 if amt20 > 2e9 else 78 if amt20 > 5e8 else 62 if amt20 > 1e8 else 42 if amt20 > 2e7 else 22
    volume = 0.45 * s_vr + 0.25 * s_amt + 0.30 * s_liq

    dims = [
        {"key": "trend", "name": "趋势强度", "score": _clip(trend), "weight": 0.25,
         "reason": f"收盘价相对 MA20 {dev20*100:+.1f}%，MA20 斜率 {slope20:+.1f}%，"
                   f"多头排列 {tech['ma_bull_count']}/4"},
        {"key": "momentum", "name": "动量", "score": _clip(momentum), "weight": 0.20,
         "reason": f"20日超额 {_fmt_signed(ex20)}，60日超额 {_fmt_signed(ex60)}，"
                   f"RSI14 {rsi_v:.1f}"},
        {"key": "risk", "name": "风险控制", "score": _clip(risk), "weight": 0.20,
         "reason": f"年化波动 {vol:.1f}%，最大回撤 {mdd:.1f}%，当前回撤 {cur:.1f}%"},
        {"key": "relative", "name": "相对强度", "score": _clip(relative), "weight": 0.20,
         "reason": f"年化 Alpha {alpha:+.1f}%，信息比率 {ir:+.2f}，"
                   f"60日相对强弱 {rs_slope:+.1f}%"},
        {"key": "volume", "name": "量能", "score": _clip(volume), "weight": 0.15,
         "reason": f"量比(5/60) {vr:.2f}，20日成交额趋势 {amt:+.0f}%"},
    ]
    for d in dims:
        d["contribution"] = _r(d["score"] * d["weight"], 1)
        d["score"] = _r(d["score"], 1)

    total = sum(d["score"] * d["weight"] for d in dims)
    grade = ("A+" if total >= 85 else "A" if total >= 78 else "A-" if total >= 72 else
             "B+" if total >= 66 else "B" if total >= 60 else "B-" if total >= 54 else
             "C+" if total >= 48 else "C" if total >= 40 else "D")
    if total >= 75:
        signal, tone = "强势", "up"
    elif total >= 62:
        signal, tone = "偏多", "up"
    elif total >= 48:
        signal, tone = "中性", "flat"
    elif total >= 35:
        signal, tone = "偏空", "down"
    else:
        signal, tone = "弱势", "down"

    best = max(dims, key=lambda d: d["score"] - 50)
    worst = min(dims, key=lambda d: d["score"] - 50)
    summary = (f"综合评分 {total:.1f}（{grade}，{signal}）。"
               f"最强项为{best['name']}（{best['score']:.0f} 分），"
               f"最弱项为{worst['name']}（{worst['score']:.0f} 分）。"
               f"评分基于趋势、动量、风险、相对强度与量能五个维度加权，"
               f"仅用于横向比较，不构成买卖建议。")
    return {"total": _r(total, 1), "grade": grade, "signal": signal, "tone": tone,
            "dims": dims, "summary": summary}


def _fmt_signed(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


# --------------------------------------------------------------------------- #
# 策略回测
# --------------------------------------------------------------------------- #
@dataclass
class BacktestResult:
    equity: pd.Series
    position: pd.Series
    trades: list[dict[str, Any]]
    metrics: dict[str, Any]


def backtest_ma(close: pd.Series, fast: int = 20, slow: int = 60,
                cost: float = 0.0012, rf: float = RISK_FREE) -> BacktestResult:
    """双均线趋势跟踪：快线在慢线上方持仓，下方空仓，单边成本 cost。"""
    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean()
    raw = (fast_ma > slow_ma).astype(float)
    raw[fast_ma.isna() | slow_ma.isna()] = 0.0
    pos = raw.shift(1).fillna(0.0)
    ret = close.pct_change().fillna(0.0)
    turn = pos.diff().fillna(pos)
    strat = pos * ret - turn.abs() * cost
    equity = (1 + strat).cumprod()

    trades: list[dict[str, Any]] = []
    entry_i = None
    positions = pos.to_numpy()
    idx = close.index
    for i in range(1, len(positions)):
        if positions[i] == 1 and positions[i - 1] == 0:
            entry_i = i
        elif positions[i] == 0 and positions[i - 1] == 1 and entry_i is not None:
            entry_px = float(close.iloc[entry_i])
            exit_px = float(close.iloc[i])
            trades.append({
                "entry": str(idx[entry_i].date()), "exit": str(idx[i].date()),
                "entry_price": _r(entry_px, 2), "exit_price": _r(exit_px, 2),
                "ret": _r(exit_px / entry_px - 1), "days": int(i - entry_i),
            })
            entry_i = None

    stats = perf_stats(strat, rf)
    stats.update({
        "trades": len(trades),
        "win_rate": _r(np.mean([t["ret"] > 0 for t in trades]), 3) if trades else None,
        "avg_win": _r(np.mean([t["ret"] for t in trades if t["ret"] > 0]), 4)
        if any(t["ret"] > 0 for t in trades) else None,
        "avg_loss": _r(np.mean([t["ret"] for t in trades if t["ret"] <= 0]), 4)
        if any(t["ret"] <= 0 for t in trades) else None,
        "exposure": _r(float(pos.mean()), 3),
    })
    return BacktestResult(equity=equity, position=pos, trades=trades, metrics=stats)


def grid_scan(close: pd.Series, fasts: Sequence[int] = (5, 10, 15, 20, 30, 40),
              slows: Sequence[int] = (20, 30, 60, 90, 120, 250)) -> list[dict[str, Any]]:
    rows = []
    for fast in fasts:
        for slow in slows:
            if fast >= slow:
                continue
            res = backtest_ma(close, fast, slow)
            m = res.metrics
            rows.append({
                "fast": fast, "slow": slow,
                "annual": _r(m.get("annual_return")),
                "max_dd": _r(m.get("max_dd")),
                "sharpe": _r(m.get("sharpe"), 2),
                "trades": m.get("trades", 0),
                "cum": _r(m.get("cum_return")),
            })
    rows.sort(key=lambda r: (r["sharpe"] is None, -(r["sharpe"] or -9)))
    return rows[:5]


# --------------------------------------------------------------------------- #
# 蒙特卡洛
# --------------------------------------------------------------------------- #
def monte_carlo(ret: pd.Series, last_price: float, horizon: int = 60,
                paths: int = 3000, seed: int = 20261116) -> dict[str, Any]:
    r = ret.dropna().to_numpy()[-504:]
    if len(r) < 60 or last_price <= 0:
        return {}
    rng = np.random.default_rng(seed)
    draws = rng.choice(r, size=(paths, horizon), replace=True)
    sims = last_price * np.cumprod(1 + draws, axis=1)
    pct = np.percentile(sims, [5, 25, 50, 75, 95], axis=0)
    terminal = sims[:, -1]
    return {
        "days": horizon, "paths": paths, "last": _r(last_price, 2),
        "p5": _safe(pct[0], 2), "p25": _safe(pct[1], 2), "p50": _safe(pct[2], 2),
        "p75": _safe(pct[3], 2), "p95": _safe(pct[4], 2),
        "term_p5": _r(np.percentile(terminal, 5), 2),
        "term_p50": _r(np.percentile(terminal, 50), 2),
        "term_p95": _r(np.percentile(terminal, 95), 2),
        "prob_up": _r((terminal > last_price).mean(), 3),
        "prob_up10": _r((terminal > last_price * 1.1).mean(), 3),
        "prob_down10": _r((terminal < last_price * 0.9).mean(), 3),
        "expected": _r(terminal.mean(), 2),
        "sample": "近 504 个交易日日收益自助抽样",
    }


# --------------------------------------------------------------------------- #
# 月度矩阵
# --------------------------------------------------------------------------- #
def monthly_matrix(ret: pd.Series) -> dict[str, Any]:
    m = (1 + ret.dropna()).resample("ME").prod() - 1
    table = pd.DataFrame({"y": m.index.year, "m": m.index.month, "r": m.values})
    pivot = table.pivot(index="y", columns="m", values="r")
    years = [int(y) for y in pivot.index]
    values = []
    for y in pivot.index:
        row = []
        for mo in range(1, 13):
            v = pivot.loc[y, mo] if mo in pivot.columns else np.nan
            row.append(_r(v))
        values.append(row)
    return {"years": years, "months": list(range(1, 13)), "values": values}


# --------------------------------------------------------------------------- #
# 汇总入口
# --------------------------------------------------------------------------- #
def _thin(seq: Sequence[Any], max_points: int = 700) -> list[Any]:
    n = len(seq)
    if n <= max_points:
        return list(seq)
    idx = np.linspace(0, n - 1, max_points).round().astype(int)
    return [seq[i] for i in idx]


def analyze(df: pd.DataFrame, benchmark: pd.DataFrame, *,
            security: dict[str, str], bench_meta: dict[str, str],
            fast: int = 20, slow: int = 60,
            quote: dict[str, Any] | None = None,
            adjusted: str = "前复权") -> dict[str, Any]:
    """df / benchmark 均为日期索引、含 OHLCV 的 DataFrame。"""
    if len(df) < 60:
        raise ValueError(f"有效交易日仅 {len(df)} 天，不足以做量化分析（至少 60 天）")

    df = df.dropna(subset=["close"])
    close = df["close"].astype(float)
    ret = close.pct_change()

    bench_close = benchmark["close"].astype(float).reindex(df.index).ffill().dropna()
    bench_ret = bench_close.pct_change()
    common = ret.dropna().index.intersection(bench_ret.dropna().index)
    ret_c, bench_c = ret.loc[common], bench_ret.loc[common]

    perf = perf_stats(ret_c)
    bench_perf = perf_stats(bench_c)
    rel = relative_stats(ret_c, bench_c)
    eq_c = (1 + ret_c).cumprod()
    adv = advanced_metrics(ret_c, eq_c, perf, rel, bench_perf)
    tech = technical_snapshot(df)

    # 相对强弱（起点归一）
    rs = (1 + ret_c).cumprod() / (1 + bench_c).cumprod()

    score = score_model(tech, rel, close, bench_close, perf, bench_perf)
    lv = levels(close, tech)

    bt = backtest_ma(close, fast, slow)
    bh = close / float(close.iloc[0])
    bt_metrics = bt.metrics
    bh_metrics = perf_stats(close.pct_change())
    bh_metrics["cum_return"] = float(close.iloc[-1] / close.iloc[0] - 1)

    grid = grid_scan(close)
    mc = monte_carlo(ret_c, float(close.iloc[-1]))

    eq = (1 + ret_c).cumprod()
    dd_series = eq / eq.cummax() - 1

    # 图表序列（与基准对齐）
    chart_dates = [str(d.date()) for d in common]
    chart_close = close.reindex(common)
    ma20 = close.rolling(20).mean().reindex(common)
    ma60 = close.rolling(60).mean().reindex(common)
    volume = df["volume"].reindex(common)

    daily = {
        "dates": _thin(chart_dates),
        "close": _thin(_safe(chart_close, 3)),
        "ma20": _thin(_safe(ma20, 3)),
        "ma60": _thin(_safe(ma60, 3)),
        "volume": _thin(_safe(volume, 0)),
        "drawdown": _thin(_safe(dd_series, 5)),
        "rs": _thin(_safe(rs, 4)),
        "bench": _thin(_safe(bench_close.reindex(common), 3)),
    }

    # 回测净值序列
    bt_dates = [str(d.date()) for d in bt.equity.index]
    backtest = {
        "name": f"MA{fast} / MA{slow} 双均线趋势跟踪",
        "params": {"fast": fast, "slow": slow, "cost_bps": 12,
                   "rule": "快线上穿慢线次日满仓，下穿次日空仓"},
        "metrics": {k: _r(v) if k not in ("days", "trades", "max_dd_start",
                                          "max_dd_end", "max_dd_recover", "start", "end")
                    else v for k, v in bt_metrics.items() if k != "current_dd"},
        "bh_metrics": {k: _r(v) if k not in ("days", "max_dd_start", "max_dd_end",
                                             "max_dd_recover", "start", "end")
                       else v for k, v in bh_metrics.items() if k != "current_dd"},
        "equity": _thin(_safe(bt.equity, 4)),
        "bh_equity": _thin(_safe(bh, 4)),
        "dates": _thin(bt_dates),
        "trades_recent": bt.trades[-8:],
        "grid": grid,
    }

    mc_block = dict(mc)
    if mc:
        mc_block["dates"] = [str((df.index[-1] + timedelta(days=i)).date())
                             for i in range(mc["days"] + 1)]
        # 首日价格补到 fan 图起点
        for k in ("p5", "p25", "p50", "p75", "p95"):
            mc_block[k] = [float(close.iloc[-1])] + mc[k]
        mc_block["last_date"] = str(df.index[-1].date())

    return {
        "meta": {
            "code": security["code"], "name": security["name"],
            "symbol": security["symbol"], "market": security["market"],
            "kind": security.get("kind", "A股"),
            "benchmark": bench_meta,
            "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
            "bars": int(len(df)), "days": int(len(common)),
            "adjusted": adjusted,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "quote": quote or {},
        "periods": period_returns(close),
        "performance": {k: _r(v) if k not in ("max_dd_start", "max_dd_end",
                                              "max_dd_recover", "start", "end", "days")
                        else v for k, v in perf.items()},
        "benchmark_performance": {k: _r(v) if k not in ("max_dd_start", "max_dd_end",
                                                        "max_dd_recover", "start", "end", "days")
                                  else v for k, v in bench_perf.items()},
        "relative": {k: _r(v) for k, v in rel.items()},
        "advanced": adv,
        "technical": tech,
        "levels": lv,
        "score": score,
        "backtest": backtest,
        "monte": mc_block,
        "monthly": monthly_matrix(ret),
        "series": daily,
        "disclaimer": ("本工具基于公开行情做统计与规则化计算，所有指标、评分与模拟结果"
                       "均不构成投资建议。历史表现不代表未来收益。"),
    }
