// swift-tools-version:5.9
import PackageDescription

// Native shell for Aide. AideCore talks to the Python core; AideShell is the AppKit/SwiftUI
// layer (testable); Aide is the thin executable.
let package = Package(
    name: "Aide",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "AideCore", targets: ["AideCore"]),
        .executable(name: "Aide", targets: ["Aide"]),
    ],
    targets: [
        .target(name: "AideCore"),
        .target(name: "AideShell", dependencies: ["AideCore"]),
        .executableTarget(name: "Aide", dependencies: ["AideShell", "AideCore"]),
        .testTarget(name: "AideCoreTests", dependencies: ["AideCore"]),
        .testTarget(name: "AideShellTests", dependencies: ["AideShell", "AideCore"]),
    ]
)
