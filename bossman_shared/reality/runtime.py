"""Explicit host boundary. Registration, execution and completion are distinct."""
from .contracts import RealityError, digest


class RealityRuntime:
    def __init__(self, store, policy, authority, *, observers, actions, fence_check, level_provider):
        self.store, self.policy, self.authority = store, policy, authority
        self.observers, self.actions = dict(observers), dict(actions)
        self.fence_check, self.level_provider = fence_check, level_provider

    def admit(self, mission):
        self.policy.admit(mission, current_level=self.level_provider())
        if any(o.verifier not in self.observers for o in mission.obligations):
            raise RealityError('missing observer adapter')
        if any(e.action not in self.actions for e in mission.effects):
            raise RealityError('missing action adapter')
        self.store.register(mission)

    def observe(self, mission, oid):
        self.policy.admit(mission, current_level=self.level_provider())
        o = mission.obligation(oid)
        receipt = self.authority.observe(mission, oid, self.observers[o.verifier])
        self.store.put_receipt(mission, receipt, self.authority)
        return receipt

    def execute(self, mission, eid, arguments, *, worker):
        self.policy.admit(mission, current_level=self.level_provider())
        effect = mission.effect(eid)
        if digest(arguments) != effect.args_digest:
            raise RealityError('arguments changed after compilation')
        if effect.action not in self.actions:
            raise RealityError('unknown action adapter')
        fence = self.store.claim(mission, eid, worker)
        # Callback MUST revalidate actual Fleet owner/fence immediately before IO.
        # DB fences cannot revoke an already in-flight network request.
        if self.fence_check(mission, effect, worker, fence) is not True:
            raise RealityError('fenced out; escrow retained')
        # Pass the full effect and mission: host adapter derives a stable provider
        # idempotency key from mission.run_id + effect.id + domain, NOT fence.
        self.actions[effect.action](mission, effect, arguments)
        o = mission.obligation(effect.obligation_id)
        receipt = self.authority.observe(mission, o.id, self.observers[o.verifier],
                                         dispatch_binding=digest([mission.fingerprint, eid, fence]))
        self.store.confirm(mission, eid, worker, fence, receipt, self.authority)
        return receipt

    def complete(self, mission):
        self.policy.admit(mission, current_level=self.level_provider())
        return self.store.completion(mission, self.authority)


def make_completion_hook(runtime, mission_loader):
    """Matches inspected BCC (task, run_id, answer) hook signature.

    mission_loader must load persisted IR by run_id with an authenticated binding.
    No model answer is used as evidence, and absent IR is FAIL, not N/A.
    """
    async def gate_completion(task, run_id, answer):
        try:
            mission = mission_loader(task, run_id)
            if mission.run_id != str(run_id):
                raise RealityError('run binding mismatch')
            return runtime.complete(mission)
        except Exception:
            # Do not leak exception contents or silently allow completion.
            return {'verdict': 'FAIL', 'requeue': False, 'reason': 'reality_proof_incomplete'}
    return gate_completion
