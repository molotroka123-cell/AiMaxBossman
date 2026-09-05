import asyncio
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from bossman_shared.reality import *


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state.db'
        self.now = 1000
        self.clock = lambda: self.now
        self.p = Constitution('1', allowed_actions=('write',), allowed_targets=('target',), verifiers=('reader',))
        self.m = Mission('m1', '1', 'write true', 'agent', self.p.fingerprint,
            (Obligation('o1', 'target', digest(True), 'reader', 30),),
            (Effect('e1', 'target', 'write', digest({'value': True}), 'o1', 'test', 'read'),), 100)
        self.a = ProofAuthority({'reader': b'k' * 32}, {'reader': 'trusted-host'}, clock=self.clock)
        self.s = RealityStore(self.path, clock=self.clock)
        self.s.register(self.m)

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def receipt(self, mission=None, binding=''):
        return self.a.observe(mission or self.m, 'o1', lambda _: True, dispatch_binding=binding)

    def confirmed(self):
        f = self.s.claim(self.m, 'e1', 'worker')
        r = self.receipt(binding=digest([self.m.fingerprint, 'e1', f]))
        self.s.confirm(self.m, 'e1', 'worker', f, r, self.a)
        return r

    def test_complete_requires_all_proofs(self):
        with self.assertRaises(RealityError): self.s.completion(self.m, self.a)
        self.confirmed()
        self.assertEqual(self.s.completion(self.m, self.a)['verdict'], 'PASS')

    def test_receipt_overwrite_cannot_erase_effect_binding(self):
        self.confirmed()
        self.s.put_receipt(self.m, self.receipt(), self.a)
        with self.assertRaises(RealityError): self.s.completion(self.m, self.a)

    def test_real_process_crash_after_claim(self):
        import subprocess, sys, json
        code = """import json, os, sys
from bossman_shared.reality import RealityCompiler, RealityStore
m=RealityCompiler().compile(json.loads(sys.argv[2]))
s=RealityStore(sys.argv[1])
s.claim(m, 'e1', 'crashed-process')
os._exit(23)
"""
        result = subprocess.run([sys.executable, '-c', code, str(self.path), json.dumps(asdict(self.m))])
        self.assertEqual(result.returncode, 23)
        with self.assertRaises(RealityError): self.s.claim(self.m, 'e1', 'new-process')

    def test_same_timestamp_pre_dispatch_receipt_rejected(self):
        r = self.receipt()
        f = self.s.claim(self.m, 'e1', 'worker')
        with self.assertRaises(RealityError): self.s.confirm(self.m, 'e1', 'worker', f, r, self.a)

    def test_cross_mission_replay(self):
        with self.assertRaises(RealityError): self.a.check(replace(self.m, id='other'), self.receipt())

    def test_cross_run_replay(self):
        with self.assertRaises(RealityError): self.a.check(replace(self.m, run_id='2'), self.receipt())

    def test_expected_value_replay(self):
        changed = replace(self.m, obligations=(replace(self.m.obligations[0], expected_digest=digest(False)),))
        with self.assertRaises(RealityError): self.a.check(changed, self.receipt())

    def test_tampered_signature(self):
        with self.assertRaises(RealityError): self.a.check(self.m, replace(self.receipt(), signature='0' * 64))

    def test_epistemic_labels_never_promote(self):
        for k in Knowledge:
            if k != Knowledge.VERIFIED:
                with self.subTest(k=k), self.assertRaises(RealityError):
                    self.a.check(self.m, replace(self.receipt(), knowledge=k.value))

    def test_observer_mismatch(self):
        with self.assertRaises(RealityError): self.a.observe(self.m, 'o1', lambda _: False)

    def test_independent_effective_identity(self):
        a = ProofAuthority({'reader': b'k' * 32}, {'reader': 'agent'}, clock=self.clock)
        with self.assertRaises(RealityError): a.observe(self.m, 'o1', lambda _: True)

    def test_stale_and_future_evidence(self):
        r = self.receipt()
        for now in (999, 1031):
            self.now = now
            with self.assertRaises(RealityError): self.a.check(self.m, r)

    def test_stale_after_confirmation_blocks_done(self):
        self.confirmed()
        self.now += 31
        with self.assertRaises(RealityError): self.s.completion(self.m, self.a)

    def test_resume_full_plan_identity(self):
        for m in (replace(self.m, intent='changed'), replace(self.m, run_id='2'), replace(self.m, budget_microusd=101)):
            with self.assertRaises(RealityError): self.s.register(m)

    def test_restart_escrow_no_duplicate(self):
        self.s.claim(self.m, 'e1', 'worker')
        self.s.close()
        self.s = RealityStore(self.path, clock=self.clock)
        with self.assertRaises(RealityError): self.s.claim(self.m, 'e1', 'other')

    def test_stale_owner_and_fence(self):
        f = self.s.claim(self.m, 'e1', 'worker')
        r = self.receipt(binding=digest([self.m.fingerprint, 'e1', f]))
        for owner, fence in (('other', f), ('worker', f + 1)):
            with self.assertRaises(RealityError): self.s.confirm(self.m, 'e1', owner, fence, r, self.a)

    def test_absence_without_terminal_attempt_is_manual(self):
        f = self.s.claim(self.m, 'e1', 'w')
        state = self.s.reconcile_absent(self.m, 'e1', 'w', f, absence_verified=True,
                                      prior_attempt_terminal=False, reference='host-reconciliation')
        self.assertEqual(state, 'MANUAL_REVIEW_REQUIRED')
        with self.assertRaises(RealityError): self.s.claim(self.m, 'e1', 'w')

    def test_authoritative_safe_retry_advances_fence(self):
        f = self.s.claim(self.m, 'e1', 'w')
        self.assertEqual(self.s.reconcile_absent(self.m, 'e1', 'w', f, absence_verified=True,
                         prior_attempt_terminal=True, reference='terminal-provider-status'), 'SAFE_TO_RETRY')
        self.assertEqual(self.s.claim(self.m, 'e1', 'w'), f + 1)

    def test_expired_uncertainty_is_manual(self):
        f = self.s.claim(self.m, 'e1', 'w')
        self.now += 301
        self.assertEqual(self.s.reconcile_absent(self.m, 'e1', 'w', f, absence_verified=True,
                         prior_attempt_terminal=True, reference='terminal'), 'MANUAL_REVIEW_REQUIRED')

    def test_budget_idempotent_and_persistent(self):
        self.s.reserve(self.m, 'a', 60)
        self.s.reserve(self.m, 'a', 60)
        with self.assertRaises(RealityError): self.s.reserve(self.m, 'a', 61)
        with self.assertRaises(RealityError): self.s.reserve(self.m, 'b', 41)
        self.s.close()
        self.s = RealityStore(self.path, clock=self.clock)
        with self.assertRaises(RealityError): self.s.reserve(self.m, 'b', 41)

    def test_negative_bool_budget_rejected(self):
        for amount in (-1, True, 1.2):
            with self.assertRaises(RealityError): self.s.reserve(self.m, 'x', amount)

    def test_concurrent_claim_only_one_winner(self):
        def claim(_):
            s = RealityStore(self.path, clock=self.clock)
            try:
                s.claim(self.m, 'e1', 'worker')
                return 1
            except RealityError: return 0
            finally: s.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(claim, range(4))), 1)

    def test_concurrent_budget_cannot_overspend(self):
        def reserve(i):
            s = RealityStore(self.path, clock=self.clock)
            try:
                s.reserve(self.m, str(i), 60)
                return 1
            except RealityError: return 0
            finally: s.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(4))), 1)

    def test_policy_binding_and_privacy(self):
        with self.assertRaises(RealityError): self.p.admit(self.m, current_level=1, cloud=True)
        with self.assertRaises(RealityError): replace(self.p, version='2').admit(self.m, current_level=1)
        with self.assertRaises(RealityError): self.p.admit(self.m, current_level=0)

    def test_compiler_strict_schema(self):
        p = asdict(self.m)
        self.assertEqual(RealityCompiler().compile(p), self.m)
        for change in ({'unknown': 1}, {'obligations': []}, {'budget_microusd': True}):
            with self.assertRaises(RealityError): RealityCompiler().compile(p | change)

    def test_duplicate_obligation_rejected(self):
        with self.assertRaises(RealityError): replace(self.m, obligations=self.m.obligations * 2)

    def test_effect_target_binding(self):
        with self.assertRaises(RealityError): replace(self.m, effects=(replace(self.m.effects[0], target='elsewhere'),))

    def runtime(self, action, observer=lambda _: True, fence=lambda *a: True):
        return RealityRuntime(self.s, self.p, self.a, observers={'reader': observer},
            actions={'write': action}, fence_check=fence, level_provider=lambda: 1)

    def test_runtime_end_to_end(self):
        world = {'value': False}
        r = self.runtime(lambda m,e,args: world.update(args), lambda _: world['value'])
        r.admit(self.m)
        r.execute(self.m, 'e1', {'value': True}, worker='w')
        self.assertEqual(r.complete(self.m)['verdict'], 'PASS')

    def test_action_crash_does_not_repeat(self):
        calls = []
        def action(*args):
            calls.append('side effect')
            raise RuntimeError('crash after effect')
        r = self.runtime(action)
        with self.assertRaises(RuntimeError): r.execute(self.m, 'e1', {'value': True}, worker='w')
        with self.assertRaises(RealityError): r.execute(self.m, 'e1', {'value': True}, worker='w')
        self.assertEqual(len(calls), 1)

    def test_fenced_out_no_dispatch(self):
        calls=[]
        r=self.runtime(lambda *a: calls.append(1), fence=lambda *a: False)
        with self.assertRaises(RealityError): r.execute(self.m, 'e1', {'value': True}, worker='w')
        self.assertFalse(calls)

    def test_changed_arguments_no_dispatch(self):
        calls=[]
        with self.assertRaises(RealityError): self.runtime(lambda *a: calls.append(1)).execute(self.m, 'e1', {'value': False}, worker='w')
        self.assertFalse(calls)

    def test_hook_does_not_trust_answer(self):
        hook = make_completion_hook(self.runtime(lambda *a: None), lambda task, run: self.m)
        self.assertEqual(asyncio.run(hook({}, 1, 'everything done!'))['verdict'], 'FAIL')
        self.confirmed()
        self.assertEqual(asyncio.run(hook({}, 1, ''))['verdict'], 'PASS')
        self.assertEqual(asyncio.run(hook({}, 2, ''))['verdict'], 'FAIL')

    def test_hook_missing_ir_fails_closed(self):
        hook=make_completion_hook(self.runtime(lambda *a: None), lambda *a: None)
        self.assertEqual(asyncio.run(hook({}, 1, 'done'))['verdict'], 'FAIL')


class SupportTests(unittest.TestCase):
    def test_autonomy_cannot_raise_owner_ceiling(self):
        self.assertEqual(autonomy_level(owner_ceiling=1, reliability=1, verifiability=1,
           reversibility=1, confidence=1, historical_success=1, risk=0), 1)

    def test_divergence_restricts(self):
        self.assertEqual(autonomy_level(owner_ceiling=4, reliability=1, verifiability=1,
           reversibility=1, confidence=1, historical_success=1, risk=0, divergence=True), 1)

    def test_nan_rejected(self):
        with self.assertRaises(RealityError): Bid('a', float('nan'), 0, 1, 0, 1)

    def test_twin_missing_extra_and_changed(self):
        t=compare_world({'x': 1,'y':2}, {'x':2,'z':3})
        self.assertEqual(t.unexpected_keys, ('x','z'))
        self.assertEqual(t.missing_keys, ('y',))
        self.assertTrue(t.divergent)

    def test_memory_dependencies_and_privacy(self):
        a=Fact('a','secret','source','mission',expires_at=100)
        b=Fact('b','result','source','mission',depends_on=('a',),expires_at=100,privacy='PUBLIC')
        c=MemoryCompiler([a,b])
        self.assertEqual([f.id for f in c.slice(['b'], now=1)], ['a','b'])
        with self.assertRaises(RealityError): c.slice(['b'],now=1,cloud=True)
        with self.assertRaises(RealityError): c.slice(['b'],now=101)
        with self.assertRaises(RealityError): c.slice(['b'],now=1,max_characters=1)

    def test_memory_cycle_and_superseded(self):
        a=Fact('a','x','s','m',depends_on=('b',),expires_at=100)
        b=Fact('b','x','s','m',depends_on=('a',),expires_at=100)
        with self.assertRaises(RealityError): MemoryCompiler([a,b]).slice(['a'],now=1)
        b=replace(b,depends_on=(),supersedes=('a',))
        with self.assertRaises(RealityError): MemoryCompiler([a,b]).slice(['a'],now=1)

    def test_quarantine_and_settlement_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'learn.db'
            db=LearningLedger(path)
            b=Bid('bad',0.99,0,10,0,1)
            for i in range(3): db.settle(str(i),b,verified_success=False,false_success=True)
            db.close()
            db=LearningLedger(path)
            self.assertTrue(db.reputation('bad')['quarantined'])
            with self.assertRaises(RealityError): db.choose([b],budget_microusd=1)
            with self.assertRaises(RealityError): db.settle('0',b,verified_success=True)
            db.close()

    def test_router_filters_private_cloud_and_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=LearningLedger(Path(tmp)/'l.db')
            cloud=Bid('cloud',1,1,1,0,1,local=False)
            local=Bid('local',0.8,0,100,0,1)
            self.assertEqual(db.choose([cloud,local],budget_microusd=10).route,'local')
            with self.assertRaises(RealityError): db.choose([cloud],budget_microusd=0,privacy='PUBLIC')
            db.close()

    def test_cause_stays_inferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=LearningLedger(Path(tmp)/'l.db')
            r=db.record_lesson('m',context_digest='x',action_digest='y',expected={'a':1},observed={'a':2},
                               cause_hypothesis='possible timing',lesson='check timing')
            self.assertEqual(r['cause_knowledge'],'INFERRED')
            db.close()

    def test_benchmark_no_changed_goalposts(self):
        b={'suite_digest':'1','cases':{'a':True,'b':False},'hard_failures':[]}
        c={'suite_digest':'2','cases':{'a':True,'b':True},'hard_failures':[]}
        self.assertFalse(candidate_eligible(b,c,minimum_cases=2))
        c['suite_digest']='1'
        self.assertTrue(candidate_eligible(b,c,minimum_cases=2))
        c['hard_failures']=['permission bypass']
        self.assertFalse(candidate_eligible(b,c,minimum_cases=2))


if __name__ == '__main__': unittest.main()
