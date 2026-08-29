# Resource-aware routing hooks

Stage 3 intentionally implements the boundary needed by the next Resource Brain without pretending it can accurately measure every runtime today.

The router already owns the selection point. Stage 4 can add a scorer with signals such as free unified memory, resident models, queue depth, model cold-start cost, KV cache, task priority, energy/temperature and disk pressure.

Do not put hardware-specific AMD logic into every agent. Resource Brain should publish normalized state and the Gateway should consume it.

Suggested future route score:

```text
priority
+ unhealthy_penalty
+ queue_penalty
+ cold_start_penalty
+ memory_pressure_penalty
+ capability_penalty (infinite if incompatible)
+ policy/cost penalty
```

Cloud fallback remains opt-in and must never be selected for a data class that policy marks local-only.
