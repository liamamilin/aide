import AideCore
import XCTest
@testable import AideShell

private final class AskStub: WorkspaceProviding, @unchecked Sendable {
    var continuation: AsyncStream<RunUpdate>.Continuation?
    private(set) var sent: [(Int, String)] = []
    private(set) var responses: [(String, Bool)] = []
    private(set) var cancelled = 0
    var note = ""

    func conversations(query: String) async throws -> [ConversationSummary] { [] }
    func conversation(id: Int) async throws -> ConversationDetail { ConversationDetail(id: id, title: "", agentID: "", messages: []) }
    func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int { 7 }
    func deleteConversation(id: Int) async throws {}
    func renameConversation(id: Int, title: String) async throws {}
    func agents() async throws -> [AgentInfo] { [] }
    func setAgent(conversationID: Int, agentID: String) async throws {}
    func models() async throws -> ModelList { ModelList(models: [], current: "") }
    func selectModel(_ model: String) async throws {}
    func send(conversationID: Int, text: String, mode: SendMode, images: [String]) async throws -> RunHandle {
        sent.append((conversationID, text))
        var c: AsyncStream<RunUpdate>.Continuation!
        let stream = AsyncStream<RunUpdate> { c = $0 }
        continuation = c
        return RunHandle(updates: stream, cancel: { [weak self] in self?.cancelled += 1 },
                         respond: { [weak self] id, approved in self?.responses.append((id, approved)) }, note: note)
    }
}

private struct Actions: RunProviding {
    func actions() async throws -> [QuickAction] { [QuickAction(id: "translate", name: "翻译")] }
    func start(actionID: String, material: String) async throws -> RunHandle { throw CoreError.notRunning }
}

@MainActor
final class QuickPanelAskTests: XCTestCase {
    private func settle() async { for _ in 0..<6 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) } }

    func testTypedRequestWithoutSelectionRunsThroughAConversationWithTools() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        XCTAssertEqual(model.phase, .noSelection)
        model.askText = "打开备忘录"
        model.confirm()                                    // Return in the field
        await settle()
        XCTAssertEqual(stub.sent.first?.1, "打开备忘录")
        XCTAssertEqual(model.phase, .running)
        stub.continuation?.yield(.tool(callID: "c1", name: "open_path", state: "tool_started", status: ""))
        stub.continuation?.yield(.tool(callID: "c1", name: "open_path", state: "tool_finished", status: "succeeded"))
        stub.continuation?.yield(.delta(kind: "content", text: "已打开"))
        stub.continuation?.yield(.finished(status: "succeeded", text: "已打开备忘录", error: "", errorCode: ""))
        stub.continuation?.finish()
        await settle()
        XCTAssertEqual(model.phase, .done)
        XCTAssertEqual(model.steps.map(\.state), [.succeeded])
        XCTAssertEqual(model.output, "已打开备忘录")
        var opened: Int?
        model.onOpenConversation = { opened = $0 }
        model.onClose = { _ in }
        model.continueInWorkspace()
        XCTAssertEqual(opened, 7)
    }

    func testSelectionBecomesContextAndEmptyFieldStillPicksTheAction() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.text("hello world"))
        XCTAssertEqual(model.phase, .ready)
        model.askText = "翻译成法语"
        model.confirm()
        await settle()
        XCTAssertEqual(stub.sent.first?.1, "翻译成法语\n\n选中的文字：\nhello world")
    }

    func testConfirmationIsAnsweredNeverSkippedAndEscDeclines() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "删除备忘录周报"
        model.submitAsk()
        await settle()
        stub.continuation?.yield(.confirm(ConfirmRequest(id: "k1", callID: "c1", preview: "删除备忘录：周报", reason: "")))
        await settle()
        XCTAssertEqual(model.pendingConfirmation?.id, "k1")
        model.escape()                                     // declines, does not cancel the run
        await settle()
        XCTAssertEqual(stub.responses.first?.0, "k1")
        XCTAssertEqual(stub.responses.first?.1, false)
        XCTAssertEqual(stub.cancelled, 0)
        XCTAssertEqual(model.phase, .running)
    }

    func testNoteIsShownAndPanelWithoutCoreAskHidesTheField() async {
        let stub = AskStub()
        stub.note = "本次未启用工具：模型不支持"
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "你好"
        model.submitAsk()
        await settle()
        XCTAssertEqual(model.notice, "本次未启用工具：模型不支持")
        XCTAssertFalse(QuickPanelModel(provider: Actions()).canAsk)
    }
}
