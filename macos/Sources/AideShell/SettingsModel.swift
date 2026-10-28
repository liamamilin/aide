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

/// Settings window state: everyday options, "advanced" sampling limits, and custom agents.
@MainActor @Observable
public final class SettingsModel {
    public var settings = AppSettings()
    public private(set) var saved = AppSettings()
    public private(set) var connection: ConnectionStatus?
    public private(set) var isTesting = false
    public private(set) var agents: [AgentInfo] = []
    public var editing: AgentDraft?
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

    private static func describe(_ error: Error) -> String {
        if case CoreError.remote(_, let message) = error { return message }
        return "无法连接到 Aide 核心，请重启 Aide。"
    }
}
