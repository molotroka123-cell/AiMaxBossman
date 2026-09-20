# KNOWN LIMITATIONS

These limitations must be read together with the exact candidate certificate.

- Real local-model certification requires the owner's Windows machine and selected local runtime/model.
- Real Telegram delivery requires the owner's authorized Telegram configuration; mock transport proves only the contract.
- External services without idempotency guarantees require reconciliation after uncertain outcomes; Bossman must not blindly retry.
- Human-only CAPTCHA/BankID/eGovernment/biometric checkpoints require owner takeover.
- Image generation depends on a configured real provider; native Image Studio editing is independent of provider credentials.
- A clean Windows artifact must be tested outside a source checkout before release.
- Intelligence-retention evidence is fail-closed when its current measurement artifact is absent.

No item above may be used to reclassify missing implementation code as hardware-required.
