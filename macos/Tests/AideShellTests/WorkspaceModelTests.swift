import AideCore
import XCTest
@testable import AideShell

private final class StubWorkspace: WorkspaceProviding, @unchecked Sendable {
    var summaries = [ConversationSummary(id: 1, title: "旧对话", agentID: "general_assistant",
                                         createdAt: Date(), messageCount: 2)]
    var details: [Int: ConversationDetail] = [
        1: ConversationDetail(id: 1, title: "旧对话", agentID: "general_assistant",
                              messages: [ChatMessage(id: 10, role: .user, content: "q"),
                                         ChatMessage(id: 11, role: .assistant, content: "a")]),
    ]
    var continuation: AsyncStream<RunUpdate>.Continuation?
    private(set) var created: [(String?, Int)] = []
    private(set) var sent: [(Int, String)] = []
    private(set) var cancelled = 0
    private(set) var responses: [(String, Bool)] = []
    var sendNote = ""
    var modelNames = ["m1", "m2"]
    private(set) var deleted: [Int] = []

    func conversations(query: String) async throws -> [ConversationSummary] {
        query.isEmpty ? summaries : summaries.filter { $0.title.contains(query) }
    }
    func conversation(id: Int) async throws -> ConversationDetail { details[id]! }
    func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int {
        created.append((agentID, seed.count)); return 2
    }
    func deleteConversation(id: Int) async throws { deleted.append(id); summaries.removeAll { $0.id == id } }
    func renameConversation(id: Int, title: String) async throws {}
    func agents() async throws -> [AgentInfo] { [AgentInfo(id: "general_assistant", name: "通用助手")] }
    private(set) var agentChanges: [(Int, String)] = []
    var failSetAgent = false
    func setAgent(conversationID: Int, agentID: String) async throws {
        if failSetAgent { throw CoreError.remote(code: "invalid", message: "x") }
        agentChanges.append((conversationID, agentID))
    }
    func models() async throws -> ModelList { ModelList(models: modelNames, current: "m1") }
    func selectModel(_ model: String) async throws {}
    func send(conversationID: Int, text: String) async throws -> RunHandle {
        sent.append((conversationID, text))
        var c: AsyncStream<RunUpdate>.Continuation!
        let stream = AsyncStream<RunUpdate> { c = $0 }
        continuation = c
        return RunHandle(updates: stream, cancel: { [weak self] in self?.cancelled += 1 },
                         respond: { [weak self] id, approved in self?.responses.append((id, approved)) }, note: sendNote)
    }
}

@MainActor
final class WorkspaceModelTests: XCTestCase {
    private func settle() async { for _ in 0..<6 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) } }

    func testStartSelectAndSendInExistingConversation() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        XCTAssertEqual(model.models, ["m1", "m2"])
        XCTAssertEqual(model.currentModel, "m1")
        await model.select(1)
        XCTAssertEqual(model.turns.map(\.content), ["q", "a"])

        model.draft = "  next  "
        model.send()
        await settle()
        XCTAssertEqual(stub.sent.first?.0, 1)
        XCTAssertEqual(stub.sent.first?.1, "next")
        XCTAssertTrue(model.isSending)
        XCTAssertEqual(model.draft, "")
        stub.continuation?.yield(.delta(kind: "content", text: "Hi"))
        await settle()
        XCTAssertEqual(model.turns.last?.content, "Hi")
        stub.continuation?.yield(.finished(status: "succeeded", text: "Hi there", error: "", errorCode: ""))
        stub.continuation?.finish()
        await settle()
        XCTAssertFalse(model.isSending)
        XCTAssertEqual(model.turns.last?.content, "Hi there")
        XCTAssertEqual(model.turns.last?.streaming, false)
    }

    func testFirstSendCreatesConversationWithSelectedAgent() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.newChat()
        model.draft = "hello"
        model.send()
        await settle()
        XCTAssertEqual(stub.created.first?.0, "general_assistant")
        XCTAssertEqual(stub.sent.first?.0, 2)
        XCTAssertEqual(model.selectedID, 2)
    }

    func testStopCancelsAndKeepsPartialMarkedStopped() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.draft = "x"
        model.send()
        await settle()
        stub.continuation?.yield(.delta(kind: "content", text: "part"))
        await settle()
        model.stop()
        await settle()
        XCTAssertEqual(stub.cancelled, 1)
        stub.continuation?.yield(.finished(status: "cancelled", text: "part", error: "", errorCode: ""))
        await settle()
        XCTAssertEqual(model.turns.last?.content, "part")
        XCTAssertEqual(model.turns.last?.stopped, true)
        XCTAssertFalse(model.isSending)
    }

    func testFailureShowsErrorAndDropsEmptyReply() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.newChat()   // start() resumes the latest conversation; this test is about a fresh one
        model.draft = "x"
        model.send()
        await settle()
        stub.continuation?.yield(.finished(status: "failed", text: "", error: "无法连接到 Ollama", errorCode: "connection"))
        await settle()
        XCTAssertEqual(model.errorMessage, "无法连接到 Ollama")
        XCTAssertEqual(model.turns.map(\.role), [.user])
        XCTAssertFalse(model.isSending)
    }

    func testDeleteSelectedClearsView() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        await model.select(1)
        await model.delete(1)
        XCTAssertNil(model.selectedID)
        XCTAssertTrue(model.turns.isEmpty)
        XCTAssertTrue(model.conversations.isEmpty)
    }

    func testGroupingBuckets() {
        let cal = Calendar(identifier: .gregorian)
        let now = cal.date(from: DateComponents(year: 2026, month: 10, day: 28, hour: 15))!
        func item(_ id: Int, daysAgo: Int, hour: Int = 9) -> ConversationSummary {
            let day = cal.date(byAdding: .day, value: -daysAgo, to: cal.startOfDay(for: now))!
            return ConversationSummary(id: id, title: "t\(id)", agentID: "a",
                                       createdAt: cal.date(byAdding: .hour, value: hour, to: day)!, messageCount: 1)
        }
        let groups = ConversationGrouping.group([item(1, daysAgo: 0), item(2, daysAgo: 1), item(3, daysAgo: 3),
                                                 item(4, daysAgo: 30)], now: now, calendar: cal)
        XCTAssertEqual(groups.map(\.title), ["今天", "昨天", "最近 7 天", "更早"])
        XCTAssertEqual(groups.map { $0.items.map(\.id) }, [[1], [2], [3], [4]])
    }

    func testMessageTextSplitsFencedCodeIncludingUnterminated() {
        let blocks = MessageText.blocks("前言\n```swift\nlet a = 1\n```\n结尾\n```\nopen")
        XCTAssertEqual(blocks, [.prose("前言"), .code(language: "swift", body: "let a = 1"),
                                .prose("结尾"), .code(language: "", body: "open")])
    }
}

@MainActor
final class WorkspaceFocusTests: XCTestCase {
    func testNewChatAndOpeningAConversationRequestComposerFocus() async {
        let model = WorkspaceModel(provider: FocusStub())
        await model.start()
        let initial = model.focusRequest
        model.newChat()
        XCTAssertEqual(model.focusRequest, initial + 1)
        await model.select(1)
        XCTAssertEqual(model.focusRequest, initial + 2)
    }
}

private struct FocusStub: WorkspaceProviding {
    func conversations(query: String) async throws -> [ConversationSummary] { [] }
    func conversation(id: Int) async throws -> ConversationDetail {
        ConversationDetail(id: id, title: "", agentID: "a", messages: [])
    }
    func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int { 1 }
    func deleteConversation(id: Int) async throws {}
    func renameConversation(id: Int, title: String) async throws {}
    func agents() async throws -> [AgentInfo] { [] }
    func setAgent(conversationID: Int, agentID: String) async throws {}
    func models() async throws -> ModelList { ModelList(models: [], current: "") }
    func selectModel(_ model: String) async throws {}
    func send(conversationID: Int, text: String) async throws -> RunHandle {
        RunHandle(updates: AsyncStream { $0.finish() }, cancel: {})
    }
}

@MainActor
final class WorkspaceAgentTests: XCTestCase {
    func testNewChatRefreshesAgentsAndDropsAMissingSelection() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.selectedAgentID = "gone"
        await model.reloadAgents()
        XCTAssertEqual(model.selectedAgentID, "general_assistant")
        model.selectedAgentID = "general_assistant"
        await model.reloadAgents()
        XCTAssertEqual(model.selectedAgentID, "general_assistant")
    }
}

@MainActor
final class WorkspaceResumeTests: XCTestCase {
    func testStartResumesTheMostRecentConversation() async {
        let model = WorkspaceModel(provider: StubWorkspace())
        await model.start()
        XCTAssertEqual(model.selectedID, 1)
        XCTAssertEqual(model.turns.map(\.content), ["q", "a"])
    }

    func testChoosingAgentInAnOpenConversationPersistsAndRollsBackOnFailure() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        XCTAssertEqual(model.selectedID, 1)
        await model.chooseAgent("translator")
        XCTAssertEqual(model.selectedAgentID, "translator")
        XCTAssertEqual(stub.agentChanges.first?.1, "translator")
        stub.failSetAgent = true
        await model.chooseAgent("code_expert")
        XCTAssertEqual(model.selectedAgentID, "translator")
        XCTAssertEqual(model.errorMessage, "无法切换助手")
    }
}

@MainActor
final class WorkspaceToolTests: XCTestCase {
    private func settle() async { for _ in 0..<6 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) } }

    func testConfirmationIsShownAnsweredOnceAndClearedWhenTheRunEnds() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.draft = "删掉那条笔记"
        model.send()
        await settle()
        stub.continuation?.yield(.tool(callID: "c1", name: "delete_note", state: "tool_started", status: ""))
        await settle()
        XCTAssertEqual(model.activity, "正在操作备忘录…")
        stub.continuation?.yield(.confirm(ConfirmRequest(id: "k1", callID: "c1", preview: "删除备忘录：周报", reason: "可恢复")))
        await settle()
        XCTAssertEqual(model.pendingConfirmation?.preview, "删除备忘录：周报")

        model.respond(approved: true)
        XCTAssertNil(model.pendingConfirmation)
        model.respond(approved: true)   // a second answer is ignored
        await settle()
        XCTAssertEqual(stub.responses.count, 1)
        XCTAssertEqual(stub.responses.first?.0, "k1")
        XCTAssertEqual(stub.responses.first?.1, true)

        stub.continuation?.yield(.finished(status: "succeeded", text: "已删除", error: "", errorCode: ""))
        await settle()
        XCTAssertNil(model.activity)
        XCTAssertFalse(model.isSending)
    }

    func testToolStepsRecordRunningThenOutcomePerCall() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.newChat()
        model.draft = "新建备忘录"
        model.send()
        await settle()
        stub.continuation?.yield(.tool(callID: "a", name: "create_note", state: "tool_started", status: ""))
        await settle()
        XCTAssertEqual(model.turns.last?.steps, [ToolStep(id: "a", name: "create_note", state: .running)])
        stub.continuation?.yield(.tool(callID: "a", name: "create_note", state: "tool_finished", status: "failed"))
        stub.continuation?.yield(.tool(callID: "b", name: "find_apps", state: "tool_finished", status: "succeeded"))
        await settle()
        XCTAssertEqual(model.turns.last?.steps.map(\.state), [.failed, .succeeded])
    }

    func testStoppingWhilePendingDropsTheCardAndNeverApproves() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.draft = "删掉"
        model.send()
        await settle()
        stub.continuation?.yield(.confirm(ConfirmRequest(id: "k2", callID: "c", preview: "p", reason: "")))
        await settle()
        model.stop()
        await settle()
        XCTAssertNil(model.pendingConfirmation)
        XCTAssertTrue(stub.responses.isEmpty)
        XCTAssertEqual(stub.cancelled, 1)
    }

    func testWhyToolsAreOffIsShownForThatMessage() async {
        let stub = StubWorkspace()
        stub.sendNote = "本次未启用工具：所选模型未声明工具调用能力"
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.draft = "hi"
        model.send()
        await settle()
        XCTAssertEqual(model.activity, "本次未启用工具：所选模型未声明工具调用能力")
    }
}

@MainActor
final class WorkspaceRecoveryTests: XCTestCase {
    private func settle() async { for _ in 0..<6 { await Task.yield(); try? await Task.sleep(nanoseconds: 5_000_000) } }

    func testConnectionFailureOffersSettingsAndCoreExitExplainsRestart() async {
        let stub = StubWorkspace()
        let model = WorkspaceModel(provider: stub)
        await model.start()
        model.newChat()
        model.draft = "hi"
        model.send()
        await settle()
        stub.continuation?.yield(.finished(status: "failed", text: "", error: "无法连接到 Ollama", errorCode: "connection"))
        await settle()
        XCTAssertEqual(model.errorMessage, "无法连接到 Ollama")
        XCTAssertTrue(model.errorOffersSettings)

        model.draft = "again"
        model.send()
        await settle()
        XCTAssertFalse(model.errorOffersSettings)
        stub.continuation?.yield(.finished(status: "failed", text: "", error: "Aide 核心已退出", errorCode: "core"))
        await settle()
        XCTAssertEqual(model.errorMessage, "Aide 核心已退出，再发送一次会自动重启。")
        XCTAssertFalse(model.errorOffersSettings)
    }

    func testNoModelsMeansServiceUnavailableUntilRefreshFindsOne() async {
        let stub = StubWorkspace()
        stub.modelNames = []
        let model = WorkspaceModel(provider: stub)
        await model.start()
        XCTAssertTrue(model.modelServiceUnavailable)
        stub.modelNames = ["m1"]
        await model.refreshModels()
        XCTAssertFalse(model.modelServiceUnavailable)
    }
}
