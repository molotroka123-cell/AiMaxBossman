# Install Stage 12 
 
## Fastest path: iPhone PWA 
 
1. Run the Stage 12 + existing remote-client tests. 
2. Start Bossman Core on loopback/private interface only. 
3. Publish only Core through private HTTPS/Tailscale Serve. 
4. Locally bootstrap the owner device token with `bootstrap_remote_device.py` (or use an existing admin-enrolled device). 
5. On iPhone open `https://<private-host>/remote/app`, paste the device token, then Safari  Share  Add to Home Screen. 
 
## Native iOS path 
 
Use `bossman-core/ios/BossmanRemoteApp/`; generate the Xcode project with XcodeGen and sign with your Apple Development Team. It uses the same API and Keychain storage.
