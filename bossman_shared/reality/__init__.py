"""Bossman Reality Compiler v0.1.0; host-integrated safety kernel."""
from .contracts import (Effect, Knowledge, Mission, Obligation, RealityCompiler,
                        RealityError, canonical, digest)
from .policy import Constitution, autonomy_level
from .proof import ProofAuthority, Receipt
from .store import RealityStore
from .runtime import RealityRuntime, make_completion_hook
from .intelligence import (Bid, Fact, LearningLedger, MemoryCompiler,
                           candidate_eligible, compare_world)
__version__ = '0.1.0'
