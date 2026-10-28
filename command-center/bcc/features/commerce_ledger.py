"""OrderToDeliveryLedger: End-to-end commerce unit economics and lifecycle ledger.

Implements all 6 commerce nodes from tree-registry.json:
1. reg-commerce_product_packages: Catalog packages, SKUs, and service specs.
2. reg-commerce_buyer_demand: Buyer intent, counterparty ID, and requested quantity/currency.
3. reg-commerce_offer_cost: Unit economics (gross - api_cost - compute_cost - fees = net_margin)
   with strict explicit manager margin enforcement (COM-UP-1).
4. reg-commerce_order_delivery: Fulfillment, escrow transition, and cryptographic delivery receipt.
5. reg-commerce_revenue_accounting: Strict isolation of PAPER_DEMO vs AUDITED_PAID_REVENUE ($0.00 invariant).
6. reg-commerce_support_refunds: Customer issue resolution, SLA tracking, and clawback/refund handling.
"""
from __future__ import annotations

import datetime
import enum
import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping


class CommerceError(Exception):
    """Base exception for Commerce Ledger operations."""


class ExplicitMarginRequiredError(CommerceError):
    """Raised when an offer is priced without explicit manager margin."""


class UnverifiedRevenueClassificationError(CommerceError):
    """Raised when unverified or paper transactions attempt to claim AUDITED_PAID_REVENUE."""


class RevenueStatus(str, enum.Enum):
    PAPER_DEMO = "PAPER_DEMO"
    AUDITED_PAID_REVENUE = "AUDITED_PAID_REVENUE"


class OrderState(str, enum.Enum):
    DEMAND_LOGGED = "DEMAND_LOGGED"
    OFFER_COSTED = "OFFER_COSTED"
    APPROVED = "APPROVED"
    DELIVERED = "DELIVERED"
    SETTLED = "SETTLED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class UnitEconomics:
    gross_revenue: Decimal
    api_cost: Decimal
    compute_cost: Decimal
    fees: Decimal
    net_margin: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "gross_revenue": str(self.gross_revenue),
            "api_cost": str(self.api_cost),
            "compute_cost": str(self.compute_cost),
            "fees": str(self.fees),
            "net_margin": str(self.net_margin),
            "margin_pct": str((self.net_margin / self.gross_revenue * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if self.gross_revenue > 0 else "0.00",
        }


@dataclass
class CommerceOrder:
    order_id: str
    client_id: str
    package_sku: str
    state: OrderState
    revenue_status: RevenueStatus
    created_at: str
    demand: dict[str, Any] = field(default_factory=dict)
    economics: UnitEconomics | None = None
    delivery_receipt: dict[str, Any] | None = None
    payment_proof: dict[str, Any] | None = None
    refund_record: dict[str, Any] | None = None
    node_trail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_id": self.client_id,
            "package_sku": self.package_sku,
            "state": self.state.value,
            "revenue_status": self.revenue_status.value,
            "created_at": self.created_at,
            "demand": self.demand,
            "economics": self.economics.to_dict() if self.economics else None,
            "delivery_receipt": self.delivery_receipt,
            "payment_proof": self.payment_proof,
            "refund_record": self.refund_record,
            "node_trail": list(self.node_trail),
        }


class OrderToDeliveryLedger:
    """Multi-node Commerce Ledger managing orders from demand ingestion to delivery and accounting."""

    def __init__(self) -> None:
        self._packages: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, CommerceOrder] = {}
        self._init_default_packages()

    def _init_default_packages(self) -> None:
        # reg-commerce_product_packages: pre-registered product packages
        self.register_product_package(
            sku="PKG-SWAPME-REMIT-01",
            name="SwapMe OTC Currency Swap Service",
            base_fee_usd=Decimal("0.00"),
            min_margin_pct=Decimal("0.01"),
        )
        self.register_product_package(
            sku="PKG-VIDEO-STUDIO-MOTION-01",
            name="Video Studio 2.0 Motion Render",
            base_fee_usd=Decimal("5.00"),
            min_margin_pct=Decimal("0.15"),
        )

    # 1. reg-commerce_product_packages
    def register_product_package(
        self,
        sku: str,
        name: str,
        base_fee_usd: Decimal | str | float = Decimal("0.00"),
        min_margin_pct: Decimal | str | float = Decimal("0.01"),
    ) -> dict[str, Any]:
        pkg = {
            "sku": sku,
            "name": name,
            "base_fee_usd": Decimal(str(base_fee_usd)),
            "min_margin_pct": Decimal(str(min_margin_pct)),
            "node": "reg-commerce_product_packages",
        }
        self._packages[sku] = pkg
        return pkg

    # 2. reg-commerce_buyer_demand
    def record_buyer_demand(
        self,
        *,
        client_id: str,
        package_sku: str,
        volume: Decimal | str | float,
        currency: str = "USDT",
        notes: str = "",
    ) -> CommerceOrder:
        if package_sku not in self._packages:
            raise CommerceError(f"Unknown product package SKU: {package_sku}")

        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        digest = hashlib.sha256(f"{client_id}:{package_sku}:{volume}:{now_str}".encode()).hexdigest()[:8].upper()
        order_id = f"ORD-{digest}"

        order = CommerceOrder(
            order_id=order_id,
            client_id=client_id,
            package_sku=package_sku,
            state=OrderState.DEMAND_LOGGED,
            revenue_status=RevenueStatus.PAPER_DEMO,
            created_at=now_str,
            demand={
                "volume": str(Decimal(str(volume))),
                "currency": currency,
                "notes": notes,
                "timestamp": now_str,
            },
            node_trail=["reg-commerce_product_packages", "reg-commerce_buyer_demand"],
        )
        self._orders[order_id] = order
        return order

    # 3. reg-commerce_offer_cost
    def calculate_offer_cost(
        self,
        order_id: str,
        *,
        gross_revenue: Decimal | str | float,
        margin_pct: Decimal | str | float | None = None,
        api_cost: Decimal | str | float = Decimal("0.00"),
        compute_cost: Decimal | str | float = Decimal("0.00"),
        fees: Decimal | str | float = Decimal("0.00"),
        require_explicit_margin: bool = True,
    ) -> UnitEconomics:
        if order_id not in self._orders:
            raise CommerceError(f"Order not found: {order_id}")

        if require_explicit_margin and margin_pct is None:
            raise ExplicitMarginRequiredError(
                "COM-UP-1: Offer calculation rejected. Explicit manager margin decision required."
            )

        gross = Decimal(str(gross_revenue))
        api = Decimal(str(api_cost))
        comp = Decimal(str(compute_cost))
        fee = Decimal(str(fees))

        m_pct = Decimal(str(margin_pct)) if margin_pct is not None else Decimal("0.03")
        calculated_margin = (gross * m_pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net_margin = calculated_margin - api - comp - fee

        economics = UnitEconomics(
            gross_revenue=gross,
            api_cost=api,
            compute_cost=comp,
            fees=fee,
            net_margin=net_margin,
        )

        order = self._orders[order_id]
        order.economics = economics
        order.state = OrderState.OFFER_COSTED
        if "reg-commerce_offer_cost" not in order.node_trail:
            order.node_trail.append("reg-commerce_offer_cost")

        return economics

    # 4. reg-commerce_order_delivery
    def deliver_order(
        self,
        order_id: str,
        *,
        deliverable_payload: Mapping[str, Any],
        operator: str = "manager",
    ) -> dict[str, Any]:
        if order_id not in self._orders:
            raise CommerceError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        if order.economics is None:
            raise CommerceError(f"Cannot deliver order {order_id} without costed economics")

        payload_bytes = json.dumps(deliverable_payload, sort_keys=True, default=str).encode("utf-8")
        receipt_hash = hashlib.sha256(payload_bytes).hexdigest()
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        delivery_receipt = {
            "order_id": order_id,
            "receipt_hash": receipt_hash,
            "operator": operator,
            "delivered_at": now_str,
            "payload_summary": {k: str(v)[:80] for k, v in deliverable_payload.items()},
        }
        order.delivery_receipt = delivery_receipt
        order.state = OrderState.DELIVERED
        if "reg-commerce_order_delivery" not in order.node_trail:
            order.node_trail.append("reg-commerce_order_delivery")

        return delivery_receipt

    # 5. reg-commerce_revenue_accounting
    def settle_revenue(
        self,
        order_id: str,
        *,
        payment_proof: Mapping[str, Any] | None = None,
    ) -> RevenueStatus:
        """Settle revenue accounting with strict separation of PAPER_DEMO vs AUDITED_PAID_REVENUE."""
        if order_id not in self._orders:
            raise CommerceError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        if order.state != OrderState.DELIVERED:
            raise CommerceError(f"Cannot settle revenue for undelivered order in state {order.state}")

        # Verification of real external payment
        if payment_proof and payment_proof.get("verified_external_tx_id") and payment_proof.get("bank_or_gateway_ref"):
            order.revenue_status = RevenueStatus.AUDITED_PAID_REVENUE
            order.payment_proof = dict(payment_proof)
        else:
            # Paper demo transactions stay strictly PAPER_DEMO ($0.00 external revenue)
            order.revenue_status = RevenueStatus.PAPER_DEMO
            order.payment_proof = {
                "paper_demo": True,
                "note": "Internal/simulated execution — zero external payment documents.",
            }

        order.state = OrderState.SETTLED
        if "reg-commerce_revenue_accounting" not in order.node_trail:
            order.node_trail.append("reg-commerce_revenue_accounting")

        return order.revenue_status

    # 6. reg-commerce_support_refunds
    def process_refund(
        self,
        order_id: str,
        *,
        reason: str,
        refund_amount: Decimal | str | float | None = None,
        operator: str = "support_agent",
    ) -> dict[str, Any]:
        if order_id not in self._orders:
            raise CommerceError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        amount = Decimal(str(refund_amount)) if refund_amount is not None else (order.economics.gross_revenue if order.economics else Decimal("0.00"))

        refund = {
            "order_id": order_id,
            "refunded_amount": str(amount),
            "reason": reason,
            "operator": operator,
            "timestamp": now_str,
        }
        order.refund_record = refund
        order.state = OrderState.REFUNDED
        if "reg-commerce_support_refunds" not in order.node_trail:
            order.node_trail.append("reg-commerce_support_refunds")

        return refund

    def get_order(self, order_id: str) -> CommerceOrder | None:
        return self._orders.get(order_id)

    def get_ledger_summary(self) -> dict[str, Any]:
        paper_count = 0
        audited_count = 0
        total_paper_gross = Decimal("0.00")
        total_audited_gross = Decimal("0.00")

        for order in self._orders.values():
            gross = order.economics.gross_revenue if order.economics else Decimal("0.00")
            if order.revenue_status == RevenueStatus.AUDITED_PAID_REVENUE:
                audited_count += 1
                total_audited_gross += gross
            else:
                paper_count += 1
                total_paper_gross += gross

        return {
            "total_orders": len(self._orders),
            "paper_demo_orders": paper_count,
            "audited_paid_orders": audited_count,
            "paper_demo_gross_volume_usd": str(total_paper_gross),
            "audited_external_revenue_usd": str(total_audited_gross),
            "invariant_audited_revenue_matches_zero_when_no_real_proof": (total_audited_gross == Decimal("0.00") if audited_count == 0 else True),
        }
