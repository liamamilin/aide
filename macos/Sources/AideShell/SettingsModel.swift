import AideCore
import Foundation
import Observation

public struct AgentDraft: Equatable, Identifiable {
    public var id: String?
    public var name = ""
    public var icon = ""
    public var systemPrompt = ""
    public var builtin = false
    public var key: String { id ?? "new" }
    public init(id: String? = nil, name: String = "", icon: String = "", systemPrompt: String = "", builtin: Bool = false) {
        self.id = id; self.name = name; self.icon = icon; self.systemPrompt = systemPrompt; self.builtin = builtin
    }
    public init(_ agent: AgentInfo) {
        self.init(id: agent.id, name: agent.name, icon: agent.icon, systemPrompt: agent.systemPrompt, builtin: agent.builtin)
    }
}

public struct ActionDraft: Equatable {
    public var source: ActionInfo?          // nil = a new custom action
    public var name = ""
    public var agentID = ""
    public var instruction = ""
    public var enabled = true
    public var builtin: Bool { source?.builtin ?? false }
    public init(source: ActionInfo? = nil, agentID: String = "") {
        self.source = source; self.agentID = agentID
        if let source { name = source.name; self.agentID = source.agentID; instruction = source.instruction; enabled = source.enabled }
    }
}

/// Settings window state: everyday options, "advanced" sampling limits, and custom agents.
@MainActor @Observable
public final class SettingsModel {
    public var settings = AppSettings()
    public private(set) var saved = AppSettings()
    public private(set) var connection: ConnectionStatus?
    public private(set) var isTesting = false
    public private(set) var agents: [AgentInfo] = []
    public var editing: AgentDraft?
    public private(set) var actions: [ActionInfo] = []
    public var editingAction: ActionDraft?
    public private(set) var errorMessage: String?

    private let provider: PreferencesProviding

    public init(provider: PreferencesProviding) { self.provider = provider }

    public var isDirty: Bool { settings != saved }

    public var canSaveAgent: Bool {
        guard let editing, !editing.builtin else { return false }
        return !editing.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !editing.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    public func load() async {
        do {
            let loaded = try await provider.settings()
            settings = loaded; saved = loaded
            agents = try await provider.agents()
            actions = try await provider.manageActions()
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    public func save() async {
        do {
            let stored = try await provider.saveSettings(settings)
            settings = stored; saved = stored
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    public func revert() { settings = saved; errorMessage = nil }

    public func testConnection() async {
        isTesting = true
        defer { isTesting = false }
        do {
            connection = try await provider.testConnection(baseURL: settings.baseURL)
            errorMessage = nil
        } catch {
            connection = nil
            errorMessage = Self.describe(error)
        }
    }

    public func newAgent() { editing = AgentDraft() }

    public func edit(_ agent: AgentInfo) { editing = AgentDraft(agent) }

    public func saveAgent() async {
        guard canSaveAgent, let draft = editing else { return }
        do {
            try await provider.saveAgent(id: draft.id, name: draft.name, icon: draft.icon, systemPrompt: draft.systemPrompt)
            agents = try await provider.agents()
            editing = nil
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    public func deleteAgent(_ id: String) async {
        do {
            try await provider.deleteAgent(id: id)
            agents = try await provider.agents()
            if editing?.id == id { editing = nil }
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    public var canSaveAction: Bool {
        guard let draft = editingAction else { return false }
        return !draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !draft.instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !draft.agentID.isEmpty
    }

    public func newAction() { editingAction = ActionDraft(agentID: agents.first?.id ?? "") }

    public func edit(_ action: ActionInfo) { editingAction = ActionDraft(source: action) }

    public func saveAction() async {
        guard canSaveAction, let draft = editingAction else { return }
        do {
            try await provider.saveAction(draft.source, name: draft.name, agentID: draft.agentID,
                                          instruction: draft.instruction, enabled: draft.enabled)
            actions = try await provider.manageActions()
            editingAction = nil
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    /// Custom actions are removed; built-in ones return to their default wording.
    public func deleteAction(_ id: String) async {
        do {
            try await provider.deleteAction(id: id)
            actions = try await provider.manageActions()
            editingAction = nil
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    private static func describe(_ error: Error) -> String {
        if case CoreError.remote(_, let message) = error { return message }
        return "无法连接到 Aide 核心，请重启 Aide。"
    }
}
