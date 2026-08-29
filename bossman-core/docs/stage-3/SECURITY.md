# Gateway security notes

- Default listen address is loopback.
- Never store production client/provider keys in `gateway.yaml`.
- Use environment or secret manager.
- Never log Authorization values.
- Separate client authorization from model/provider authorization.
- Alias allowlists reduce accidental access but are not a replacement for agent permissions/approval policy.
- Provider fallback must later become sensitivity-aware: private/local-only context cannot silently leave the machine.
- Static bearer keys are an integration mechanism. The private iPhone client should use device enrollment + short-lived sessions + revocation.
- Do not expose Ollama/LM Studio raw ports externally when Gateway is the intended boundary.
