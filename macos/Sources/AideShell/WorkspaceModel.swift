import AideCore
import Foundation
import Observation

/// One tool call shown under the reply, so a step that failed or is still running is never invisible.
public struct ToolStep: Identifiable, Equatable {
    public enum State: Equatable { case running, succeeded, failed }
    public let id: String
    public let name: String
    public var state: State
}

public enum ToolLabels {
    /// Short noun phrase for a finished-or-running step line.
    public static func title(_ name: String) -> String {
        switch name {
        case "find_apps": "查找应用"
        case "find_files": "查找文件"
        case "open_path": "打开"
        case "list_windows": "查看窗口"
        case "arrange_window": "调整窗口"
        case "create_note": "新建备忘录"
        case "list_notes": "查看备忘录"
        case "append_note": "追加备忘录"
        case "delete_note": "删除备忘录"
        case "create_reminder": "新建提醒"
        case "list_reminders": "查看提醒"
        case "complete_reminder": "完成提醒"
        case "delete_reminder": "删除提醒"
        default: "使用工具"
        }
    }

    public static func running(_ name: String) -> String {
        switch name {
        case "find_apps": "正在查找应用…"
        case "find_files": "正在查找文件…"
        case "open_path": "正在打开…"
        case "list_windows", "arrange_window": "正在处理窗口…"
        case "create_note", "list_notes", "append_note", "delete_note": "正在操作备忘录…"
        case "create_reminder", "list_reminders", "complete_reminder", "delete_reminder": "正在操作提醒事项…"
        default: "正在使用工具…"
        }
    }
}

public struct Turn: Identifiable, Equatable {
    public let id: String
    public let role: ChatMessage.Role
    public var content: String
    public var streaming = false
    public var stopped = false
    public var steps: [ToolStep] = []
}

public struct ConversationGroup: Equatable {
    public let title: String
    public let items: [ConversationSummary]
}

public enum ConversationGrouping {
    /// Newest first: 今天 / 昨天 / 最近 7 天 / 更早. Input order is preserved inside a group.
    public static func group(_ items: [ConversationSummary], now: Date = Date(),
                             calendar: Calendar = .current) -> [ConversationGroup] {
        let startToday = calendar.startOfDay(for: now)
        let startYesterday = calendar.date(byAdding: .day, value: -1, to: startToday) ?? startToday
        let startWeek = calendar.date(byAdding: .day, value: -7, to: startToday) ?? startToday
        let buckets: [(String, (Date) -> Bool)] = [
            ("今天", { $0 >= startToday }),
            ("昨天", { $0 >= startYesterday && $0 < startToday }),
            ("最近 7 天", { $0 >= startWeek && $0 < startYesterday }),
            ("更早", { $0 < startWeek }),
        ]
        return buckets.compactMap { title, test in
            let members = items.filter { test($0.createdAt) }
            return members.isEmpty ? nil : ConversationGroup(title: title, items: members)
        }
    }
}

/// Workspace state: conversation list, the open conversation, and one in-flight reply.
@MainActor @Observable
public final class WorkspaceModel {
    public private(set) var conversations: [ConversationSummary] = []
    public private(set) var selectedID: Int?
    public private(set) var turns: [Turn] = []
    public private(set) var agents: [AgentInfo] = []
    public private(set) var models: [String] = []
    public private(set) var currentModel = ""
    public private(set) var isSending = false
    public private(set) var errorMessage: String?
    /// Subtle one-line status while a tool runs ("正在查找应用…"), or why this message ran without tools.
    public private(set) var activity: String?
    public private(set) var pendingConfirmation: ConfirmRequest?
    /// True when no model could be listed: Ollama is not running or has nothing installed.
    public private(set) var modelServiceUnavailable = false
    /// The current error can be fixed in Settings (wrong address, service down).
    public private(set) var errorOffersSettings = false
    public var onOpenSettings: () -> Void = {}
    /// Bumped whenever the composer should take keyboard focus (new chat, opened conversation).
    public private(set) var focusRequest = 0
    public var draft = ""
    public var search = "" { didSet { if search != oldValue { Task { await reloadList() } } } }
    public var selectedAgentID: String?

    private let provider: WorkspaceProviding
    private var handle: RunHandle?
    private var generation = 0

    public init(provider: WorkspaceProviding) { self.provider = provider }

    public var title: String {
        conversations.first { $0.id == selectedID }.map { $0.title.isEmpty ? "新对话" : $0.title } ?? "新对话"
    }

    public func start() async {
        let before = generation
        async let list: Void = reloadList()
        do {
            agents = try await provider.agents()
            selectedAgentID = selectedAgentID ?? agents.first?.id
            let list = try await provider.models()
            models = list.models
            currentModel = list.current
            modelServiceUnavailable = list.models.isEmpty
        } catch {
            errorMessage = "无法连接 Aide 核心"
        }
        await list
        // Resume the most recent conversation, unless the user (or a Quick Panel hand-off) already chose one.
        if generation == before, selectedID == nil, let latest = conversations.first { await select(latest.id) }
    }

    /// Re-check the model service, e.g. after the person started Ollama or fixed the address in Settings.
    public func refreshModels() async {
        guard let list = try? await provider.models() else { return }
        models = list.models
        currentModel = list.current
        modelServiceUnavailable = list.models.isEmpty
    }

    public func reloadList() async {
        do { conversations = try await provider.conversations(query: search) }
        catch { errorMessage = "无法读取对话列表" }
    }

    public func newChat() {
        stop()
        generation += 1
        selectedID = nil
        turns = []
        errorMessage = nil
        activity = nil
        focusRequest += 1
        Task { await reloadAgents() }
    }

    /// New chats just remember the choice; an open conversation switches agent for its next turn.
    public func chooseAgent(_ id: String) async {
        let previous = selectedAgentID
        selectedAgentID = id
        guard let conversation = selectedID, id != previous else { return }
        do { try await provider.setAgent(conversationID: conversation, agentID: id) }
        catch {
            selectedAgentID = previous
            errorMessage = "无法切换助手"
        }
    }

    /// Picks up agents created or removed in Settings; keeps the selection when it still exists.
    public func reloadAgents() async {
        guard let latest = try? await provider.agents() else { return }
        agents = latest
        if !latest.contains(where: { $0.id == selectedAgentID }) { selectedAgentID = latest.first?.id }
    }

    public func select(_ id: Int) async {
        guard id != selectedID else { return }
        stop()
        generation += 1
        let mine = generation
        do {
            let detail = try await provider.conversation(id: id)
            guard generation == mine else { return }
            selectedID = id
            selectedAgentID = detail.agentID
            turns = detail.messages.map { Turn(id: "m\($0.id)", role: $0.role, content: $0.content) }
            errorMessage = nil
            focusRequest += 1
        } catch {
            errorMessage = "无法打开这个对话"
        }
    }

    public func delete(_ id: Int) async {
        do {
            try await provider.deleteConversation(id: id)
            if selectedID == id { newChat() }
            await reloadList()
        } catch { errorMessage = "删除失败" }
    }

    public func choose(model: String) async {
        do { try await provider.selectModel(model); currentModel = model }
        catch { errorMessage = "切换模型失败" }
    }

    public func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending else { return }
        draft = ""
        errorMessage = nil
        errorOffersSettings = false
        activity = nil
        pendingConfirmation = nil
        isSending = true
        generation += 1
        let mine = generation
        turns.append(Turn(id: UUID().uuidString, role: .user, content: text))
        let reply = Turn(id: UUID().uuidString, role: .assistant, content: "", streaming: true)
        turns.append(reply)
        Task {
            do {
                let conversationID = try await ensureConversation()
                let handle = try await provider.send(conversationID: conversationID, text: text)
                guard generation == mine else { await handle.cancel(); return }
                self.handle = handle
                if !handle.note.isEmpty { activity = handle.note }
                for await update in handle.updates where generation == mine { apply(update, to: reply.id) }
            } catch {
                guard generation == mine else { return }
                fail("发送失败：\(error)", removing: reply.id)
            }
            if generation == mine { isSending = false; self.handle = nil; pendingConfirmation = nil }
            await reloadList()
        }
    }

    /// Answers the pending confirmation; the core runs the action only on an explicit approval.
    public func respond(approved: Bool) {
        guard let request = pendingConfirmation, let handle else { return }
        pendingConfirmation = nil
        Task { await handle.respond(request.id, approved) }
    }

    public func stop() {
        guard isSending else { return }
        let handle = self.handle
        self.handle = nil
        pendingConfirmation = nil
        if let handle { Task { await handle.cancel() } }
    }

    /// Open a conversation created elsewhere (e.g. "continue" from the Quick Panel).
    public func open(conversationID: Int) async {
        selectedID = nil
        await reloadList()
        await select(conversationID)
    }

    private func ensureConversation() async throws -> Int {
        if let selectedID { return selectedID }
        let id = try await provider.createConversation(agentID: selectedAgentID, seed: [])
        selectedID = id
        return id
    }

    private func apply(_ update: RunUpdate, to turnID: String) {
        guard let index = turns.firstIndex(where: { $0.id == turnID }) else { return }
        switch update {
        case .delta(let kind, let text):
            if kind == "content" { turns[index].content += text }
        case .tool(let callID, let name, let state, let status):
            activity = state == "tool_finished" ? nil : ToolLabels.running(name)
            let outcome: ToolStep.State = state != "tool_finished" ? .running : (status == "succeeded" ? .succeeded : .failed)
            if let step = turns[index].steps.firstIndex(where: { $0.id == callID }) {
                turns[index].steps[step].state = outcome
            } else {
                turns[index].steps.append(ToolStep(id: callID, name: name, state: outcome))
            }
        case .confirm(let request):
            pendingConfirmation = request
        case .finished(let status, let text, let error, let code):
            activity = nil
            pendingConfirmation = nil
            turns[index].streaming = false
            switch status {
            case "succeeded": turns[index].content = text
            case "cancelled", "limited": turns[index].stopped = true
            default: fail(Self.describe(error, code: code), removing: turnID, offersSettings: Self.fixableInSettings(code))
            }
            isSending = false
        }
    }

    private static func fixableInSettings(_ code: String) -> Bool { code == "connection" || code == "timeout" }

    private static func describe(_ error: String, code: String) -> String {
        if code == "core" { return "Aide 核心已退出，再发送一次会自动重启。" }
        return error.isEmpty ? "请求失败" : error
    }

    private func fail(_ message: String, removing turnID: String, offersSettings: Bool = false) {
        errorMessage = message
        errorOffersSettings = offersSettings
        isSending = false
        if let index = turns.firstIndex(where: { $0.id == turnID }), turns[index].content.isEmpty {
            turns.remove(at: index)
        } else if let index = turns.firstIndex(where: { $0.id == turnID }) {
            turns[index].streaming = false
            turns[index].stopped = true
        }
    }
}
