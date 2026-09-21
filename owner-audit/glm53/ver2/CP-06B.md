# CP-06B — Routing and model failure boundaries

Only the two requested local Qwen Vulkan servers were left resident: MAIN 8081 and FAST 8082. The extra 8083 process was stopped.

With FAST stopped, `/api/router/preview` remained responsive and selected MAIN. With MAIN stopped, the same safe preview remained responsive and did not claim a generated success; both servers were restored and `/health` returned `{"status":"ok"}`.

Prior independent model probes remain authoritative: MAIN native generation about 10 tok/s, FAST about 49 tok/s; tools, JSON, vision and long context passed. Qwen3.6 retains the separate arithmetic model-quality caveat. No cloud provider was created or used.
