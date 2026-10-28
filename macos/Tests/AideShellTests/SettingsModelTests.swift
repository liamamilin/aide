import AideCore
import XCTest
@testable import AideShell

private final class StubPreferences: PreferencesProviding, @unchecked Sendable {
    var stored = AppSettings()
    var agentList = [AgentInfo(id: "general_assistant", name: "通用助手"),
                     AgentInfo(id: "custom_1", name: "周报", icon: "📝", systemPrompt: "写周报", builtin: false)]
    var failNextSave = false
    private(set) var savedAgents: [(String?, String)] = []
    private(set) var deletedAgents: [String] = []

    func settings() async throws -> AppSettings { stored }
    func saveSettings(_ settings: AppSettings) async throws -> AppSettings {
        if failNextSave { failNextSave = false; throw CoreError.remote(code: "invalid", message: "温度超出范围") }
        stored = settings; return settings
    }
    func testConnection(baseURL: String) async throws -> ConnectionStatus {
        ConnectionStatus(ok: baseURL.contains("ok"), modelCount: 3)
    }
    func agents() async throws -> [AgentInfo] { agentList }
    func saveAgent(id: String?, name: String, icon: String, systemPrompt: String) async throws {
        savedAgents.append((id, name))
    }
    func deleteAgent(id: String) async throws { deletedAgents.append(id); agentList.removeAll { $0.id == id } }
}

@MainActor
final class SettingsModelTests: XCTestCase {
    func testDirtyTracksEditsAndSaveClearsIt() async {
        let stub = StubPreferences()
        let model = SettingsModel(provider: stub)
        await model.load()
        XCTAssertFalse(model.isDirty)
        model.settings.think = .off
        XCTAssertTrue(model.isDirty)
        await model.save()
        XCTAssertFalse(model.isDirty)
        XCTAssertEqual(stub.stored.think, .off)
    }

    func testRejectedSaveKeepsDraftAndShowsCoreMessage() async {
        let stub = StubPreferences()
        let model = SettingsModel(provider: stub)
        await model.load()
        model.settings.temperature = 9
        stub.failNextSave = true
        await model.save()
        XCTAssertTrue(model.isDirty)
        XCTAssertEqual(model.errorMessage, "温度超出范围")
        model.revert()
        XCTAssertFalse(model.isDirty)
        XCTAssertNil(model.errorMessage)
    }

    func testConnectionCheck() async {
        let model = SettingsModel(provider: StubPreferences())
        await model.load()
        model.settings.baseURL = "http://ok"
        await model.testConnection()
        XCTAssertEqual(model.connection, ConnectionStatus(ok: true, modelCount: 3))
    }

    func testBuiltinAgentsAreReadOnlyAndCustomOnesSaveAndDelete() async {
        let stub = StubPreferences()
        let model = SettingsModel(provider: stub)
        await model.load()
        model.edit(model.agents[0])
        XCTAssertFalse(model.canSaveAgent)

        model.edit(model.agents[1])
        XCTAssertTrue(model.canSaveAgent)
        model.editing?.systemPrompt = "  "
        XCTAssertFalse(model.canSaveAgent)
        model.editing?.systemPrompt = "写日报"
        await model.saveAgent()
        XCTAssertEqual(stub.savedAgents.first?.0, "custom_1")
        XCTAssertNil(model.editing)

        model.newAgent()
        XCTAssertFalse(model.canSaveAgent)
        await model.deleteAgent("custom_1")
        XCTAssertEqual(stub.deletedAgents, ["custom_1"])
        XCTAssertEqual(model.agents.map(\.id), ["general_assistant"])
    }
}
