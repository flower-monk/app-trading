from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


PaperCurrency = Literal["USD", "CNY", "USDT"]
PaperPositionSide = Literal["long", "short"]
PaperOrderEffect = Literal["open", "close"]
PaperOrderType = Literal["market", "limit", "stop", "stop_limit"]
PaperOrderStatus = Literal["pending", "triggered", "filled", "canceled", "rejected", "expired"]


class PaperAccount(BaseModel):
    id: str
    user_id: str
    currency: PaperCurrency
    initial_cash: float
    cash_balance: float
    reserved_cash: float
    available_cash: float
    market_value: float = 0
    equity: float = 0
    unrealized_pnl: float = 0
    realized_pnl: float = 0
    total_fees: float = 0
    utilization_percent: float = 0
    created_at: str
    updated_at: str


class PaperAccountUpdate(BaseModel):
    initial_cash: float = Field(gt=0, le=1_000_000_000)


class PaperOrderCreate(BaseModel):
    account_id: str
    instrument_id: str
    position_side: PaperPositionSide = "long"
    effect: PaperOrderEffect = "open"
    order_type: PaperOrderType = "market"
    quantity: float = Field(gt=0)
    quantity_unit: str = ""
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    time_in_force: Literal["day", "gtc"] = "day"

    @model_validator(mode="after")
    def validate_prices(self):
        if self.order_type in {"limit", "stop_limit"} and self.limit_price is None:
            raise ValueError("限价单必须填写限价")
        if self.order_type in {"stop", "stop_limit"} and self.stop_price is None:
            raise ValueError("止损或止损限价单必须填写触发价")
        return self


class PaperOrder(BaseModel):
    id: str
    account_id: str
    user_id: str
    instrument_id: str
    symbol: str
    name: str
    currency: str
    position_side: PaperPositionSide
    effect: PaperOrderEffect
    order_type: PaperOrderType
    quantity: float
    quantity_unit: str
    unit_label: str
    unit_multiplier: float
    base_quantity: float
    limit_price: float | None
    stop_price: float | None
    take_profit: float | None
    stop_loss: float | None
    time_in_force: Literal["day", "gtc"]
    status: PaperOrderStatus
    trigger_activated: bool
    reserved_amount: float
    rejection_reason: str | None
    parent_order_id: str | None
    oco_group_id: str | None
    submitted_at: str
    updated_at: str
    filled_at: str | None


class PaperFill(BaseModel):
    id: str
    order_id: str
    account_id: str
    instrument_id: str
    symbol: str
    position_side: PaperPositionSide
    effect: PaperOrderEffect
    quantity: float
    quantity_unit: str
    unit_label: str
    base_quantity: float
    price: float
    gross_amount: float
    fee: float
    realized_pnl: float
    source: str
    simulated: bool = True
    executed_at: str


class PaperPosition(BaseModel):
    id: str
    account_id: str
    instrument_id: str
    symbol: str
    name: str
    currency: str
    side: PaperPositionSide
    quantity: float
    quantity_unit: str
    unit_label: str
    unit_multiplier: float
    base_quantity: float
    average_price: float
    current_price: float | None
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    realized_pnl: float
    take_profit: float | None = None
    stop_loss: float | None = None
    quote_as_of: str | None = None
    quote_source: str | None = None
    stale_price: bool = False
    opened_at: str
    updated_at: str


class PaperPortfolio(BaseModel):
    account: PaperAccount
    positions: list[PaperPosition]
    pending_orders: int
    generated_at: str


class PaperCashLedgerEntry(BaseModel):
    id: str
    account_id: str
    entry_type: str
    amount: float
    balance_after: float
    reference_id: str | None
    detail: str
    created_at: str


class PaperClosePosition(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    quantity_unit: str | None = None
    order_type: PaperOrderType = "market"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    time_in_force: Literal["day", "gtc"] = "day"


class PaperMatchingResult(BaseModel):
    checked: int
    filled: int
    expired: int
    errors: list[str] = Field(default_factory=list)
    ran_at: str
