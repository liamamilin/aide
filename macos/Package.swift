// swift-tools-version:5.9
import PackageDescription

// Native shell for Aide. P0a: the core client only; the app target arrives with P0b.
let package = Package(
    name: "Aide",
    platforms: [.macOS(.v14)],
    products: [.library(name: "AideCore", targets: ["AideCore"])],
    targets: [
        .target(name: "AideCore"),
        .testTarget(name: "AideCoreTests", dependencies: ["AideCore"]),
    ]
)
