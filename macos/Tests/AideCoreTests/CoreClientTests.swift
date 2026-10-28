import Foundation
import XCTest
@testable import AideCore

final class CoreClientTests: XCTestCase {
    /// Repo root: macos/Tests/AideCoreTests/<this file> -> up four levels.
    private var repoRoot: URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
    }

    private func makeClient() throws -> (CoreClient, URL) {
        let dataDir = FileManager.default.temporaryDirectory.appendingPathComponent("aide-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        let python = ProcessInfo.processInfo.environment["AIDE_PYTHON"] ?? "python3"
        let client = CoreClient(launch: .init(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: [python, "-m", "ai_desktop.core"],
            environment: ["AIDE_DATA_DIR": dataDir.path, "PYTHONPATH": repoRoot.path]))
        return (client, dataDir)
    }

    func testReadyPingAndHealth() async throws {
        let (client, dataDir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await client.start()

        var iterator = client.events.makeAsyncIterator()
        let ready = await iterator.next()
        XCTAssertEqual(ready?.name, "core.ready")

        let pong = try await client.call("ping")
        XCTAssertEqual(pong["pong"]?.boolValue, true)

        let health = try await client.call("health")
        XCTAssertEqual(health["qt_loaded"]?.boolValue, false)
        XCTAssertEqual(health["data_dir"]?.stringValue, dataDir.path)
        await client.stop()
    }

    func testUnknownMethodSurfacesRemoteError() async throws {
        let (client, dataDir) = try makeClient()
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await client.start()
        do {
            _ = try await client.call("nope")
            XCTFail("expected error")
        } catch let CoreError.remote(code, _) {
            XCTAssertEqual(code, "unknown_method")
        }
        await client.stop()
    }
}
