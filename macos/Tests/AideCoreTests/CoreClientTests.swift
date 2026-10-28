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
