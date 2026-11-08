"""Bullshot.io & Bull GEMS Independent Earnings Ledger and EV Verifier.

Implements Phase 1 and Phase 4 accounting rules for Argon Master Prompt 1:
1. Expected Net Earnings formula:
   Expected net earnings = Realizable reward value - Direct costs - Transaction fees - Execution costs
2. Untradeable off-chain points/GEMS/XP/ores must have zero confirmed realized USD income
   ($0.00) until actual on-chain monetization occurs.
3. Strict experiment status classification:
   NOT_STARTED | SIMULATED | PENDING | VERIFIED_REWARD | VERIFIED_NET_PROFIT | FAILED
4. Overall lifecycle verdict:
   VERIFIED_NET_PROFIT is granted ONLY when an experiment has settled on-chain transaction
   hashes, independent RPC/explorer verification, zero conflation with outside deposits,
   and strictly positive net realized USD/ETH profit across the full entry-to-exit lifecycle.
   Otherwise the overall verdict is NOT_PROVEN with the exact mathematical/protocol blocker.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

VALID_STATUSES = (
    "NOT_STARTED",
    "SIMULATED",
    "PENDING",
    "VERIFIED_REWARD",
    "VERIFIED_NET_PROFIT",
    "FAILED",
)

OVERALL_VERDICTS = ("VERIFIED_NET_PROFIT", "NOT_PROVEN")

# Official Bullshot.io Base Mainnet (8453) protocol constants verified on-chain
BASE_CHAIN_ID = 8453
BULLSHOT_CREATION_FEE_ETH = 0.00005
BULLSHOT_POOL_SWAP_FEE_RATE = 0.0100       # 1.00% Uniswap V3 pool fee tier (10000)
BULLSHOT_CREATOR_FEE_SHARE = 0.70          # 70% of 1% = 0.70% per swap to token creator
BULLSHOT_PROTOCOL_FEE_SHARE = 0.30         # 30% of 1% = 0.30% per swap retained by protocol
BULLSHOT_EFFECTIVE_CREATOR_RATE = BULLSHOT_POOL_SWAP_FEE_RATE * BULLSHOT_CREATOR_FEE_SHARE   # 0.0070
BULLSHOT_EFFECTIVE_PROTOCOL_CUT = BULLSHOT_POOL_SWAP_FEE_RATE * BULLSHOT_PROTOCOL_FEE_SHARE  # 0.0030


@dataclass(frozen=True)
class ExpectedEarningsCalculation:
    """Result of the Phase 1 Expected Net Earnings formula."""

    method_id: str
    method_name: str
    is_onchain: bool
    is_monetizable_now: bool
    gross_reward_units: float
    reward_unit_name: str
    realizable_reward_usd: float
    direct_costs_usd: float
    transaction_fees_usd: float
    execution_costs_usd: float
    expected_net_earnings_usd: float
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_expected_net_earnings(
    *,
    method_id: str,
    method_name: str,
    is_onchain: bool,
    is_monetizable_now: bool,
    gross_reward_units: float,
    reward_unit_name: str,
    unit_market_price_usd: float,
    direct_costs_usd: float,
    transaction_fees_usd: float,
    execution_costs_usd: float,
    notes: str = "",
) -> ExpectedEarningsCalculation:
    """Compute Expected Net Earnings under strict Phase 1 monetization rules.

    Formula:
        Expected net earnings = Realizable reward value - Direct costs - Transaction fees - Execution costs

    Crucial constraint:
        Untradeable points / off-chain GEMS / XP / ores (`is_monetizable_now=False`)
        are forced to `realizable_reward_usd = 0.0` until actual monetization occurs.
    """
    for val, name in (
        (gross_reward_units, "gross_reward_units"),
        (unit_market_price_usd, "unit_market_price_usd"),
        (direct_costs_usd, "direct_costs_usd"),
        (transaction_fees_usd, "transaction_fees_usd"),
        (execution_costs_usd, "execution_costs_usd"),
    ):
        if not math.isfinite(val) or val < 0.0:
            raise ValueError(f"{name} must be a non-negative finite number, got {val!r}")

    if is_monetizable_now:
        realizable_usd = round(gross_reward_units * unit_market_price_usd, 6)
    else:
        realizable_usd = 0.0

    net_usd = round(
        realizable_usd - direct_costs_usd - transaction_fees_usd - execution_costs_usd,
        6,
    )
    return ExpectedEarningsCalculation(
        method_id=method_id,
        method_name=method_name,
        is_onchain=is_onchain,
        is_monetizable_now=is_monetizable_now,
        gross_reward_units=round(gross_reward_units, 6),
        reward_unit_name=reward_unit_name,
        realizable_reward_usd=realizable_usd,
        direct_costs_usd=round(direct_costs_usd, 6),
        transaction_fees_usd=round(transaction_fees_usd, 6),
        execution_costs_usd=round(execution_costs_usd, 6),
        expected_net_earnings_usd=net_usd,
        notes=notes,
    )


def calculate_creator_fee_breakeven_volume_usd(
    *,
    eth_price_usd: float,
    creation_fee_eth: float = BULLSHOT_CREATION_FEE_ETH,
    total_gas_eth: float = 0.000010,
    creator_fee_rate: float = BULLSHOT_EFFECTIVE_CREATOR_RATE,
) -> float:
    """Return external swap volume (USD) required for a token creator to break even.

    Because the creator earns `0.70%` (`0.0070`) of external swap volume via
    `FeeCollector.collectAll(token)`, break-even external volume is:
        V_ext = (creation_fee_usd + gas_usd) / 0.0070
    """
    if eth_price_usd <= 0 or not math.isfinite(eth_price_usd):
        raise ValueError("eth_price_usd must be positive and finite")
    if creator_fee_rate <= 0 or not math.isfinite(creator_fee_rate):
        raise ValueError("creator_fee_rate must be positive and finite")
    fixed_cost_usd = (creation_fee_eth + total_gas_eth) * eth_price_usd
    return round(fixed_cost_usd / creator_fee_rate, 4)


def calculate_self_volume_cycle_loss_eth(
    *,
    swap_amount_eth: float,
    cycles: int,
    gas_per_cycle_eth: float = 0.0000017,
) -> dict[str, float]:
    """Calculate exact ETH loss from rotating self-volume on a Bullshot Base pool.

    Each Buy + Sell cycle of size `swap_amount_eth` incurs:
    - 1.00% pool fee on Buy + 1.00% pool fee on Sell = 2.00% gross pool fee
    - 70% of that fee (1.40% of swap_amount_eth) is reclaimable via FeeCollector.collectAll
    - 30% of that fee (0.60% of swap_amount_eth) is permanently retained by the protocol
    - Plus Base L2 gas for Buy, Sell, and FeeCollector.collectAll
    """
    if swap_amount_eth < 0 or cycles < 0:
        raise ValueError("swap_amount_eth and cycles must be non-negative")
    total_volume_eth = swap_amount_eth * 2.0 * cycles
    gross_pool_fee_eth = total_volume_eth * BULLSHOT_POOL_SWAP_FEE_RATE
    creator_rebate_eth = gross_pool_fee_eth * BULLSHOT_CREATOR_FEE_SHARE
    protocol_leakage_eth = gross_pool_fee_eth * BULLSHOT_PROTOCOL_FEE_SHARE
    total_gas_eth = gas_per_cycle_eth * cycles
    net_pnl_eth = -(protocol_leakage_eth + total_gas_eth)
    return {
        "total_volume_eth": round(total_volume_eth, 8),
        "gross_pool_fee_eth": round(gross_pool_fee_eth, 8),
        "creator_rebate_eth": round(creator_rebate_eth, 8),
        "protocol_leakage_eth": round(protocol_leakage_eth, 8),
        "total_gas_eth": round(total_gas_eth, 8),
        "net_pnl_eth": round(net_pnl_eth, 8),
    }


@dataclass
class EarningsLedgerEntry:
    """Single audited experiment record in the Phase 4 Earnings Ledger."""

    experiment_id: str
    timestamp: str
    chain: str
    wallet_or_account: str
    tx_hashes: list[str]
    initial_balances: dict[str, float]
    outside_deposits: dict[str, float]
    operations_performed: list[str]
    rewards_received: dict[str, float]
    is_reward_monetizable_onchain: bool
    cash_out_proceeds_usd: float
    direct_costs_usd: float
    gas_and_protocol_fees_usd: float
    execution_costs_usd: float
    net_realized_pnl_usd: float
    net_realized_pnl_eth: float
    independent_verification_method: str
    status: str
    notes: str = ""

    def validate(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {self.status!r}; must be one of {VALID_STATUSES}")

        for val, name in (
            (self.cash_out_proceeds_usd, "cash_out_proceeds_usd"),
            (self.direct_costs_usd, "direct_costs_usd"),
            (self.gas_and_protocol_fees_usd, "gas_and_protocol_fees_usd"),
            (self.execution_costs_usd, "execution_costs_usd"),
            (self.net_realized_pnl_usd, "net_realized_pnl_usd"),
            (self.net_realized_pnl_eth, "net_realized_pnl_eth"),
        ):
            if not math.isfinite(val):
                raise ValueError(f"{name} must be finite, got {val!r}")

        # Enforce Phase 1/4 rule: off-chain untradeable rewards cannot claim cash_out_proceeds_usd > 0
        if not self.is_reward_monetizable_onchain and self.cash_out_proceeds_usd > 0.0:
            raise ValueError(
                f"Experiment {self.experiment_id}: untradeable off-chain rewards cannot have "
                f"positive cash_out_proceeds_usd ({self.cash_out_proceeds_usd})"
            )

        expected_net = round(
            self.cash_out_proceeds_usd
            - self.direct_costs_usd
            - self.gas_and_protocol_fees_usd
            - self.execution_costs_usd,
            4,
        )
        if abs(expected_net - round(self.net_realized_pnl_usd, 4)) > 0.02:
            raise ValueError(
                f"Experiment {self.experiment_id}: net_realized_pnl_usd ({self.net_realized_pnl_usd}) "
                f"does not match proceeds - costs ({expected_net})"
            )

        # Enforce strict VERIFIED_NET_PROFIT guardrails
        if self.status == "VERIFIED_NET_PROFIT":
            if not self.tx_hashes:
                raise ValueError(
                    f"Experiment {self.experiment_id}: VERIFIED_NET_PROFIT requires settled on-chain tx_hashes"
                )
            if not self.is_reward_monetizable_onchain:
                raise ValueError(
                    f"Experiment {self.experiment_id}: VERIFIED_NET_PROFIT cannot be claimed on untradeable off-chain points/GEMS"
                )
            if self.net_realized_pnl_usd <= 0.0 or self.net_realized_pnl_eth <= 0.0:
                raise ValueError(
                    f"Experiment {self.experiment_id}: VERIFIED_NET_PROFIT requires positive net realized P&L "
                    f"(got USD={self.net_realized_pnl_usd}, ETH={self.net_realized_pnl_eth})"
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BullshotEarningsLedger:
    """Phase 4 & 5 verifiable earnings ledger with overall verdict evaluation."""

    def __init__(self, entries: Sequence[EarningsLedgerEntry] | None = None) -> None:
        self._entries: list[EarningsLedgerEntry] = []
        for entry in entries or ():
            self.add_entry(entry)

    @property
    def entries(self) -> list[EarningsLedgerEntry]:
        return list(self._entries)

    def add_entry(self, entry: EarningsLedgerEntry) -> None:
        entry.validate()
        self._entries.append(entry)

    def summary(self) -> dict[str, Any]:
        total_proceeds_usd = round(sum(e.cash_out_proceeds_usd for e in self._entries), 4)
        total_direct_costs_usd = round(sum(e.direct_costs_usd for e in self._entries), 4)
        total_fees_usd = round(sum(e.gas_and_protocol_fees_usd for e in self._entries), 4)
        total_exec_costs_usd = round(sum(e.execution_costs_usd for e in self._entries), 4)
        total_net_pnl_usd = round(sum(e.net_realized_pnl_usd for e in self._entries), 4)
        total_net_pnl_eth = round(sum(e.net_realized_pnl_eth for e in self._entries), 8)

        verified_net_profit_entries = [
            e.experiment_id for e in self._entries if e.status == "VERIFIED_NET_PROFIT"
        ]
        verified_reward_entries = [
            e.experiment_id for e in self._entries if e.status == "VERIFIED_REWARD"
        ]

        # Overall verdict across the Bullshot campaign lifecycle:
        # Must be NOT_PROVEN unless total_net_pnl_usd > 0 AND at least one VERIFIED_NET_PROFIT exists
        if verified_net_profit_entries and total_net_pnl_usd > 0.0 and total_net_pnl_eth > 0.0:
            overall_verdict = "VERIFIED_NET_PROFIT"
            blocker = None
        else:
            overall_verdict = "NOT_PROVEN"
            blocker = (
                "1) On-chain Bullshot.io (Base 8453) creator fee claims via FeeCollector.collectAll() are "
                "VERIFIED_REWARD (reclaimed 0.00049765 ETH / $1.33 USD in WETH fees + recovered 0.00152448 ETH "
                "from token inventory), but self-funded swap cycles incur a 0.30% protocol fee cut per swap "
                "(0.60% per buy+sell round-trip) plus a 0.00005 ETH token creation fee, resulting in negative "
                "full-lifecycle net realized P&L without external buyer volume (> $19.43 USD external volume "
                "per token required to break even). "
                "2) Off-chain Bull GEMS (gem.bullshot.io) farming accumulated 47,537.52 liquid GEMS (60,802.52 "
                "net worth GEMS, Level 15, 260 MP) at $0.00 gas cost (VERIFIED_REWARD), but GEMS/XP/ores are "
                "currently backend-only database records with no on-chain withdrawal/redemption contract deployed "
                "yet ($0.00 confirmed realized USD income under strict accounting)."
            )

        return {
            "total_experiments": len(self._entries),
            "status_counts": {
                st: sum(1 for e in self._entries if e.status == st) for st in VALID_STATUSES
            },
            "verified_reward_experiment_ids": verified_reward_entries,
            "verified_net_profit_experiment_ids": verified_net_profit_entries,
            "total_cash_out_proceeds_usd": total_proceeds_usd,
            "total_direct_costs_usd": total_direct_costs_usd,
            "total_gas_and_protocol_fees_usd": total_fees_usd,
            "total_execution_costs_usd": total_exec_costs_usd,
            "total_net_realized_pnl_usd": total_net_pnl_usd,
            "total_net_realized_pnl_eth": total_net_pnl_eth,
            "overall_verdict": overall_verdict,
            "blocker": blocker,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "entries": [e.to_dict() for e in self._entries],
        }

    def save_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out


def build_canonical_bullshot_ledger() -> BullshotEarningsLedger:
    """Build the verified Phase 4 ledger from our real on-chain Base & off-chain Gem experiments."""
    entries = [
        EarningsLedgerEntry(
            experiment_id="EXP-01-BASE-BSENT-LAUNCH-AND-FEE-CLAIM",
            timestamp="2026-11-07T16:38:06Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[
                "0x0f7ebccb07eda0da0d0c97cd897dc9d566ee754e2b1736db1ff6da3a8a7d73e1",
                "0x1c64dfb75c27b0e8d44f08d608e33fc2475f7889304823482442c0549be5534f",
                "0xb77217aa82480886729c01980b1593c98723476867477a3f53953445f6166a41",
                "0x6a9be17c5ef373915a66c1cf051f3339f21dc764438e688318661411d133e326",
            ],
            initial_balances={"ETH": 0.01298828},
            outside_deposits={"ETH": 0.01298828},
            operations_performed=[
                "BaseFactory.createToken($BSENT #315 -> 0xaf17eede03d9d8fde2c0560a08964cc6c7c118ab)",
                "32 Uniswap V3 Buy/Sell swaps rotating 0.064 ETH ($171.52 USD) volume",
                "FeeCollector.collectAll($BSENT) reclaiming 70% creator LP fees (0.0002123 ETH)",
                "WETH.withdraw(0.0002123 ETH) to native ETH",
            ],
            rewards_received={"WETH_creator_fees_eth": 0.0002123, "WETH_creator_fees_usd": 0.569},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=0.569,
            direct_costs_usd=0.134,          # 0.00005 ETH launch fee
            gas_and_protocol_fees_usd=2.3598, # 1% pool fees (0.569 returned to creator + 1.7908 protocol/curve + gas)
            execution_costs_usd=0.0,
            net_realized_pnl_usd=-1.9248,
            net_realized_pnl_eth=-0.00071826,
            independent_verification_method=(
                "Base Mainnet RPC eth_getTransactionReceipt & eth_getBalance "
                "(0.01298828 ETH -> 0.01227002 ETH; base_campaign_result.json)"
            ),
            status="VERIFIED_REWARD",
            notes=(
                "Verified that FeeCollector.collectAll() pays 70% of the 1% pool fee to the creator in real WETH "
                "(+0.0002123 ETH / $0.569), but self-swapping loses 0.30% per swap to the protocol (-0.00071826 ETH net)."
            ),
        ),
        EarningsLedgerEntry(
            experiment_id="EXP-02-BASE-ROBLOX3D-LAUNCH-AND-CAPITAL-RECOVERY",
            timestamp="2026-11-07T17:36:54Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[
                "0xe510847f3054999de23be4fe603da617fbffaa518331ac84e34cf75fe6f9aab8",
                "0x223d673dffbd4fe8c8630fefd8d55ef2c46d9eb55921b8886fc83ce395a20b1d",
                "0x497ac27ebef628d17a9eacb3bc692db05973c9f5164d6766652069cfae598eb4",
                "0x2a8c1ec6bb3fae6a3126c3389cea2d221bc97a38b3b1891162d730da6df34336",
            ],
            initial_balances={"ETH_before_launch": 0.01227002, "ETH_before_recovery": 0.01067756},
            outside_deposits={"ETH": 0.0},
            operations_performed=[
                "BaseFactory.createToken($ROBLOX3D #320 -> 0x061827f4829e53c2fe61f52c8e5740844c7068b9) + initial buy (0.00159246 ETH)",
                "SwapRouter02.multicall([exactInputSingle, unwrapWETH9]) liquidating held $ROBLOX3D inventory",
                "FeeCollector.collectAll($ROBLOX3D & $SWAPME) + WETH.withdraw()",
            ],
            rewards_received={"ETH_recovered_from_inventory_and_fees": 0.00146790},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=3.934,      # +0.00146790 ETH recovered in exit step
            direct_costs_usd=3.934,           # Principal portion of 0.00159246 ETH entry buy
            gas_and_protocol_fees_usd=0.3338, # 0.00012456 ETH launch fee + 0.30% protocol fee + gas
            execution_costs_usd=0.0,
            net_realized_pnl_usd=-0.3338,
            net_realized_pnl_eth=-0.00012456,
            independent_verification_method=(
                "Base Mainnet RPC eth_getBalance across full launch-to-exit lifecycle "
                "(0.01227002 ETH pre-launch -> 0.01067756 ETH post-launch -> 0.01214546 ETH post-recovery; maximum_growth_mission_log.json)"
            ),
            status="VERIFIED_REWARD",
            notes=(
                "Isolated recovery step returned +0.00146790 ETH (+$3.93 USD) for 0.00000226 ETH gas, "
                "but full launch-to-exit lifecycle net P&L was -0.00012456 ETH (-$0.33 USD)."
            ),
        ),
        EarningsLedgerEntry(
            experiment_id="EXP-03-BASE-6TOKEN-ROTATION-AND-FEE-HARVEST",
            timestamp="2026-11-08T13:25:20Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[
                "0xce83c744451295d567faca5b85f1ce058b398714bbc35d434e8ff5c81ebcac8b",
                "0x016759797aa17ba72fa3f7794e57cc7c9100ba23b7ed53894e21d9e5ac946c58",
                "0x70ba5a70824c0d25993b0f6d5aab621c652d7b1ea5c5b04630ebbf9e68ac3289",
            ],
            initial_balances={"ETH": 0.012145},
            outside_deposits={"ETH": 0.0},
            operations_performed=[
                "55 on-chain Base transactions across 6 created tokens ($BSENT, $SWAPME, $ROBLOX3D, $PRG420, $GEMMA, $BULLAI)",
                "Rotated 0.09036 ETH swap volume + retained ~33,782 tokens per coin (6 * 0.00006 ETH = 0.00036 ETH inventory)",
                "Reclaimed 0.000285 ETH ($0.76 USD) in WETH creator fees via FeeCollector.collectAll()",
            ],
            rewards_received={"WETH_creator_fees_eth": 0.000285, "held_tokens_each": 33782.0},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=0.7638,
            direct_costs_usd=0.9648,          # 0.00036 ETH spent into held token bags
            gas_and_protocol_fees_usd=1.7715, # 0.000376 ETH net protocol fee cut + gas after 0.000285 ETH rebate
            execution_costs_usd=0.0,
            net_realized_pnl_usd=-1.9725,
            net_realized_pnl_eth=-0.000736,
            independent_verification_method="Base Mainnet RPC eth_getTransactionReceipt (55 txs in old_tokens_boost_receipt.json)",
            status="VERIFIED_REWARD",
            notes="Reclaimed 0.000285 ETH in creator fees, but net liquid ETH decreased by 0.000736 ETH (including 0.00036 ETH in 6 held bags).",
        ),
        EarningsLedgerEntry(
            experiment_id="EXP-04-PHASE3-LIVE-BSENT-BAG-EXIT-AND-FEE-CLAIM",
            timestamp="2026-11-08T19:24:11Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[
                "0x6f39ee6456fd0f06bdf3c13ec77badea412e7b6fb791b6738d1327611ae2dda4",
                "0x30d196c245890feb6f9ebc9fbc8e95123ec43edeb131fa68c75b506ab9602ebf",
                "0x03701058c4cb96b02df4328bd51bad300c04053f35dfe4d78551644a0ad193b3",
            ],
            initial_balances={"ETH": 0.00591489, "BSENT": 33780.2526},
            outside_deposits={"ETH": 0.0},
            operations_performed=[
                "Pre-flight eth_call quote on SwapRouter02 (quoted 0.00005881 ETH)",
                "SwapRouter02.multicall([exactInputSingle(33,780.25 BSENT -> WETH), unwrapWETH9]) at block 54876438",
                "FeeCollector.collectAll($BSENT) at block 54876447 (+0.00000035 WETH + 370.3101 BSENT fee tokens)",
                "WETH.withdraw(0.00000035 WETH) at block 54876457",
            ],
            rewards_received={"ETH_gross_proceeds": 0.00005916, "BSENT_fee_tokens": 370.3101},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=0.1319,     # 0.00005916 ETH at $2,229.80/ETH
            direct_costs_usd=0.1338,          # 0.00006000 ETH original cost of the BSENT bag (tx 0x70ba5a70...)
            gas_and_protocol_fees_usd=0.0058, # 0.00000258 ETH total gas across 3 txs
            execution_costs_usd=0.0,
            net_realized_pnl_usd=-0.0077,
            net_realized_pnl_eth=-0.00000342,
            independent_verification_method=(
                "Base Mainnet RPC blocks 54876432..54876464 (0.00591489 ETH -> 0.00597147 ETH; "
                "reports/phase3_live_onchain_experiment.json)"
            ),
            status="VERIFIED_REWARD",
            notes=(
                "Live Phase 3 execution recovered +0.00005658 ETH (+$0.126 USD) into liquid native ETH after gas, "
                "proving on-chain liquidation & FeeCollector work deterministically. Relative to the 0.00006000 ETH "
                "entry cost of that bag, full-lifecycle P&L is -0.00000342 ETH (-$0.0077 USD)."
            ),
        ),
        EarningsLedgerEntry(
            experiment_id="EXP-05-GEM-BULLSHOT-OFFCHAIN-MINING-QUESTS-STAKING",
            timestamp="2026-11-08T19:15:24Z",
            chain="Off-Chain Backend (gemapi.bullshot.io)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[],
            initial_balances={"GEMS": 0.0, "XP": 0.0, "ETH_spent": 0.0},
            outside_deposits={"ETH": 0.0},
            operations_performed=[
                "SIWE authentication (POST /api/auth/nonce + POST /api/auth/login)",
                "Completed legitimate daily/weekly/one-time quests -> unlocked 2.7x mining multiplier",
                "Upgraded Mining Power I (upg-power-1) at 250 GEMS (+5 MP, +100 XP) to 260 MP & Level 15 (34,445 XP)",
                "Staked 2 Ruby (12% APY) & 3 Sapphire (14% APY) in 0-day lock pools",
                "Live Phase 3 claim of ready cave-6ad9ec1875966 position (+37.8 GEMS, +50 XP -> 47,537.5217 liquid GEMS)",
            ],
            rewards_received={
                "liquid_GEMS": 47537.5217,
                "net_worth_GEMS": 60802.5217,
                "XP": 34445.0,
                "level": 15.0,
                "mining_power": 260.0,
            },
            is_reward_monetizable_onchain=False,
            cash_out_proceeds_usd=0.0,
            direct_costs_usd=0.0,
            gas_and_protocol_fees_usd=0.0,
            execution_costs_usd=0.0,
            net_realized_pnl_usd=0.0,
            net_realized_pnl_eth=0.0,
            independent_verification_method=(
                "Authenticated GET https://gemapi.bullshot.io/api/user/me & POST /api/mining/positions/:id/claim "
                "(User ID 3523304; reports/live_verification_snapshot.json)"
            ),
            status="VERIFIED_REWARD",
            notes=(
                "Zero-gas off-chain accumulation verified (+47,537.52 liquid GEMS). Valued at $0.00 realized USD "
                "income because gem.bullshot.io has no on-chain token withdrawal or redemption contract live yet."
            ),
        ),
        EarningsLedgerEntry(
            experiment_id="EXP-06-GEM-RWA-STOCK-BUY-ENDPOINT-PROBE",
            timestamp="2026-11-08T19:15:24Z",
            chain="Off-Chain Backend (gemapi.bullshot.io)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[],
            initial_balances={"GEMS": 47499.7217},
            outside_deposits={"ETH": 0.0},
            operations_performed=[
                "GET /api/rwa/stocks (returned HTTP 200 with 8 synthetic stocks: NVDAX, AAPLX, TSLAX, MSFTX, AMZNX, METAX, GOOGLX, COIN)",
                "POST /api/rwa/stocks/NVDAX/buy with {symbol: 'NVDAX', amountGems: 100}",
            ],
            rewards_received={},
            is_reward_monetizable_onchain=False,
            cash_out_proceeds_usd=0.0,
            direct_costs_usd=0.0,
            gas_and_protocol_fees_usd=0.0,
            execution_costs_usd=0.0,
            net_realized_pnl_usd=0.0,
            net_realized_pnl_eth=0.0,
            independent_verification_method=(
                "Live HTTP probe to https://gemapi.bullshot.io/api/rwa/stocks/NVDAX/buy -> HTTP 404 "
                "{'error': 'Endpoint not found: POST /api/rwa/stocks/NVDAX/buy', 'code': 404}"
            ),
            status="FAILED",
            notes="Backend route POST /api/rwa/stocks/:symbol/buy is unimplemented (HTTP 404); zero GEMS deducted.",
        ),
    ]
    return BullshotEarningsLedger(entries)
