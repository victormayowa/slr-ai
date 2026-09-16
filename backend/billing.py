"""Payments behind one interface, so the payment provider can be chosen (or changed) without touching the rest of the
app. BILLING_PROVIDER selects the adapter:

- "manual": an administrator assigns plans (for invoiced institutions). No checkout; the default.
- "dev": a simulated checkout for development. Completing it sends a signed webhook through the same processing path
  a real provider's would, so plan changes, idempotency, and failed payments can be tested end to end. Refused when
  APP_ENV=production.
- "stripe": Stripe Checkout, the customer portal, and signed webhooks (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET). Each
  paid plan links its Stripe prices in Plan.provider_prices, e.g. {"stripe": {"month": "price_...", "year": "..."}}.

Providers report changes as webhooks. Each is stored once in billing_events (unique per provider and event id), so a
redelivered event is recognized and never applied twice.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import requests
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
import notifications
from entitlements import Account, account_label, subscription_for

logger = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_TOLERANCE_SECONDS = 300
DEV_SESSION_SECONDS = 3600
INTERVALS = ("month", "year")


class BillingError(Exception):
    """Billing can't do what was asked. The message is safe to show users."""


@dataclass
class CheckoutRequest:
    account: Account
    plan: models.Plan
    interval: str
    email: str
    success_url: str
    cancel_url: str
    existing: models.Subscription | None = None


@dataclass
class ProviderEvent:
    """A provider's webhook, normalized. `type` is one of: subscription.updated, subscription.deleted,
    checkout.completed, payment.failed, or ignored."""

    event_id: str
    type: str
    original_type: str
    account: Account | None = None
    plan_code: str = ""
    price_id: str = ""
    status: str = ""
    interval: str = ""
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    customer_id: str = ""
    subscription_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class BillingProvider(Protocol):
    id: str
    label: str

    def configured(self) -> bool: ...

    def checkout(self, request: CheckoutRequest) -> str: ...

    def portal(self, subscription: models.Subscription, return_url: str) -> str: ...

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent: ...

    def cancel(self, subscription: models.Subscription, at_period_end: bool) -> None: ...


def app_url(path: str = "") -> str:
    return notifications.app_url(path)


# --- Manual ---


class ManualProvider:
    id = "manual"
    label = "Invoiced by arrangement"

    def configured(self) -> bool:
        return True

    def checkout(self, request: CheckoutRequest) -> str:
        raise BillingError("Plans on this server are arranged directly with us. Contact support to change plan.")

    def portal(self, subscription: models.Subscription, return_url: str) -> str:
        raise BillingError("Billing on this server is handled by invoice. Contact support about payments.")

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        raise BillingError("This server doesn't accept billing webhooks")

    def cancel(self, subscription: models.Subscription, at_period_end: bool) -> None:
        return None


# --- Development ---


def _dev_key() -> bytes:
    return hashlib.sha256(("omnireview-dev-billing:" + os.getenv("JWT_SECRET_KEY", "")).encode()).digest()


def _sign(payload: bytes) -> str:
    return hmac.new(_dev_key(), payload, hashlib.sha256).hexdigest()


class DevProvider:
    id = "dev"
    label = "Simulated payments (development)"

    def configured(self) -> bool:
        return os.getenv("APP_ENV", "development") != "production"

    def checkout(self, request: CheckoutRequest) -> str:
        session = {
            "account": request.account.key,
            "plan": request.plan.code,
            "interval": request.interval,
            "expires": int(time.time()) + DEV_SESSION_SECONDS,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(session, sort_keys=True).encode()).decode()
        return app_url(f"/billing/dev-checkout?session={encoded}.{_sign(encoded.encode())}")

    def portal(self, subscription: models.Subscription, return_url: str) -> str:
        return return_url

    def read_session(self, token: str) -> dict[str, Any]:
        encoded, _, signature = token.partition(".")
        if not signature or not hmac.compare_digest(_sign(encoded.encode()), signature):
            raise BillingError("That checkout session isn't valid")
        session = json.loads(base64.urlsafe_b64decode(encoded.encode()))
        if session["expires"] < time.time():
            raise BillingError("That checkout session has expired; start again from Billing")
        return session

    def completion_webhook(self, token: str, outcome: str) -> tuple[bytes, dict[str, str]]:
        """The signed webhook a real provider would send when this checkout is paid (or its payment fails)."""
        session = self.read_session(token)
        account = session["account"].replace(":", "_")
        period_days = 366 if session["interval"] == "year" else 31
        event = {
            "id": f"evt_dev_{secrets.token_hex(8)}",
            "type": "subscription.updated" if outcome == "paid" else "payment.failed",
            "account": session["account"],
            "plan": session["plan"],
            "interval": session["interval"],
            "status": "active" if outcome == "paid" else "past_due",
            "current_period_end": (models.utcnow() + timedelta(days=period_days)).isoformat(),
            "customer": f"cus_dev_{account}",
            "subscription": f"sub_dev_{account}",
        }
        body = json.dumps(event).encode()
        return body, {"x-dev-signature": _sign(body)}

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        signature = headers.get("x-dev-signature", "")
        if not signature or not hmac.compare_digest(_sign(body), signature):
            raise BillingError("The webhook signature isn't valid")
        event = json.loads(body)
        return ProviderEvent(
            event_id=event["id"],
            type=event["type"],
            original_type=event["type"],
            account=Account.parse(event.get("account", "")),
            plan_code=event.get("plan", ""),
            status=event.get("status", ""),
            interval=event.get("interval", ""),
            current_period_end=datetime.fromisoformat(event["current_period_end"])
            if event.get("current_period_end")
            else None,
            customer_id=event.get("customer", ""),
            subscription_id=event.get("subscription", ""),
            payload=event,
        )

    def cancel(self, subscription: models.Subscription, at_period_end: bool) -> None:
        return None


# --- Stripe ---

_STRIPE_STATUS = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "unpaid": "past_due",
    "incomplete": "incomplete",
    "incomplete_expired": "canceled",
    "canceled": "canceled",
    "paused": "past_due",
}


def verify_stripe_signature(body: bytes, header: str, secret: str, now: float | None = None) -> None:
    """Check a Stripe-Signature header: an HMAC-SHA256 of "<timestamp>.<body>" within the replay tolerance."""
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    timestamps = parts.get("t", [])
    if not timestamps or not timestamps[0].isdigit() or not parts.get("v1"):
        raise BillingError("The webhook signature isn't valid")
    timestamp = int(timestamps[0])
    if abs((time.time() if now is None else now) - timestamp) > STRIPE_TOLERANCE_SECONDS:
        raise BillingError("The webhook is too old")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts["v1"]):
        raise BillingError("The webhook signature isn't valid")


def _epoch(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if isinstance(value, int | float) else None


class StripeProvider:
    id = "stripe"
    label = "Stripe"

    def _secret_key(self) -> str:
        return os.getenv("STRIPE_SECRET_KEY", "").strip()

    def configured(self) -> bool:
        return bool(self._secret_key() and os.getenv("STRIPE_WEBHOOK_SECRET", "").strip())

    def _request(self, method: str, path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._secret_key():
            raise BillingError("Online payment isn't configured on this server")
        try:
            response = requests.request(
                method, f"{STRIPE_API}/{path}", data=data, auth=(self._secret_key(), ""), timeout=20
            )
        except requests.RequestException as exc:
            raise BillingError("The payment provider couldn't be reached. Try again shortly.") from exc
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            logger.warning("Stripe %s %s failed: %s", method, path, (body.get("error") or {}).get("type"))
            raise BillingError("The payment provider refused the request. Try again or contact support.")
        return body

    def checkout(self, request: CheckoutRequest) -> str:
        price = ((request.plan.provider_prices or {}).get("stripe") or {}).get(request.interval)
        if not price:
            raise BillingError(
                f"The {request.plan.name} plan isn't available for online payment by the {request.interval}"
            )
        data: dict[str, Any] = {
            "mode": "subscription",
            "success_url": request.success_url,
            "cancel_url": request.cancel_url,
            "client_reference_id": request.account.key,
            "line_items[0][price]": price,
            "line_items[0][quantity]": 1,
            "allow_promotion_codes": "true",
            "metadata[account]": request.account.key,
            "metadata[plan]": request.plan.code,
            "subscription_data[metadata][account]": request.account.key,
            "subscription_data[metadata][plan]": request.plan.code,
        }
        if request.existing is not None and request.existing.provider_customer_id:
            data["customer"] = request.existing.provider_customer_id
        else:
            data["customer_email"] = request.email
        return str(self._request("POST", "checkout/sessions", data)["url"])

    def portal(self, subscription: models.Subscription, return_url: str) -> str:
        if not subscription.provider_customer_id:
            raise BillingError("There is no payment account to manage yet")
        session = self._request(
            "POST", "billing_portal/sessions", {"customer": subscription.provider_customer_id, "return_url": return_url}
        )
        return str(session["url"])

    def parse_webhook(self, body: bytes, headers: Mapping[str, str]) -> ProviderEvent:
        secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise BillingError("Stripe webhooks aren't configured on this server")
        verify_stripe_signature(body, headers.get("stripe-signature", ""), secret)
        event = json.loads(body)
        kind = event.get("type", "")
        obj = (event.get("data") or {}).get("object") or {}
        base = {"event_id": event.get("id", ""), "original_type": kind, "payload": event}
        if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
            items = ((obj.get("items") or {}).get("data")) or [{}]
            item = items[0]
            price = item.get("price") or {}
            period_end = obj.get("current_period_end") or item.get("current_period_end")
            metadata = obj.get("metadata") or {}
            return ProviderEvent(
                type="subscription.deleted" if kind.endswith("deleted") else "subscription.updated",
                account=Account.parse(metadata.get("account", "")),
                plan_code=metadata.get("plan", ""),
                price_id=price.get("id", ""),
                status="canceled"
                if kind.endswith("deleted")
                else _STRIPE_STATUS.get(obj.get("status", ""), "incomplete"),
                interval=(price.get("recurring") or {}).get("interval", ""),
                current_period_end=_epoch(period_end),
                cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
                customer_id=obj.get("customer") or "",
                subscription_id=obj.get("id") or "",
                **base,
            )
        if kind == "checkout.session.completed":
            metadata = obj.get("metadata") or {}
            return ProviderEvent(
                type="checkout.completed",
                account=Account.parse(metadata.get("account") or obj.get("client_reference_id") or ""),
                plan_code=metadata.get("plan", ""),
                customer_id=obj.get("customer") or "",
                subscription_id=obj.get("subscription") or "",
                **base,
            )
        if kind == "invoice.payment_failed":
            subscription_id = obj.get("subscription") or (
                ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription") or ""
            )
            return ProviderEvent(
                type="payment.failed", customer_id=obj.get("customer") or "", subscription_id=subscription_id, **base
            )
        return ProviderEvent(type="ignored", **base)

    def cancel(self, subscription: models.Subscription, at_period_end: bool) -> None:
        if not subscription.provider_subscription_id:
            return
        path = f"subscriptions/{subscription.provider_subscription_id}"
        if at_period_end:
            self._request("POST", path, {"cancel_at_period_end": "true"})
        else:
            self._request("DELETE", path)


PROVIDERS: dict[str, BillingProvider] = {"manual": ManualProvider(), "dev": DevProvider(), "stripe": StripeProvider()}


def provider_id() -> str:
    return os.getenv("BILLING_PROVIDER", "manual").strip().lower() or "manual"


def provider() -> BillingProvider:
    chosen = PROVIDERS.get(provider_id())
    if chosen is None:
        raise BillingError(f"Unknown BILLING_PROVIDER: {provider_id()}")
    if not chosen.configured():
        raise BillingError(f"{chosen.label} isn't configured on this server")
    return chosen


# --- Applying events ---


def plan_for_event(db: Session, event: ProviderEvent, provider_name: str) -> models.Plan | None:
    plans = list(db.scalars(select(models.Plan)))
    if event.price_id:
        for plan in plans:
            prices = (plan.provider_prices or {}).get(provider_name) or {}
            if event.price_id in prices.values():
                return plan
    return next((plan for plan in plans if plan.code == event.plan_code), None)


def notify_account(db: Session, account: Account, title: str, body: str) -> None:
    if account.kind == "user":
        recipients = [account.id]
    else:
        recipients = list(
            db.scalars(
                select(models.OrganizationMember.user_id).where(
                    models.OrganizationMember.organization_id == account.id,
                    models.OrganizationMember.role.in_(("owner", "admin")),
                )
            )
        )
    for user_id in recipients:
        notifications.notify(
            db, user_id=user_id, project_id=None, kind="billing", title=title, body=body, link="/billing"
        )


def apply_event(db: Session, provider_name: str, event: ProviderEvent) -> str:
    """Update the subscription an event describes. Returns "processed" or "ignored"; raises BillingError when the
    event can't be applied (for example an unknown plan)."""
    if event.type == "ignored":
        return "ignored"
    subscription = None
    if event.subscription_id:
        subscription = db.scalar(
            select(models.Subscription).where(
                models.Subscription.provider == provider_name,
                models.Subscription.provider_subscription_id == event.subscription_id,
            )
        )
    if subscription is None and event.account is not None:
        subscription = subscription_for(db, event.account)
    if event.type == "checkout.completed":
        if subscription is not None and event.customer_id:
            subscription.provider_customer_id = event.customer_id
        return "processed"
    if event.type == "payment.failed":
        if subscription is None:
            raise BillingError("A failed payment arrived for a subscription this server doesn't know")
        subscription.status = "past_due"
        account = _account_of(subscription)
        notify_account(
            db, account, "A payment failed", "Update your payment details under Billing to keep your current plan."
        )
        return "processed"
    if event.type == "subscription.deleted":
        if subscription is None:
            return "ignored"
        subscription.status = "canceled"
        subscription.cancel_at_period_end = False
        notify_account(db, _account_of(subscription), "Your subscription ended", "The account is now on the free plan.")
        return "processed"
    # subscription.updated
    owner = event.account or (_account_of(subscription) if subscription else None)
    if owner is None:
        raise BillingError("The subscription doesn't say which account it belongs to")
    plan = plan_for_event(db, event, provider_name)
    if plan is None:
        raise BillingError("The subscription is for a plan this server doesn't know")
    changed_plan = subscription is None or subscription.plan_id != plan.id
    if subscription is None:
        subscription = models.Subscription(
            user_id=owner.id if owner.kind == "user" else None,
            organization_id=owner.id if owner.kind == "organization" else None,
            plan_id=plan.id,
            status=event.status or "active",
            provider=provider_name,
        )
        db.add(subscription)
    subscription.plan_id = plan.id
    subscription.status = event.status or "active"
    subscription.provider = provider_name
    subscription.interval = event.interval if event.interval in INTERVALS else subscription.interval
    subscription.current_period_end = event.current_period_end
    subscription.cancel_at_period_end = event.cancel_at_period_end
    if event.customer_id:
        subscription.provider_customer_id = event.customer_id
    if event.subscription_id:
        subscription.provider_subscription_id = event.subscription_id
    db.flush()
    if changed_plan:
        notify_account(db, owner, f"You're now on the {plan.name} plan", "Your new limits apply straight away.")
    elif subscription.status == "past_due":
        notify_account(db, owner, "A payment is overdue", "Update your payment details under Billing.")
    return "processed"


def _account_of(subscription: models.Subscription) -> Account:
    if subscription.organization_id is not None:
        return Account("organization", subscription.organization_id)
    return Account("user", subscription.user_id or 0)


def handle_webhook(db: Session, chosen: BillingProvider, body: bytes, headers: Mapping[str, str]) -> str:
    """Verify, record, and apply a webhook once. Returns "processed", "ignored", "duplicate", or "failed"."""
    event = chosen.parse_webhook(body, {key.lower(): value for key, value in headers.items()})
    if not event.event_id:
        raise BillingError("The webhook has no event id")
    exists = db.scalar(
        select(models.BillingEvent.id).where(
            models.BillingEvent.provider == chosen.id, models.BillingEvent.event_id == event.event_id
        )
    )
    if exists is not None:
        return "duplicate"
    record = models.BillingEvent(
        provider=chosen.id, event_id=event.event_id, type=event.original_type, payload=event.payload, status="received"
    )
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        # The same event arrived concurrently and the other delivery recorded it first.
        return "duplicate"
    try:
        record.status = apply_event(db, chosen.id, event)
    except BillingError as exc:
        logger.error("Billing event %s from %s couldn't be applied: %s", event.event_id, chosen.id, exc)
        record.status, record.error = "failed", str(exc)
    db.commit()
    return record.status


def account_email(db: Session, account: Account, fallback: models.User) -> str:
    if account.kind == "user":
        user = db.get(models.User, account.id)
        return user.email if user else fallback.email
    return fallback.email


__all__ = ["BillingError", "BillingProvider", "CheckoutRequest", "ProviderEvent", "account_label"]
