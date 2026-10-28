import AideCore
import Foundation
import Observation

public struct Turn: Identifiable, Equatable {
    public let id: String
    public let role: ChatMessage.Role
    public var content: String
    public var streaming = false
    public var stopped = false
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
        async let list: Void = reloadList()
        do {
            agents = try await provider.agents()
            selectedAgentID = selectedAgentID ?? agents.first?.id
            let list = try await provider.models()
            models = list.models
            currentModel = list.current
        } catch {
            errorMessage = "无法连接 Aide 核心"
        }
        await list
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
        focusRequest += 1
        Task { await reloadAgents() }
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
                for await update in handle.updates where generation == mine { apply(update, to: reply.id) }
            } catch {
                guard generation == mine else { return }
                fail("发送失败：\(error)", removing: reply.id)
            }
            if generation == mine { isSending = false; self.handle = nil }
            await reloadList()
        }
    }

    public func stop() {
        guard isSending else { return }
        let handle = self.handle
        self.handle = nil
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
        case .finished(let status, let text, let error, _):
            turns[index].streaming = false
            switch status {
            case "succeeded": turns[index].content = text
            case "cancelled", "limited": turns[index].stopped = true
            default: fail(error.isEmpty ? "请求失败" : error, removing: turnID)
            }
            isSending = false
        }
    }

    private func fail(_ message: String, removing turnID: String) {
        errorMessage = message
        isSending = false
        if let index = turns.firstIndex(where: { $0.id == turnID }), turns[index].content.isEmpty {
            turns.remove(at: index)
        } else if let index = turns.firstIndex(where: { $0.id == turnID }) {
            turns[index].streaming = false
            turns[index].stopped = true
        }
    }
}
