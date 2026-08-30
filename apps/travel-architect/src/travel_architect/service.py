
from __future__ import annotations
from .models import JOB_TYPES
from .store import SQLiteStore

class JobService:
    def __init__(self, app_id: str):
        self.store = SQLiteStore(app_id)

    def create(self, req):
        if req.type not in JOB_TYPES:
            raise ValueError(f"unsupported job type: {req.type}")
        return self.store.job_create(req.type, req.params, getattr(req, "idempotency_key", None))

    def list(self): return self.store.job_list()
    def get(self, job_id): return self.store.job_get(job_id)
    def cancel(self, job_id): return self.store.job_cancel(job_id)
    def metrics(self): return self.store.metrics()
