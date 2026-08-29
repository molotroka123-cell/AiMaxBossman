// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "BossmanRemoteKit",
    products: [.library(name: "BossmanRemoteKit", targets: ["BossmanRemoteKit"])],
    targets: [
        .target(name: "BossmanRemoteKit"),
        .testTarget(name: "BossmanRemoteKitTests", dependencies: ["BossmanRemoteKit"])
    ]
)
