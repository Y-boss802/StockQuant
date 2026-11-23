"""交易时段判断：预测只在收盘后生成。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _prev_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def state(market_key: str) -> dict[str, str]:
    """返回 state（open / closed_today / closed_prev）与基准交易日。"""
    if market_key == "us":
        now = datetime.now(ZoneInfo("America/New_York"))
        close_label = "16:00 ET"
        open_t, close_t = time(9, 30), time(16, 0)
    else:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        close_label = "15:00"
        open_t, close_t = time(9, 15), time(15, 0)

    if now.weekday() >= 5:
        return {"state": "closed_prev", "session_date": _prev_weekday(now.date()).isoformat(),
                "now": now.strftime("%Y-%m-%d %H:%M"), "close_label": close_label}
    today = now.date()
    if now.time() < open_t:
        return {"state": "closed_prev", "session_date": _prev_weekday(today).isoformat(),
                "now": now.strftime("%Y-%m-%d %H:%M"), "close_label": close_label}
    if now.time() < close_t:
        return {"state": "open", "session_date": _prev_weekday(today).isoformat(),
                "now": now.strftime("%Y-%m-%d %H:%M"), "close_label": close_label}
    return {"state": "closed_today", "session_date": today.isoformat(),
            "now": now.strftime("%Y-%m-%d %H:%M"), "close_label": close_label}
