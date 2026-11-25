"""Currency conversion HTTP boundary."""
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..auth.dependencies import guest_access
from .client import CurrencyClient, CurrencyConfigError, CurrencyRequestError, TargetCurrency

router = APIRouter()


@router.get("/api/currency/convert", dependencies=[Depends(guest_access("currency", 30))])
def convert_currency(request: Request, to: TargetCurrency, amount: Annotated[float, Query(ge=0, allow_inf_nan=False)] = 1):
    try:
        with CurrencyClient(store=request.app.state.database) as currency:
            return currency.convert_jpy(amount, to)
    except CurrencyConfigError as exc:
        raise HTTPException(503, detail={"type": type(exc).__name__, "message": str(exc)}) from exc
    except CurrencyRequestError as exc:
        timeout = isinstance(exc.__cause__, httpx.TimeoutException)
        raise HTTPException(504 if timeout else 502, detail={
            "type": type(exc).__name__,
            "message": "The currency provider timed out." if timeout else "Could not retrieve the currency conversion.",
        }) from exc


