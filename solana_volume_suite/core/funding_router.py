import math
import random
from typing import List, Dict, Any, Optional
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
from solders.instruction import Instruction


class CEXWithdrawalAdapter:
    """
    Simulates / abstracts external CEX withdrawals (e.g. Binance / Bybit hot wallet).
    Funding via CEX completely severs the on-chain graph from any master funding address.
    """

    def __init__(self, exchange_name: str = "binance_main"):
        self.exchange_name = exchange_name

    def plan_cex_batch_withdrawal(
        self,
        sub_wallet_addresses: List[str],
        total_sol: float
    ) -> List[Dict[str, Any]]:
        count = len(sub_wallet_addresses)
        base_per_wallet = total_sol / count
        plans = []
        for addr in sub_wallet_addresses:
            jitter = (random.random() - 0.5) * 0.1 * base_per_wallet
            amount = round(base_per_wallet + jitter, 4)
            plans.append({
                "exchange": self.exchange_name,
                "recipient": addr,
                "amount_sol": amount,
                "currency": "SOL",
                "status": "QUEUED_API_CALL"
            })
        return plans


class FundingRouter:
    """
    Anti-Clustering & Anti-Bubblemaps Cascade Engine.
    Enforces hierarchical tree funding:
      Level 0: Master wallet (CEX / cold)
      Level 1: 3-5 Transit wallets (graph severance)
      Level 2: 10-15 Sub-wallets per transit node (30-50 total)
    """

    def __init__(self, master_wallet: str, cascade_depth: int = 2):
        self.master = master_wallet
        self.cascade_depth = cascade_depth

    def generate_cascade_structure(self, total_subwallets: int) -> Dict[str, Any]:
        """
        Generates 2-tier tree structure:
        Master -> 3-5 Transit -> Sub-wallets distributed evenly.
        """
        transit_count = 3
        sub_per_transit = max(1, total_subwallets // transit_count)

        structure = {
            "master": self.master,
            "transit_wallets": [],
            "sub_wallets": {},
            "total_subwallets": total_subwallets
        }

        # Generate Level 1 transit keypairs
        for _ in range(transit_count):
            transit_kp = Keypair()
            structure["transit_wallets"].append(str(transit_kp.pubkey()))

        # Generate Level 2 sub-wallets per transit node
        created_count = 0
        for idx, transit_addr in enumerate(structure["transit_wallets"]):
            structure["sub_wallets"][transit_addr] = []
            count_for_this = sub_per_transit if idx < transit_count - 1 else (total_subwallets - created_count)
            for _ in range(count_for_this):
                sub_kp = Keypair()
                structure["sub_wallets"][transit_addr].append(str(sub_kp.pubkey()))
                created_count += 1

        return structure

    @staticmethod
    def detect_direct_fanout_violation(
        funding_txs: List[Dict[str, Any]],
        max_allowed_direct_links: int = 5
    ) -> bool:
        """
        Pattern Detector:
        Returns True if a 1-to-N direct fanout violation is detected (e.g. Master directly sends to > 5 wallets).
        """
        sender_counts: Dict[str, int] = {}
        for tx in funding_txs:
            sender = tx.get("from") or tx.get("from_pubkey") or tx.get("sender")
            if sender:
                sender_counts[sender] = sender_counts.get(sender, 0) + 1
                if sender_counts[sender] > max_allowed_direct_links:
                    return True
        return False

    def calculate_pareto_amount(self, base_sol: float = 0.1, alpha: float = 1.6) -> float:
        """
        Generates Pareto-distributed trading volume with noise.
        Strictly prevents round numbers (0.1, 0.5, 1.0 SOL).
        """
        pareto_sample = random.paretovariate(alpha=alpha)
        noise = random.uniform(0.0123, 0.0543)
        amount = (pareto_sample * base_sol) + noise
        # Round to 4 decimal places
        val = round(amount, 4)
        # Invariant: reject exact round tenths
        if abs(val * 10 - round(val * 10)) < 1e-4:
            val += 0.0037
        return round(val, 4)

    def calculate_poisson_delay(
        self,
        lambda_rate: float = 20.0,
        min_sec: float = 3.0,
        max_sec: float = 120.0
    ) -> float:
        """
        Generates Poisson-distributed inter-transaction delays (bounded between min_sec and max_sec).
        """
        u = random.random()
        u = max(1e-6, min(1.0 - 1e-6, u))
        inter_arrival = -math.log(1.0 - u) * lambda_rate
        jitter = random.uniform(0.1, 0.9)
        clamped = max(min_sec, min(max_sec, inter_arrival + jitter))
        return round(clamped, 2)


class AntiClusteringFundingRouter(FundingRouter):
    """
    Backwards-compatible subclass with rotation ledger and explicit helper methods.
    """

    def __init__(self, transit_count: int = 3, master_wallet: str = "MasterWalletDefault"):
        super().__init__(master_wallet=master_wallet, cascade_depth=2)
        self.transit_count = transit_count
        self.rotation_ledger: Dict[str, List[Dict[str, Any]]] = {}

    @staticmethod
    def generate_pareto_volume(
        min_sol: float = 0.035,
        max_sol: float = 1.85,
        alpha: float = 1.75
    ) -> float:
        raw = (random.paretovariate(alpha) - 1.0) * (max_sol - min_sol) * 0.35 + min_sol
        clamped = max(min_sol, min(max_sol, raw))
        noise = random.uniform(0.0031, 0.0099)
        val = round(clamped + noise, 4)
        if abs(val - round(val, 1)) < 0.0015:
            val += 0.0037
        return round(val, 4)

    @staticmethod
    def generate_poisson_interval(
        lam: float = 18.0,
        min_sec: float = 4.0,
        max_sec: float = 95.0
    ) -> float:
        u = random.random()
        u = max(1e-6, min(1.0 - 1e-6, u))
        inter_arrival = -math.log(1.0 - u) * lam
        jitter = random.uniform(0.1, 0.9)
        clamped = max(min_sec, min(max_sec, inter_arrival + jitter))
        return round(clamped, 2)

    def plan_cascaded_funding(
        self,
        master_kp: Keypair,
        sub_wallet_pubkeys: List[Pubkey],
        total_sol: float
    ) -> Dict[str, Any]:
        if not sub_wallet_pubkeys:
            return {"transit_keypairs": [], "tier1_transfers": [], "tier2_transfers": []}

        transit_kps = [Keypair() for _ in range(self.transit_count)]
        tier1_transfers = []
        tier2_transfers = []

        shards: List[List[Pubkey]] = [[] for _ in range(self.transit_count)]
        for idx, pk in enumerate(sub_wallet_pubkeys):
            shards[idx % self.transit_count].append(pk)

        total_lamports = int(total_sol * 1_000_000_000)
        portion_lamports = total_lamports // self.transit_count

        for transit_kp in transit_kps:
            ix = transfer(
                TransferParams(
                    from_pubkey=master_kp.pubkey(),
                    to_pubkey=transit_kp.pubkey(),
                    lamports=portion_lamports
                )
            )
            tier1_transfers.append({
                "from": str(master_kp.pubkey()),
                "to": str(transit_kp.pubkey()),
                "lamports": portion_lamports,
                "instruction": ix
            })

        for t_idx, shard in enumerate(shards):
            if not shard:
                continue
            transit_kp = transit_kps[t_idx]
            lamports_per_sub = (portion_lamports - 100_000) // len(shard)
            for sub_pk in shard:
                delta = int(random.uniform(-50_000, 50_000))
                send_amt = max(10_000, lamports_per_sub + delta)
                ix = transfer(
                    TransferParams(
                        from_pubkey=transit_kp.pubkey(),
                        to_pubkey=sub_pk,
                        lamports=send_amt
                    )
                )
                tier2_transfers.append({
                    "transit_index": t_idx,
                    "from": str(transit_kp.pubkey()),
                    "to": str(sub_pk),
                    "lamports": send_amt,
                    "instruction": ix
                })

        return {
            "transit_keypairs": transit_kps,
            "tier1_transfers": tier1_transfers,
            "tier2_transfers": tier2_transfers
        }

    def register_rotation_step(
        self,
        token_mint: str,
        buyer_index: int,
        amount_tokens: int,
        available_wallet_indices: List[int]
    ) -> Dict[str, Any]:
        candidates = [idx for idx in available_wallet_indices if idx != buyer_index]
        if len(candidates) < 2:
            wallet_b = buyer_index
            wallet_c = buyer_index
        else:
            wallet_b = random.choice(candidates)
            candidates_c = [idx for idx in candidates if idx != wallet_b]
            wallet_c = random.choice(candidates_c) if candidates_c else candidates[0]

        sell_via_b = int(amount_tokens * 0.40)
        rotate_to_c = int(amount_tokens * 0.35)
        hold_in_a = amount_tokens - sell_via_b - rotate_to_c

        plan = {
            "token_mint": token_mint,
            "buyer_wallet": buyer_index,
            "seller_wallet_b": wallet_b,
            "transfer_target_c": wallet_c,
            "tokens_bought": amount_tokens,
            "tokens_to_sell_b": sell_via_b,
            "tokens_to_transfer_c": rotate_to_c,
            "tokens_held_a": hold_in_a,
            "min_blocks_delay": random.randint(3, 12)
        }

        if token_mint not in self.rotation_ledger:
            self.rotation_ledger[token_mint] = []
        self.rotation_ledger[token_mint].append(plan)

        return plan
