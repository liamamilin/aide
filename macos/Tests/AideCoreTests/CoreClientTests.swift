import Foundation
import XCTest
@testable import AideCore

final class CoreClientTests: XCTestCase {
    private func makeClient() throws -> (CoreClient, URL) { try makeCore() }

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

final class CoreRestartTests: XCTestCase {
    func testCoreComesBackOnTheNextRequestAfterItDies() async throws {
        let (core, dataDir) = try makeCore()
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()
        _ = try await core.call("ping")
        let running = await core.processIdentifier
        let pid = try XCTUnwrap(running)
        kill(pid, SIGKILL)
        for _ in 0..<100 {
            if await core.processIdentifier == nil { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }

        let result = try await core.call("ping")   // lazily restarts
        XCTAssertEqual(result["pong"], .bool(true))
        let newPID = await core.processIdentifier
        XCTAssertNotEqual(newPID, pid)
        await core.stop()
    }

    func testNoRestartAfterAnExplicitStop() async throws {
        let (core, dataDir) = try makeCore()
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()
        await core.stop()
        do { _ = try await core.call("ping"); XCTFail("restarted after stop") }
        catch { XCTAssertEqual(error as? CoreError, .notRunning) }
    }
}
