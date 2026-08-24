import os
import sys
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

sys.path.insert(0, '/root/trade-execution-webhook')
import push_notify

router = APIRouter()
logger = logging.getLogger(__name__)


class PushKeys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: PushKeys


@router.get("/push/vapid-public-key")
async def vapid_public_key():
    return {"publicKey": os.getenv("VAPID_PUBLIC_KEY", "")}


@router.post("/push/subscribe")
async def subscribe(sub: SubscriptionIn):
    try:
        push_notify.save_subscription(sub.endpoint, sub.keys.p256dh, sub.keys.auth)
        return {"success": True}
    except Exception as e:
        logger.error(f"push subscribe failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/push/unsubscribe")
async def unsubscribe(sub: SubscriptionIn):
    try:
        push_notify.remove_subscription(sub.endpoint)
        return {"success": True}
    except Exception as e:
        logger.error(f"push unsubscribe failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.post("/push/test")
async def test_push():
    sent = push_notify.notify_all(
        "🔔 Test notification",
        "If you see this, push notifications are working.",
        url="/",
    )
    return {"success": True, "sent": sent}
