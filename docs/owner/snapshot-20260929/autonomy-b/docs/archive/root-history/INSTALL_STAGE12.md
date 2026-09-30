# Install Stage 12

## Fastest path: iPhone PWA

1. Apply backend patch described in `backend_patch/INTEGRATE.md`.
2. Run the Stage 12 + existing remote-client tests.
3. Start Bossman Core on loopback/private interface only.
4. Publish only Core through private HTTPS/Tailscale Serve.
5. Locally bootstrap the owner device token with `bootstrap_remote_device.py` (or use an existing admin-enrolled device).
6. On iPhone open `https://<private-host>/remote/app`, paste the device token, then Safari → Share → Add to Home Screen.

## Native iOS path

Use `ios/BossmanRemoteApp/`; generate the Xcode project with XcodeGen and sign with your Apple Development Team. It uses the same API and Keychain storage.
