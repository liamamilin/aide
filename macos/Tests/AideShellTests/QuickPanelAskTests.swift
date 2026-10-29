import AideCore
import XCTest
@testable import AideShell

private final class AskStub: WorkspaceProviding, @unchecked Sendable {
    var continuation: AsyncStream<RunUpdate>.Continuation?
    private(set) var sent: [(Int, String)] = []
    private(set) var responses: [(String, Bool)] = []
    private(set) var cancelled = 0
    var note = ""
    /// When set, `send` waits for the test to release it, like a core that is slow to start the run.
    var gate: AsyncStream<Void>?

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
        if let gate { for await _ in gate { break } }
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

    func testLookupResultsComeFromTheToolsOwnJSON() {
        let files = #"{"results":[{"kind":"file","name":"报告.pdf","path":"/Users/x/报告.pdf","modified":"2026-10-27"},{"kind":"folder","name":"项目","path":"/Users/x/项目"}]}"#
        guard case .files(let items)? = ToolResults.parse(tool: "find_files", output: files) else { return XCTFail() }
        XCTAssertEqual(items.map(\.name), ["报告.pdf", "项目"])
        XCTAssertEqual(items[0].modified, "2026-10-27")
        XCTAssertEqual(ToolResults.parse(tool: "list_notes", output: #"{"total_notes":42,"matching":42,"notes":[{"id":"x","title":"周报","age_seconds":5}]}"#),
                       .notes(total: 42, matching: 42, titles: ["周报"]))
        XCTAssertNil(ToolResults.parse(tool: "find_files", output: #"{"results":[]}"#))     // nothing found: no empty card
        XCTAssertNil(ToolResults.parse(tool: "open_path", output: #"{"action":"opened"}"#))
        XCTAssertNil(ToolResults.parse(tool: "find_apps", output: "not json"))
    }

    func testFinishedLookupStepCarriesItsResultIntoThePanel() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "找 PDF"
        model.submitAsk()
        await settle()
        stub.continuation?.yield(.tool(callID: "c1", name: "find_files", state: "tool_started", status: ""))
        stub.continuation?.yield(.tool(callID: "c1", name: "find_files", state: "tool_finished", status: "succeeded",
                                       output: #"{"results":[{"kind":"file","name":"a.pdf","path":"/tmp/a.pdf"}]}"#))
        await settle()
        guard case .files(let items)? = model.steps.first?.result else { return XCTFail("no result card") }
        XCTAssertEqual(items.first?.path, "/tmp/a.pdf")
    }

    func testEscBeforeTheRunHandleArrivesStopsItAsSoonAsItDoes() async {
        let stub = AskStub()
        var release: AsyncStream<Void>.Continuation!
        stub.gate = AsyncStream<Void> { release = $0 }
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "找 PDF"
        model.confirm()
        await settle()
        XCTAssertEqual(model.phase, .running)
        model.escape()                                      // the core has not handed back a run yet
        XCTAssertEqual(model.phase, .cancelled)
        release.yield(())
        await settle()
        XCTAssertEqual(stub.cancelled, 1, "the run that started late is cancelled right away")
        stub.continuation?.yield(.delta(kind: "content", text: "迟到"))
        await settle()
        XCTAssertEqual(model.output, "")
        XCTAssertEqual(model.phase, .cancelled)
    }

    func testNothingIsSentWhenStoppedBeforeTheConversationExists() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "x"
        model.confirm()
        model.escape()                                      // same turn: the task has not even run yet
        await settle()
        XCTAssertTrue(stub.sent.isEmpty)
        XCTAssertEqual(model.phase, .cancelled)
    }

    func testStoppingAfterAChangeSaysItWasNotUndone() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "新建备忘录"
        model.confirm()
        await settle()
        stub.continuation?.yield(.tool(callID: "c1", name: "create_note", state: "tool_finished", status: "succeeded"))
        await settle()
        XCTAssertTrue(model.keptChanges)
        model.escape()
        XCTAssertEqual(model.phase, .cancelled)
        XCTAssertTrue(model.keptChanges, "cancel does not claim the note was rolled back")
    }

    func testLookupOnlyRunHasNoChangesToKeep() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "找文件"
        model.confirm()
        await settle()
        stub.continuation?.yield(.tool(callID: "c1", name: "find_files", state: "tool_finished", status: "succeeded"))
        await settle()
        XCTAssertFalse(model.keptChanges)
    }

    func testOpeningAMissingFileFailsWithoutPretendingItWorked() {
        XCTAssertNotNil(ToolResultView.open("/nonexistent/aide-\(UUID().uuidString).pdf"))
        XCTAssertNotNil(ToolResultView.reveal("/nonexistent/aide-\(UUID().uuidString).pdf"))
    }

    func testAfterAStopANewQuestionCanBeTypedAndTheOldRunStaysSilent() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "写一篇长文章"
        model.confirm()
        await settle()
        let first = stub.continuation
        model.escape()
        XCTAssertEqual(model.phase, .cancelled)
        XCTAssertTrue(model.acceptsTypedRequest, "the field is back after a stop")
        model.askText = "换个问题"
        model.confirm()                                     // a typed question goes out, it is not a retry
        await settle()
        XCTAssertEqual(stub.sent.map(\.1), ["写一篇长文章", "换个问题"])
        XCTAssertEqual(model.lastAsk, "换个问题")
        XCTAssertEqual(model.askText, "")
        first?.yield(.delta(kind: "content", text: "旧"))
        await settle()
        XCTAssertEqual(model.output, "", "nothing from the stopped run gets in")
        stub.continuation?.yield(.delta(kind: "content", text: "新"))
        await settle()
        XCTAssertEqual(model.output, "新")
    }

    func testEmptyFieldAfterAStopRetriesTheSameRequest() async {
        let stub = AskStub()
        let model = QuickPanelModel(provider: Actions(), ask: stub)
        await model.present(.empty)
        model.askText = "找 PDF"
        model.confirm()
        await settle()
        model.escape()
        model.confirm()
        await settle()
        XCTAssertEqual(stub.sent.map(\.1), ["找 PDF", "找 PDF"])
        XCTAssertEqual(model.phase, .running)
    }
}
