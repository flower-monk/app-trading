from __future__ import annotations

from fastapi import APIRouter, Request

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
)
from app.modules.trading import paper as service
from app.modules.platform.service import current_request_user


router = APIRouter(prefix="/api/paper", tags=["Paper trading"])


@router.get("/accounts", response_model=list[PaperAccount])
def paper_accounts(request: Request): return service.ensure_accounts(current_request_user(request).id)


@router.patch("/accounts/{account_id}", response_model=PaperAccount)
def patch_account(account_id: str, payload: PaperAccountUpdate, request: Request):
    return service.update_account(current_request_user(request).id, account_id, payload)


@router.get("/portfolio", response_model=PaperPortfolio)
def paper_portfolio(account_id: str, request: Request): return service.get_portfolio(current_request_user(request).id, account_id)


@router.get("/orders", response_model=list[PaperOrder])
def paper_orders(request: Request, account_id: str | None=None, status: str | None=None, instrument_id: str | None=None):
    return service.list_orders(current_request_user(request).id, account_id, status, instrument_id)


@router.post("/orders", response_model=PaperOrder)
def create_order(payload: PaperOrderCreate, request: Request): return service.create_order(current_request_user(request).id, payload)


@router.post("/orders/{order_id}/cancel", response_model=PaperOrder)
def cancel_order(order_id: str, request: Request): return service.cancel_order(current_request_user(request).id, order_id)


@router.post("/positions/{position_id}/close", response_model=PaperOrder)
def close_position(position_id: str, payload: PaperClosePosition, request: Request):
    return service.close_position(current_request_user(request).id, position_id, payload)


@router.get("/fills", response_model=list[PaperFill])
def paper_fills(request: Request, account_id: str | None=None, instrument_id: str | None=None):
    return service.list_fills(current_request_user(request).id, account_id, instrument_id)


@router.get("/cash-ledger", response_model=list[PaperCashLedgerEntry])
def cash_ledger(account_id: str, request: Request): return service.list_ledger(current_request_user(request).id, account_id)


@router.post("/matching/run", response_model=PaperMatchingResult)
def run_matching(request: Request): return service.run_matching(current_request_user(request).id)
