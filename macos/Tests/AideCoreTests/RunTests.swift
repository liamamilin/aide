import XCTest
@testable import AideCore

final class RunTests: XCTestCase {
    func testTranslateStreamsDeltasThenFinishes() async throws {
        let fake = try FakeOllamaProcess(mode: "stream")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()

        let actions = try await core.actions()
        XCTAssertTrue(actions.contains { $0.id == "translate" })

        let updates = await collect(try await core.start(actionID: "translate", material: "你好"))
        let deltas = updates.compactMap { update -> String? in
            if case .delta("content", let text) = update { return text }
            return nil
        }
        XCTAssertEqual(deltas.joined(), "Hello")
        guard case .finished(let status, let text, _, _)? = updates.last else { return XCTFail("no finish") }
        XCTAssertEqual(status, "succeeded")
        XCTAssertEqual(text, "Hello")
        await core.stop()
    }

    func testCancelEndsAHeldRun() async throws {
        let fake = try FakeOllamaProcess(mode: "hold")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()

        let handle = try await core.start(actionID: "explain", material: "x")
        var iterator = handle.updates.makeAsyncIterator()
        guard case .delta(_, let first)? = await iterator.next() else { return XCTFail("no delta") }
        XCTAssertEqual(first, "partial")
        await handle.cancel()
        guard case .finished(let status, _, _, _)? = await iterator.next() else { return XCTFail("no finish") }
        XCTAssertEqual(status, "cancelled")
        await core.stop()
    }

    func testServerErrorIsReported() async throws {
        let fake = try FakeOllamaProcess(mode: "error")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()

        let updates = await collect(try await core.start(actionID: "summarize", material: "x"))
        guard case .finished(let status, _, _, let code)? = updates.last else { return XCTFail("no finish") }
        XCTAssertEqual(status, "failed")
        XCTAssertEqual(code, "http")
        await core.stop()
    }

    func testCoreDeathFailsOpenRuns() async throws {
        let fake = try FakeOllamaProcess(mode: "hold")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()
        let handle = try await core.start(actionID: "explain", material: "x")
        await core.stop()
        let updates = await collect(handle)
        guard case .finished(let status, _, _, let code)? = updates.last else { return XCTFail("no finish") }
        XCTAssertEqual(status, "failed")
        XCTAssertEqual(code, "core")
    }
}
