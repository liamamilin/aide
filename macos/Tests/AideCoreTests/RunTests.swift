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

final class WorkspaceCoreTests: XCTestCase {
    func testConversationRoundTripAgainstRealCore() async throws {
        let fake = try FakeOllamaProcess(mode: "stream")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()

        let agents = try await core.agents()
        XCTAssertTrue(agents.contains { $0.id == "general_assistant" })

        let id = try await core.createConversation(agentID: "general_assistant", seed: [])
        let updates = await collect(try await core.send(conversationID: id, text: "你好"))
        guard case .finished(let status, _, _, _)? = updates.last else { return XCTFail("no finish") }
        XCTAssertEqual(status, "succeeded")

        let detail = try await core.conversation(id: id)
        XCTAssertEqual(detail.messages.map(\.content), ["你好", "Hello"])
        XCTAssertEqual(detail.title, "你好")
        let listed = try await core.conversations(query: "")
        XCTAssertEqual(listed.first?.id, id)
        try await core.deleteConversation(id: id)
        let after = try await core.conversations(query: "")
        XCTAssertTrue(after.isEmpty)
        await core.stop()
    }

    func testSettingsAndCustomAgentRoundTripAgainstRealCore() async throws {
        let fake = try FakeOllamaProcess(mode: "stream")
        defer { fake.stop() }
        let (core, dataDir) = try makeCore(ollamaURL: fake.url)
        defer { try? FileManager.default.removeItem(at: dataDir) }
        try await core.start()

        var settings = try await core.settings()
        XCTAssertEqual(settings.baseURL, fake.url)
        settings.think = .off
        settings.temperature = 0.2
        settings.numCtx = 4096
        let stored = try await core.saveSettings(settings)
        XCTAssertEqual(stored.think, .off)
        XCTAssertEqual(stored.temperature, 0.2)
        let reloaded = try await core.settings()
        XCTAssertEqual(reloaded.numCtx, 4096)

        settings.temperature = 9
        do { _ = try await core.saveSettings(settings); XCTFail("out-of-range value accepted") } catch {}

        let status = try await core.testConnection(baseURL: fake.url)
        XCTAssertTrue(status.ok)

        try await core.saveAgent(id: nil, name: "周报", icon: "📝", systemPrompt: "写周报")
        let custom = try await core.agents().first { $0.name == "周报" }
        XCTAssertEqual(custom?.builtin, false)
        XCTAssertEqual(custom?.systemPrompt, "写周报")
        try await core.deleteAgent(id: custom!.id)
        let remaining = try await core.agents()
        XCTAssertFalse(remaining.contains { $0.name == "周报" })
        await core.stop()
    }
}

final class RunRouterToolEventTests: XCTestCase {
    func testToolAndConfirmationEventsReachTheRunsStreamInOrder() async {
        var router = RunRouter()
        let stream = router.register(runID: "r1")
        router.route(CoreEvent(name: "run.tool", data: .object([
            "run_id": .string("r1"), "call_id": .string("c1"), "tool": .string("find_apps"),
            "state": .string("tool_started"), "status": .string("")])))
        router.route(CoreEvent(name: "run.confirm_request", data: .object([
            "run_id": .string("r1"), "confirmation_id": .string("k1"), "call_id": .string("c1"),
            "preview": .string("删除备忘录：周报"), "reason": .string("可恢复")])))
        router.route(CoreEvent(name: "run.future_thing", data: .object(["run_id": .string("r1")])))   // unknown: ignored
        router.route(CoreEvent(name: "run.finished", data: .object(["run_id": .string("r1"), "status": .string("succeeded")])))
        var got: [RunUpdate] = []
        for await update in stream { got.append(update) }
        XCTAssertEqual(got.count, 3)
        XCTAssertEqual(got[0], .tool(callID: "c1", name: "find_apps", state: "tool_started", status: ""))
        XCTAssertEqual(got[1], .confirm(ConfirmRequest(id: "k1", callID: "c1", preview: "删除备忘录：周报", reason: "可恢复")))
    }
}

final class RunRouterCoalescingTests: XCTestCase {
    private func delta(_ kind: String, _ text: String) -> CoreEvent {
        CoreEvent(name: "run.delta", data: .object(["run_id": .string("r1"), "kind": .string(kind), "text": .string(text)]))
    }

    func testManyTokensBecomeFewUpdatesInOrderAndNothingIsLost() async {
        var router = RunRouter()
        let stream = router.register(runID: "r1")
        XCTAssertTrue(router.route(delta("thinking", "想")))
        for piece in ["Hel", "lo", " wor", "ld"] { router.route(delta("content", piece)) }
        router.flushAll()
        router.route(delta("content", "!"))
        router.route(CoreEvent(name: "run.finished", data: .object(["run_id": .string("r1"), "status": .string("succeeded")])))
        var got: [RunUpdate] = []
        for await update in stream { got.append(update) }
        XCTAssertEqual(got, [.delta(kind: "thinking", text: "想"), .delta(kind: "content", text: "Hello world"),
                             .delta(kind: "content", text: "!"),
                             .finished(status: "succeeded", text: "", error: "", errorCode: "")])
    }
}
