# Native SwiftUI client

The native client uses the same `/remote/*` contract as the installable PWA, but stores the session token in iOS Keychain (`WhenUnlockedThisDeviceOnly`) and refuses non-HTTPS endpoints.

Build on macOS:

```bash
brew install xcodegen
cd ios/BossmanRemoteApp
xcodegen generate
open BossmanRemoteApp.xcodeproj
```

Choose your Apple Development Team, build to your iPhone. No App Store or external SDK is required.

`BossmanRemoteKit` is a dependency-free Swift package; run its portable tests with `swift test`.
