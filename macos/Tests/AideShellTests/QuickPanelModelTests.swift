import AideCore
import XCTest
@testable import AideShell

private final class StubProvider: RunProviding, @unchecked Sendable {
    var continuation: AsyncStream<RunUpdate>.Continuation?
    private(set) var cancelled = 0
    private(set) var started: [(String, String)] = []
    var failActions = false

    func actions() async throws -> [QuickAction] {
        if failActions { throw CoreError.notRunning }
        return [QuickAction(id: "translate", name: "翻译"), QuickAction(id: "explain", name: "解释")]
    }

    func start(actionID: String, material: String) async throws -> RunHandle {
        started.append((actionID, material))
        var c: AsyncStream<RunUpdate>.Continuation!
        let stream = AsyncStream<RunUpdate> { c = $0 }
        continuation = c
        return RunHandle(updates: stream, cancel: { [weak self] in self?.cancelled += 1 })
    }
}

@MainActor
final class QuickPanelModelTests: XCTestCase {
    private func settle() async { for _ in 0..<5 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) } }

    func testSelectionToResultToCopy() async {
        let stub = StubProvider()
        let model = QuickPanelModel(provider: stub)
        var copied: String?
        model.onClose = { copied = $0 }

        await model.present(.text("你好"))
        XCTAssertEqual(model.phase, .ready)
        model.choose(index: 0)
        await settle()
        XCTAssertEqual(model.phase, .running)
        XCTAssertEqual(stub.started.first?.0, "translate")
        XCTAssertEqual(stub.started.first?.1, "你好")

        stub.continuation?.yield(.delta(kind: "thinking", text: "…"))
        await settle()
        XCTAssertTrue(model.isThinking)
        stub.continuation?.yield(.delta(kind: "content", text: "Hel"))
        stub.continuation?.yield(.delta(kind: "content", text: "lo"))
        await settle()
        XCTAssertEqual(model.output, "Hello")
        stub.continuation?.yield(.finished(status: "succeeded", text: "Hello", error: "", errorCode: ""))
        stub.continuation?.finish()
        await settle()
        XCTAssertEqual(model.phase, .done)

        model.confirm()
        XCTAssertEqual(copied, "Hello")
    }

    func testEscapeWhileRunningCancelsInsteadOfClosing() async {
        let stub = StubProvider()
        let model = QuickPanelModel(provider: stub)
        var closed = false
        model.onClose = { _ in closed = true }
        await model.present(.text("x"))
        model.chooseHighlighted()
        await settle()
        model.escape()
        await settle()
        XCTAssertEqual(model.phase, .cancelled)
        XCTAssertEqual(stub.cancelled, 1)
        XCTAssertFalse(closed)
        model.escape()
        XCTAssertTrue(closed)
    }

    func testStaleRunUpdatesAreIgnored() async {
        let stub = StubProvider()
        let model = QuickPanelModel(provider: stub)
        await model.present(.text("x"))
        model.choose(index: 0)
        await settle()
        let first = stub.continuation
        model.escape()
        await model.present(.text("y"))
        model.choose(index: 1)
        await settle()
        first?.yield(.delta(kind: "content", text: "OLD"))
        await settle()
        XCTAssertEqual(model.output, "")
        XCTAssertEqual(model.activeAction?.id, "explain")
    }

    func testNoSelectionPermissionAndCoreFailure() async {
        let stub = StubProvider()
        let model = QuickPanelModel(provider: stub)
        await model.present(.empty)
        XCTAssertEqual(model.phase, .noSelection)
        await model.present(.needsAccessibility)
        XCTAssertEqual(model.phase, .needsAccessibility)
        stub.failActions = true
        await model.present(.text("x"))
        guard case .failed = model.phase else { return XCTFail("expected failure") }
    }

    func testFailureThenRetryRunsAgain() async {
        let stub = StubProvider()
        let model = QuickPanelModel(provider: stub)
        await model.present(.text("x"))
        model.choose(index: 0)
        await settle()
        stub.continuation?.yield(.finished(status: "failed", text: "", error: "无法连接到 Ollama", errorCode: "connection"))
        await settle()
        XCTAssertEqual(model.phase, .failed("无法连接到 Ollama"))
        model.confirm()
        await settle()
        XCTAssertEqual(model.phase, .running)
        XCTAssertEqual(stub.started.count, 2)
    }
}
