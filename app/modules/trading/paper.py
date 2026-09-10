from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_hex
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.schemas.paper_trading import (
    PaperAccount,
    PaperAccountUpdate,
    PaperCashLedgerEntry,
    PaperClosePosition,
    PaperFill,
    PaperMatchingResult,
    PaperOrder,
    PaperOrderCreate,
    PaperPortfolio,
    PaperPosition,
)
from app.modules.infrastructure.database import db_connect
from app.modules.platform.service import get_config_value, set_config_value
from app.modules.market.charting import build_chart_bundle, latest_instrument_quote, resolve_instrument
from app.schemas.charting import ChartQuery


DEFAULT_BALANCES = {"USD": 100_000.0, "CNY": 1_000_000.0, "USDT": 100_000.0}
FEE_BPS = 2.0
SLIPPAGE_BPS = 1.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return sha256(f"{prefix}:{_now()}:{token_hex(6)}".encode()).hexdigest()[:20]


def ensure_schema() -> None:
    with db_connect() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS paper_accounts (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, currency TEXT NOT NULL,
          initial_cash REAL NOT NULL, cash_balance REAL NOT NULL, reserved_cash REAL NOT NULL DEFAULT 0,
          realized_pnl REAL NOT NULL DEFAULT 0, total_fees REAL NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, currency)
        );
        CREATE TABLE IF NOT EXISTS paper_orders (
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, user_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
          symbol TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL, position_side TEXT NOT NULL,
          effect TEXT NOT NULL, order_type TEXT NOT NULL, quantity REAL NOT NULL, quantity_unit TEXT NOT NULL,
          unit_label TEXT NOT NULL, unit_multiplier REAL NOT NULL, base_quantity REAL NOT NULL,
          limit_price REAL, stop_price REAL, take_profit REAL, stop_loss REAL, time_in_force TEXT NOT NULL,
          status TEXT NOT NULL, trigger_activated INTEGER NOT NULL DEFAULT 0, reserved_amount REAL NOT NULL DEFAULT 0,
          rejection_reason TEXT, parent_order_id TEXT, oco_group_id TEXT, last_evaluated_at TEXT,
          submitted_at TEXT NOT NULL, updated_at TEXT NOT NULL, filled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_paper_orders_user_status ON paper_orders(user_id,status,submitted_at DESC);
        CREATE TABLE IF NOT EXISTS paper_fills (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
          symbol TEXT NOT NULL, position_side TEXT NOT NULL, effect TEXT NOT NULL, quantity REAL NOT NULL,
          base_quantity REAL NOT NULL, price REAL NOT NULL, gross_amount REAL NOT NULL, fee REAL NOT NULL,
          realized_pnl REAL NOT NULL DEFAULT 0, source TEXT NOT NULL, executed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_paper_fills_account_time ON paper_fills(account_id,executed_at DESC);
        CREATE TABLE IF NOT EXISTS paper_positions (
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL, symbol TEXT NOT NULL,
          name TEXT NOT NULL, currency TEXT NOT NULL, side TEXT NOT NULL, quantity REAL NOT NULL,
          quantity_unit TEXT NOT NULL, unit_label TEXT NOT NULL, unit_multiplier REAL NOT NULL,
          base_quantity REAL NOT NULL, average_price REAL NOT NULL, realized_pnl REAL NOT NULL DEFAULT 0,
          take_profit REAL, stop_loss REAL, opened_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(account_id,instrument_id,side)
        );
        CREATE TABLE IF NOT EXISTS paper_cash_ledger (
          id TEXT PRIMARY KEY, account_id TEXT NOT NULL, entry_type TEXT NOT NULL, amount REAL NOT NULL,
          balance_after REAL NOT NULL, reference_id TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_migrations (key TEXT PRIMARY KEY, completed_at TEXT NOT NULL);
        """)
        now = _now()
        connection.execute("INSERT OR IGNORE INTO app_config(key,value,updated_at) VALUES('paper_matching_enabled','true',?)", (now,))
        connection.execute("INSERT OR IGNORE INTO app_config(key,value,updated_at) VALUES('paper_matching_interval','60',?)", (now,))


def matching_settings() -> tuple[bool, int]:
    ensure_schema()
    enabled = get_config_value("paper_matching_enabled", "true") == "true"
    interval = max(60, min(86400, int(get_config_value("paper_matching_interval", "60"))))
    return enabled, interval


def set_matching_settings(enabled: bool, interval: int) -> None:
    set_config_value("paper_matching_enabled", "true" if enabled else "false")
    set_config_value("paper_matching_interval", str(max(60, min(86400, interval))))


def ensure_accounts(user_id: str) -> list[PaperAccount]:
    ensure_schema()
    now = _now()
    with db_connect() as connection:
        for currency, balance in DEFAULT_BALANCES.items():
            account_id = f"paper-{user_id}-{currency.lower()}"
            created = connection.execute(
                "INSERT OR IGNORE INTO paper_accounts(id,user_id,currency,initial_cash,cash_balance,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (account_id, user_id, currency, balance, balance, now, now),
            )
            if created.rowcount:
                connection.execute("INSERT OR IGNORE INTO paper_cash_ledger VALUES(?,?,?,?,?,?,?,?)",
                    (f"initial-{account_id}",account_id,"initial_deposit",balance,balance,None,"模拟账户初始资金",now))
    migrate_legacy_positions()
    return list_accounts(user_id, create=False)


def migrate_legacy_positions() -> None:
    ensure_schema()
    with db_connect() as connection:
        if connection.execute("SELECT 1 FROM paper_migrations WHERE key='legacy-portfolio-v1'").fetchone():
            return
        owner = connection.execute("SELECT id FROM users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
        if not owner:
            return
        rows = connection.execute("SELECT * FROM portfolio_positions WHERE enabled=1 ORDER BY created_at").fetchall()
        connection.execute("INSERT OR IGNORE INTO paper_migrations(key,completed_at) VALUES('legacy-portfolio-v1',?)", (_now(),))
    if not rows:
        return
    ensure_accounts(str(owner["id"]))
    for row in rows:
        try:
            instrument = resolve_instrument(row["instrument_id"] or row["symbol"])
            account = next(item for item in list_accounts(str(owner["id"]), create=False) if item.currency == instrument.currency)
            _insert_adjustment(account, instrument, row)
        except Exception:
            continue


def _insert_adjustment(account, instrument, row) -> None:
    unit = next((item for item in instrument.quantity_units if item.code == (row["quantity_unit"] or "")), instrument.quantity_units[0])
    payload = PaperOrderCreate(account_id=account.id, instrument_id=instrument.id, position_side=row["side"], effect="open",
                               order_type="market", quantity=float(row["quantity"]), quantity_unit=unit.code)
    order = _create_order_record(account.user_id, payload, instrument, unit, status="pending", reserve=False,
                                 submitted_at=row["created_at"])
    _fill_order(order.id, float(row["average_price"]), "历史持仓迁移", apply_slippage=False, bypass_cash=True)


def list_accounts(user_id: str, create: bool = True) -> list[PaperAccount]:
    if create:
        ensure_accounts(user_id)
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM paper_accounts WHERE user_id=? ORDER BY CASE currency WHEN 'USD' THEN 1 WHEN 'CNY' THEN 2 ELSE 3 END", (user_id,)).fetchall()
    return [_account_model(row, live=False) for row in rows]


def _account_model(row, positions: list[PaperPosition] | None = None, live: bool = True) -> PaperAccount:
    if positions is None and live:
        positions = _position_rows(row["id"])
    if positions is None:
        with db_connect() as connection:
            stored = connection.execute("SELECT side,base_quantity,average_price FROM paper_positions WHERE account_id=?", (row["id"],)).fetchall()
        market_value = sum(float(item["base_quantity"]) * float(item["average_price"]) for item in stored)
        unrealized = 0.0
        equity_position_value = market_value
    else:
        market_value = sum(abs(item.market_value) for item in positions)
        unrealized = sum(item.unrealized_pnl for item in positions)
        equity_position_value = sum(item.market_value if item.side == "long" else item.cost_basis + item.unrealized_pnl for item in positions)
    equity = float(row["cash_balance"]) + equity_position_value
    available = float(row["cash_balance"]) - float(row["reserved_cash"])
    return PaperAccount(**dict(row), available_cash=round(available, 2), market_value=round(market_value, 2),
                        equity=round(equity, 2), unrealized_pnl=round(unrealized, 2),
                        utilization_percent=round(market_value / max(abs(equity), 1) * 100, 2))


def update_account(user_id: str, account_id: str, payload: PaperAccountUpdate) -> PaperAccount:
    account = _owned_account(user_id, account_id)
    if _position_rows(account_id) or list_orders(user_id, account_id, status="pending"):
        raise HTTPException(409, "账户存在持仓或委托，不能重置初始资金")
    now = _now()
    with db_connect() as connection:
        connection.execute("UPDATE paper_accounts SET initial_cash=?,cash_balance=?,reserved_cash=0,realized_pnl=0,total_fees=0,updated_at=? WHERE id=?",
                           (payload.initial_cash, payload.initial_cash, now, account_id))
        connection.execute("INSERT INTO paper_cash_ledger VALUES(?,?,?,?,?,?,?,?)",
            (_id("ledger"),account_id,"account_reset",payload.initial_cash,payload.initial_cash,None,"重置模拟账户初始资金",now))
    return _owned_account(user_id, account_id)


def _owned_account(user_id: str, account_id: str) -> PaperAccount:
    ensure_schema()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM paper_accounts WHERE id=? AND user_id=?", (account_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "模拟账户不存在")
    return _account_model(row, live=False)


def _unit(instrument, code: str):
    if not instrument.quantity_units:
        raise HTTPException(422, "该标的不支持模拟交易")
    unit = next((item for item in instrument.quantity_units if item.code == code), None) if code else instrument.quantity_units[0]
    if not unit:
        raise HTTPException(422, "数量单位不适用于该标的")
    return unit


def create_order(user_id: str, payload: PaperOrderCreate) -> PaperOrder:
    account = _owned_account(user_id, payload.account_id)
    instrument = resolve_instrument(payload.instrument_id)
    if not instrument.positionable or instrument.asset_class in {"index", "crypto_perpetual"}:
        raise HTTPException(422, "首期仅支持股票、ETF 和加密现货模拟交易")
    if instrument.currency != account.currency:
        raise HTTPException(422, f"请选择 {instrument.currency} 模拟账户")
    unit = _unit(instrument, payload.quantity_unit)
    if payload.effect == "close":
        position = _find_position(account.id, instrument.id, payload.position_side)
        if not position or payload.quantity * unit.multiplier > float(position["base_quantity"]) + 1e-9:
            raise HTTPException(422, "平仓数量超过当前持仓")
    quote = latest_instrument_quote(instrument.id)
    if quote.stale:
        raise HTTPException(409, "行情已过期，无法提交模拟委托")
    reserve_price = payload.limit_price or payload.stop_price or quote.price
    reserve = payload.quantity * unit.multiplier * reserve_price * (1 + FEE_BPS / 10000)
    if payload.effect == "open" and account.available_cash + 1e-9 < reserve:
        raise HTTPException(422, "可用资金不足")
    order = _create_order_record(user_id, payload, instrument, unit, status="pending", reserve=payload.effect == "open",
                                 reserve_amount=reserve if payload.effect == "open" else 0)
    if payload.order_type == "market":
        adverse = 1 + (SLIPPAGE_BPS / 10000) * (1 if payload.position_side == "long" and payload.effect == "open" or payload.position_side == "short" and payload.effect == "close" else -1)
        return _fill_order(order.id, quote.price * adverse, quote.source, apply_slippage=False)
    return order


def _create_order_record(user_id, payload, instrument, unit, status="pending", reserve=True, reserve_amount=0, submitted_at=None):
    now = submitted_at or _now()
    order_id = _id("paper-order")
    base_quantity = payload.quantity * unit.multiplier
    with db_connect() as connection:
        connection.execute("""INSERT INTO paper_orders(id,account_id,user_id,instrument_id,symbol,name,currency,position_side,effect,order_type,
          quantity,quantity_unit,unit_label,unit_multiplier,base_quantity,limit_price,stop_price,take_profit,stop_loss,time_in_force,status,
          reserved_amount,submitted_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (order_id,payload.account_id,user_id,instrument.id,instrument.symbol,instrument.name,instrument.currency,payload.position_side,payload.effect,
           payload.order_type,payload.quantity,unit.code,unit.label,unit.multiplier,base_quantity,payload.limit_price,payload.stop_price,
           payload.take_profit,payload.stop_loss,payload.time_in_force,status,reserve_amount,now,now))
        if reserve and reserve_amount:
            connection.execute("UPDATE paper_accounts SET reserved_cash=reserved_cash+?,updated_at=? WHERE id=?", (reserve_amount,_now(),payload.account_id))
    return get_order(user_id, order_id)


def _order_model(row) -> PaperOrder:
    values = dict(row)
    values["trigger_activated"] = bool(values["trigger_activated"])
    return PaperOrder(**values)


def get_order(user_id: str, order_id: str) -> PaperOrder:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM paper_orders WHERE id=? AND user_id=?", (order_id,user_id)).fetchone()
    if not row:
        raise HTTPException(404, "模拟委托不存在")
    return _order_model(row)


def list_orders(user_id: str, account_id: str | None = None, status: str | None = None, instrument_id: str | None = None) -> list[PaperOrder]:
    ensure_schema()
    clauses, params = ["user_id=?"], [user_id]
    if account_id: clauses.append("account_id=?"); params.append(account_id)
    if status: clauses.append("status=?"); params.append(status)
    if instrument_id: clauses.append("instrument_id=?"); params.append(instrument_id)
    with db_connect() as connection:
        rows = connection.execute(f"SELECT * FROM paper_orders WHERE {' AND '.join(clauses)} ORDER BY submitted_at DESC LIMIT 500", params).fetchall()
    return [_order_model(row) for row in rows]


def cancel_order(user_id: str, order_id: str) -> PaperOrder:
    order = get_order(user_id, order_id)
    if order.status not in {"pending", "triggered"}:
        raise HTTPException(409, "只有待成交委托可以撤销")
    with db_connect() as connection:
        connection.execute("UPDATE paper_orders SET status='canceled',updated_at=? WHERE id=?", (_now(),order.id))
        connection.execute("UPDATE paper_accounts SET reserved_cash=MAX(0,reserved_cash-?),updated_at=? WHERE id=?", (order.reserved_amount,_now(),order.account_id))
    return get_order(user_id, order_id)


def _find_position(account_id, instrument_id, side):
    with db_connect() as connection:
        return connection.execute("SELECT * FROM paper_positions WHERE account_id=? AND instrument_id=? AND side=?", (account_id,instrument_id,side)).fetchone()


def _fill_order(order_id: str, price: float, source: str, apply_slippage=False, bypass_cash=False) -> PaperOrder:
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone()
        if not row or row["status"] not in {"pending", "triggered"}: return _order_model(row)
        gross = float(row["base_quantity"]) * price
        fee = gross * FEE_BPS / 10000
        position = connection.execute("SELECT * FROM paper_positions WHERE account_id=? AND instrument_id=? AND side=?", (row["account_id"],row["instrument_id"],row["position_side"])).fetchone()
        realized = 0.0
        cash_delta = 0.0
        remaining = None
        if row["effect"] == "open":
            if not bypass_cash:
                account = connection.execute("SELECT cash_balance FROM paper_accounts WHERE id=?", (row["account_id"],)).fetchone()
                if float(account["cash_balance"]) + 1e-9 < gross + fee:
                    connection.execute("UPDATE paper_orders SET status='rejected',rejection_reason='成交时资金不足',updated_at=? WHERE id=?", (_now(),order_id))
                    connection.execute("UPDATE paper_accounts SET reserved_cash=MAX(0,reserved_cash-?) WHERE id=?", (row["reserved_amount"],row["account_id"]))
                    connection.commit()
                    return get_order(row["user_id"], order_id)
            next_base = float(row["base_quantity"]) + (float(position["base_quantity"]) if position else 0)
            next_avg = ((float(position["base_quantity"]) * float(position["average_price"]) if position else 0) + gross) / next_base
            if position:
                display_qty = next_base / float(row["unit_multiplier"])
                connection.execute("UPDATE paper_positions SET quantity=?,base_quantity=?,average_price=?,take_profit=COALESCE(?,take_profit),stop_loss=COALESCE(?,stop_loss),updated_at=? WHERE id=?",
                                   (display_qty,next_base,next_avg,row["take_profit"],row["stop_loss"],_now(),position["id"]))
            else:
                connection.execute("""INSERT INTO paper_positions(id,account_id,instrument_id,symbol,name,currency,side,quantity,quantity_unit,unit_label,
                  unit_multiplier,base_quantity,average_price,take_profit,stop_loss,opened_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (_id("paper-position"),row["account_id"],row["instrument_id"],row["symbol"],row["name"],row["currency"],row["position_side"],
                   row["quantity"],row["quantity_unit"],row["unit_label"],row["unit_multiplier"],row["base_quantity"],price,row["take_profit"],row["stop_loss"],row["submitted_at"],_now()))
            cash_delta = -(gross + fee) if row["position_side"] == "long" else -(gross + fee)
        else:
            if not position or float(position["base_quantity"]) + 1e-9 < float(row["base_quantity"]):
                connection.execute("UPDATE paper_orders SET status='rejected',rejection_reason='平仓数量超过当前持仓',updated_at=? WHERE id=?", (_now(),order_id))
                connection.commit()
                return get_order(row["user_id"], order_id)
            direction = 1 if row["position_side"] == "long" else -1
            realized = (price - float(position["average_price"])) * float(row["base_quantity"]) * direction - fee
            remaining = float(position["base_quantity"]) - float(row["base_quantity"])
            if remaining <= 1e-9:
                connection.execute("DELETE FROM paper_positions WHERE id=?", (position["id"],))
            else:
                connection.execute("UPDATE paper_positions SET quantity=?,base_quantity=?,realized_pnl=realized_pnl+?,updated_at=? WHERE id=?",
                                   (remaining/float(position["unit_multiplier"]),remaining,realized,_now(),position["id"]))
            cash_delta = gross - fee if row["position_side"] == "long" else gross + realized
        now = _now(); fill_id = _id("paper-fill")
        connection.execute("INSERT INTO paper_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (fill_id,row["id"],row["account_id"],row["instrument_id"],row["symbol"],row["position_side"],row["effect"],row["quantity"],row["base_quantity"],price,gross,fee,realized,source,now))
        connection.execute("UPDATE paper_orders SET status='filled',filled_at=?,updated_at=? WHERE id=?", (now,now,row["id"]))
        connection.execute("UPDATE paper_accounts SET cash_balance=cash_balance+?,reserved_cash=MAX(0,reserved_cash-?),realized_pnl=realized_pnl+?,total_fees=total_fees+?,updated_at=? WHERE id=?",
                           (cash_delta,row["reserved_amount"],realized,fee,now,row["account_id"]))
        balance = connection.execute("SELECT cash_balance FROM paper_accounts WHERE id=?", (row["account_id"],)).fetchone()["cash_balance"]
        connection.execute("INSERT INTO paper_cash_ledger VALUES(?,?,?,?,?,?,?,?)", (_id("ledger"),row["account_id"],"fill",cash_delta,balance,row["id"],f"{row['symbol']} 模拟成交",now))
        if row["effect"] == "close":
            bracket_rows = connection.execute("""SELECT id,unit_multiplier FROM paper_orders
              WHERE account_id=? AND instrument_id=? AND position_side=? AND effect='close'
                AND parent_order_id IS NOT NULL AND status IN ('pending','triggered')""",
              (row["account_id"],row["instrument_id"],row["position_side"])).fetchall()
            for bracket in bracket_rows:
                if not remaining or remaining <= 1e-9:
                    connection.execute("UPDATE paper_orders SET status='canceled',updated_at=? WHERE id=?", (now,bracket["id"]))
                else:
                    connection.execute("UPDATE paper_orders SET base_quantity=?,quantity=?,updated_at=? WHERE id=?",
                                       (remaining,remaining/float(bracket["unit_multiplier"]),now,bracket["id"]))
        if row["oco_group_id"]:
            siblings = connection.execute("SELECT id,reserved_amount FROM paper_orders WHERE oco_group_id=? AND id<>? AND status IN ('pending','triggered')", (row["oco_group_id"],row["id"])).fetchall()
            for sibling in siblings:
                connection.execute("UPDATE paper_orders SET status='canceled',updated_at=? WHERE id=?", (now,sibling["id"]))
        # Brackets are close orders and do not reserve additional cash.
        if row["effect"] == "open" and (row["take_profit"] or row["stop_loss"]):
            oco = _id("oco")
            for kind, value in (("limit",row["take_profit"]),("stop",row["stop_loss"])):
                if not value: continue
                child_id = _id("paper-order")
                connection.execute("""INSERT INTO paper_orders(id,account_id,user_id,instrument_id,symbol,name,currency,position_side,effect,order_type,
                 quantity,quantity_unit,unit_label,unit_multiplier,base_quantity,limit_price,stop_price,time_in_force,status,reserved_amount,parent_order_id,oco_group_id,submitted_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (child_id,row["account_id"],row["user_id"],row["instrument_id"],row["symbol"],row["name"],row["currency"],row["position_side"],"close",kind,
                  row["quantity"],row["quantity_unit"],row["unit_label"],row["unit_multiplier"],row["base_quantity"],value if kind=="limit" else None,
                  value if kind=="stop" else None,"gtc","pending",0,row["id"],oco,now,now))
    return get_order(row["user_id"], order_id)


def _position_rows(account_id: str) -> list[PaperPosition]:
    ensure_schema()
    with db_connect() as connection:
        rows = connection.execute("SELECT * FROM paper_positions WHERE account_id=? ORDER BY updated_at DESC", (account_id,)).fetchall()
    def load_quote(row):
        try:
            return latest_instrument_quote(row["instrument_id"])
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(rows)))) as executor:
        quotes = list(executor.map(load_quote, rows))
    result = []
    for row, quote in zip(rows, quotes):
        price = quote.price if quote else None
        quote_as_of = quote.as_of if quote else None
        quote_source = quote.source if quote else None
        stale = quote.stale if quote else True
        mark = price if price is not None else float(row["average_price"])
        direction = 1 if row["side"] == "long" else -1
        pnl = (mark-float(row["average_price"]))*float(row["base_quantity"])*direction
        result.append(PaperPosition(**dict(row), current_price=price, market_value=round(mark*float(row["base_quantity"]),2),
          cost_basis=round(float(row["average_price"])*float(row["base_quantity"]),2), unrealized_pnl=round(pnl,2),
          unrealized_pnl_percent=round(direction*(mark/float(row["average_price"])-1)*100,2), quote_as_of=quote_as_of,
          quote_source=quote_source, stale_price=stale))
    return result


def get_portfolio(user_id: str, account_id: str) -> PaperPortfolio:
    _owned_account(user_id, account_id)
    positions = _position_rows(account_id)
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM paper_accounts WHERE id=? AND user_id=?", (account_id, user_id)).fetchone()
    account = _account_model(row, positions=positions)
    return PaperPortfolio(account=account, positions=positions, pending_orders=len(list_orders(user_id,account_id,"pending")), generated_at=_now())


def list_fills(user_id: str, account_id: str | None=None, instrument_id: str | None=None) -> list[PaperFill]:
    clauses, params = ["a.user_id=?"], [user_id]
    if account_id: clauses.append("f.account_id=?"); params.append(account_id)
    if instrument_id: clauses.append("f.instrument_id=?"); params.append(instrument_id)
    with db_connect() as connection:
        rows=connection.execute(f"""SELECT f.*,o.quantity_unit,o.unit_label FROM paper_fills f
          JOIN paper_accounts a ON a.id=f.account_id JOIN paper_orders o ON o.id=f.order_id
          WHERE {' AND '.join(clauses)} ORDER BY f.executed_at DESC LIMIT 500""",params).fetchall()
    return [PaperFill(**dict(row)) for row in rows]


def list_ledger(user_id: str, account_id: str) -> list[PaperCashLedgerEntry]:
    _owned_account(user_id,account_id)
    with db_connect() as connection:
        rows=connection.execute("SELECT * FROM paper_cash_ledger WHERE account_id=? ORDER BY created_at DESC LIMIT 500",(account_id,)).fetchall()
    return [PaperCashLedgerEntry(**dict(row)) for row in rows]


def close_position(user_id: str, position_id: str, payload: PaperClosePosition) -> PaperOrder:
    with db_connect() as connection:
        row=connection.execute("SELECT p.* FROM paper_positions p JOIN paper_accounts a ON a.id=p.account_id WHERE p.id=? AND a.user_id=?",(position_id,user_id)).fetchone()
    if not row: raise HTTPException(404,"模拟持仓不存在")
    quantity = payload.quantity or float(row["quantity"])
    return create_order(user_id,PaperOrderCreate(account_id=row["account_id"],instrument_id=row["instrument_id"],position_side=row["side"],effect="close",
      order_type=payload.order_type,quantity=quantity,quantity_unit=payload.quantity_unit or row["quantity_unit"],limit_price=payload.limit_price,
      stop_price=payload.stop_price,time_in_force=payload.time_in_force))


def run_matching(user_id: str | None=None) -> PaperMatchingResult:
    ensure_schema(); errors=[]; filled=expired=0
    clauses="status IN ('pending','triggered')"; params=[]
    if user_id: clauses+=" AND user_id=?"; params.append(user_id)
    with db_connect() as connection:
        rows=connection.execute(f"SELECT * FROM paper_orders WHERE {clauses} ORDER BY submitted_at",params).fetchall()
    for row in rows:
        try:
            instrument=resolve_instrument(row["instrument_id"])
            submitted_at = datetime.fromisoformat(row["submitted_at"])
            if row["time_in_force"] == "day":
                zone = ZoneInfo(instrument.timezone or "UTC")
                if datetime.now(timezone.utc).astimezone(zone).date() > submitted_at.astimezone(zone).date():
                    with db_connect() as connection:
                        connection.execute("UPDATE paper_orders SET status='expired',updated_at=? WHERE id=?", (_now(),row["id"]))
                        connection.execute("UPDATE paper_accounts SET reserved_cash=MAX(0,reserved_cash-?),updated_at=? WHERE id=?", (row["reserved_amount"],_now(),row["account_id"]))
                    expired += 1
                    continue
            interval="1m" if "1m" in instrument.intervals else "15m" if "15m" in instrument.intervals else instrument.intervals[0]
            bundle=build_chart_bundle(ChartQuery(instrument_id=instrument.id,interval=interval,range="1D",indicators=[],news_mode="off",include_series=False,limit=100))
            submitted=submitted_at.timestamp()
            candidates=[c for c in bundle.candles if c.time>=submitted]
            price=None
            is_buy = (row["position_side"] == "long" and row["effect"] == "open") or (row["position_side"] == "short" and row["effect"] == "close")
            for candle in candidates:
                if row["order_type"]=="limit" and ((is_buy and candle.low <= row["limit_price"]) or (not is_buy and candle.high >= row["limit_price"])):
                    price = min(row["limit_price"], candle.open) if is_buy else max(row["limit_price"], candle.open)
                elif row["order_type"]=="stop" and ((is_buy and candle.high >= row["stop_price"]) or (not is_buy and candle.low <= row["stop_price"])):
                    price = max(row["stop_price"], candle.open) if is_buy else min(row["stop_price"], candle.open)
                elif row["order_type"]=="stop_limit" and candle.low<=row["stop_price"]<=candle.high and candle.low<=row["limit_price"]<=candle.high:
                    price=row["limit_price"]
                if price: break
            if price:
                _fill_order(row["id"],float(price),bundle.status.provider_label or bundle.status.provider); filled+=1
        except Exception as exc: errors.append(f"{row['symbol']}: {exc}")
    return PaperMatchingResult(checked=len(rows),filled=filled,expired=expired,errors=errors,ran_at=_now())


async def matching_loop() -> None:
    while True:
        enabled, interval = matching_settings()
        try:
            if enabled: await asyncio.to_thread(run_matching)
        except Exception: pass
        await asyncio.sleep(interval)
