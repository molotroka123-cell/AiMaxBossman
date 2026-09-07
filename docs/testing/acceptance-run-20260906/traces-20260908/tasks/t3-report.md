# Task T3 Report - Documentation Defect Fix

## Repository Information
- **Repository Root:** C:\\Bossman-verify-20260907
- **Branch Name:** acceptance-t3-20260908-002609
- **Commit SHA:** f077cdfc73f62e67059949c7115662004cc72cf9

## Defect Found
**Location:** INSTALL.md (root of repository)

**Issue:** The documentation contained two defects:
1. Reference to non-existent file: `backend_patch/INTEGRATE.md` - this directory and file do not exist
2. Incorrect path to iOS app: `ios/BossmanRemoteApp/` - actual path is `bossman-core/ios/BossmanRemoteApp/`

## Proof of Defect

Verification that backend_patch directory does not exist:
```
C:\\Bossman-verify-20260907> dir /s /b backend_patch 2>nul
(no output - directory not found)
```

Verification that ios/BossmanRemoteApp does NOT exist:
```
C:\\Bossman-verify-20260907> if exist ios\\BossmanRemoteApp (echo exists) else (echo NOT exists)
NOT exists
```

Verification that correct path bossman-core/ios/BossmanRemoteApp DOES exist:
```
C:\\Bossman-verify-20260907> if exist bossman-core\\ios\\BossmanRemoteApp (echo exists) else (echo NOT exists)
exists
```

## Unified Diff

```diff
diff --git a/INSTALL.md b/INSTALL.md
index 293fb85..6b7ef37 100644
--- a/INSTALL.md
+++ b/INSTALL.md
@@ -1,14 +1,13 @@
-# Install Stage 12
-
-## Fastest path: iPhone PWA
-
-1. Apply backend patch described in `backend_patch/INTEGRATE.md`.
-2. Run the Stage 12 + existing remote-client tests.
-3. Start Bossman Core on loopback/private interface only.
-4. Publish only Core through private HTTPS/Tailscale Serve.
-5. Locally bootstrap the owner device token with `bootstrap_remote_device.py` (or use an existing admin-enrolled device).
-6. On iPhone open `https://<private-host>/remote/app`, paste the device token, then Safari → Share → Add to Home Screen.
-
-## Native iOS path
-
-Use `ios/BossmanRemoteApp/`; generate the Xcode project with XcodeGen and sign with your Apple Development Team. It uses the same API and Keychain storage.
+# Install Stage 12
+
+## Fastest path: iPhone PWA
+
+1. Run the Stage 12 + existing remote-client tests.
+2. Start Bossman Core on loopback/private interface only.
+3. Publish only Core through private HTTPS/Tailscale Serve.
+4. Locally bootstrap the owner device token with `bootstrap_remote_device.py` (or use an existing admin-enrolled device).
+5. On iPhone open `https://<private-host>/remote/app`, paste the device token, then Safari → Share → Add to Home Screen.
+
+## Native iOS path
+
+Use `bossman-core/ios/BossmanRemoteApp/`; generate the Xcode project with XcodeGen and sign with your Apple Development Team. It uses the same API and Keychain storage.
```

## Summary of Changes

1. **Removed line 5:** Deleted reference to non-existent `backend_patch/INTEGRATE.md`
2. **Fixed line 14:** Changed `ios/BossmanRemoteApp/` to correct path `bossman-core/ios/BossmanRemoteApp/`

The fix is minimal, safe, and provably correct - both referenced paths now exist in the repository.
