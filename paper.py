"""
模拟盘
======
本地纸面交易：现金账户、持仓、成交记录，按实时报价盯市。

  · A 股：100 股/手，佣金万 2.5（最低 5 元），卖出印花税 0.05%
  · 美股：1 股起，佣金 0
  · 账户与成交保存在 data/paper_<market>.json
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import market

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
_LOT = {"cn": 100, "us": 1}
_DEFAULT_CASH = {"cn": 100000.0, "us": 10000.0}


def _path(market_key: str) -> str:
    return os.path.join(DATA, f"paper_{market_key}.json")


def _load(market_key: str) -> dict[str, Any]:
    try:
        with open(_path(market_key), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {"cash": _DEFAULT_CASH[market_key], "positions": {}, "trades": [],
                "created": time.strftime("%Y-%m-%d %H:%M:%S")}


def _save(market_key: str, state: dict[str, Any]) -> None:
    os.makedirs(DATA, exist_ok=True)
    with open(_path(market_key), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def _fees(market_key: str, amount: float, side: str) -> float:
    if market_key == "cn":
        fee = max(5.0, amount * 0.00025) + amount * 0.00001
        if side == "sell":
            fee += amount * 0.0005
        return round(fee, 2)
    return 0.0


def get_state(market_key: str = "cn") -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    state = _load(market_key)
    positions = []
    market_value = 0.0
    for sym, pos in state.get("positions", {}).items():
        try:
            quote = market.fetch_quote(sym)
            price = float(quote.get("price") or 0)
        except market.MarketError:
            price = float(pos.get("last") or pos.get("cost") or 0)
        if price <= 0:
            price = float(pos.get("cost") or 0)
        value = price * pos["shares"]
        cost = pos["cost"] * pos["shares"]
        market_value += value
        positions.append({
            "symbol": sym, "code": pos.get("code", sym), "name": pos.get("name", sym),
            "shares": pos["shares"], "cost": round(pos["cost"], 2),
            "price": round(price, 2), "value": round(value, 2),
            "pnl": round(value - cost, 2),
            "pnl_pct": round((value / cost - 1) * 100, 2) if cost else 0,
        })
    positions.sort(key=lambda x: x["value"], reverse=True)
    cash = float(state.get("cash", 0))
    equity = cash + market_value
    return {
        "status": "ok", "market": market_key,
        "cash": round(cash, 2), "market_value": round(market_value, 2),
        "equity": round(equity, 2), "positions": positions,
        "trades": state.get("trades", [])[-20:][::-1],
        "created": state.get("created", ""),
    }


def reset(market_key: str, cash: float | None = None) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    amount = float(cash) if cash and cash > 0 else _DEFAULT_CASH[market_key]
    state = {"cash": amount, "positions": {}, "trades": [],
             "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(market_key, state)
    return get_state(market_key)


def trade(market_key: str, query: str, side: str, qty: float) -> dict[str, Any]:
    market_key = market_key if market_key in ("cn", "us") else "cn"
    side = "buy" if str(side).lower() in ("buy", "b", "买入") else "sell"
    lot = _LOT[market_key]
    shares = int(qty or 0)
    if shares <= 0 or shares % lot:
        return {"status": "error", "error": f"数量需为 {lot} 的整数倍"}
    try:
        sec = market.resolve(query)
    except market.MarketError as exc:
        return {"status": "error", "error": str(exc)}
    if market_key == "cn" and sec.market not in ("sh", "sz", "bj"):
        return {"status": "error", "error": "A 股模拟盘只能交易沪深京股票"}
    if market_key == "us" and sec.market != "us":
        return {"status": "error", "error": "美股模拟盘只能交易美股"}
    try:
        quote = market.fetch_quote(sec.symbol)
    except market.MarketError as exc:
        return {"status": "error", "error": str(exc)}
    price = float(quote.get("price") or 0)
    if price <= 0:
        return {"status": "error", "error": "报价无效"}

    state = _load(market_key)
    amount = price * shares
    fee = _fees(market_key, amount, side)
    pos = state["positions"].get(sec.symbol)

    if side == "buy":
        if state["cash"] < amount + fee:
            return {"status": "error", "error": "现金不足"}
        state["cash"] -= amount + fee
        if pos:
            total = pos["shares"] + shares
            pos["cost"] = (pos["cost"] * pos["shares"] + amount) / total
            pos["shares"] = total
        else:
            state["positions"][sec.symbol] = {
                "shares": shares, "cost": price,
                "code": sec.code, "name": sec.name,
            }
    else:
        if not pos or pos["shares"] < shares:
            return {"status": "error", "error": "持仓不足"}
        state["cash"] += amount - fee
        pos["shares"] -= shares
        if pos["shares"] <= 0:
            del state["positions"][sec.symbol]

    state["trades"].append({
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sec.symbol, "code": sec.code, "name": sec.name,
        "side": side, "shares": shares, "price": round(price, 2),
        "fee": fee, "amount": round(amount, 2),
    })
    _save(market_key, state)
    return get_state(market_key)
