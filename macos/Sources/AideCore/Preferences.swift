import Foundation

public enum ThinkMode: String, Sendable, CaseIterable {
    case modelDefault = "model_default", on, off
}

/// The settings a person is expected to touch; sampling and limits are "advanced" in the UI.
public struct AppSettings: Sendable, Equatable {
    public var baseURL: String
    public var think: ThinkMode
    public var timeout: Int
    public var numCtx: Int
    public var numPredict: Int
    public var temperature: Double
    public var topP: Double
    public var topK: Int
    public var repeatPenalty: Double
    public var maxRounds: Int

    public init(baseURL: String = "http://localhost:11434", think: ThinkMode = .modelDefault, timeout: Int = 120,
                numCtx: Int = 16384, numPredict: Int = 20480, temperature: Double = 0.7, topP: Double = 0.9,
                topK: Int = 40, repeatPenalty: Double = 1.1, maxRounds: Int = 10) {
        self.baseURL = baseURL; self.think = think; self.timeout = timeout; self.numCtx = numCtx
        self.numPredict = numPredict; self.temperature = temperature; self.topP = topP; self.topK = topK
        self.repeatPenalty = repeatPenalty; self.maxRounds = maxRounds
    }

    var wire: JSONValue {
        .object([
            "base_url": .string(baseURL), "think": .object(["mode": .string(think.rawValue)]),
            "timeout": .number(Double(timeout)), "num_ctx": .number(Double(numCtx)),
            "num_predict": .number(Double(numPredict)), "temperature": .number(temperature),
            "top_p": .number(topP), "top_k": .number(Double(topK)),
            "repeat_penalty": .number(repeatPenalty), "max_rounds": .number(Double(maxRounds)),
        ])
    }

    init(wire row: JSONValue) {
        let d = AppSettings()
        self.init(baseURL: row["base_url"]?.stringValue ?? d.baseURL,
                  think: row["think"]?["mode"]?.stringValue.flatMap(ThinkMode.init(rawValue:)) ?? d.think,
                  timeout: row["timeout"]?.intValue ?? d.timeout, numCtx: row["num_ctx"]?.intValue ?? d.numCtx,
                  numPredict: row["num_predict"]?.intValue ?? d.numPredict,
                  temperature: row["temperature"]?.doubleValue ?? d.temperature,
                  topP: row["top_p"]?.doubleValue ?? d.topP, topK: row["top_k"]?.intValue ?? d.topK,
                  repeatPenalty: row["repeat_penalty"]?.doubleValue ?? d.repeatPenalty,
                  maxRounds: row["max_rounds"]?.intValue ?? d.maxRounds)
    }
}

public struct ConnectionStatus: Sendable, Equatable {
    public let ok: Bool
    public let modelCount: Int
    public init(ok: Bool, modelCount: Int) { self.ok = ok; self.modelCount = modelCount }
}

/// What the Settings window needs from the core. Tests stub this.
public protocol PreferencesProviding: Sendable {
    func settings() async throws -> AppSettings
    func saveSettings(_ settings: AppSettings) async throws -> AppSettings
    func testConnection(baseURL: String) async throws -> ConnectionStatus
    func agents() async throws -> [AgentInfo]
    func saveAgent(id: String?, name: String, icon: String, systemPrompt: String) async throws
    func deleteAgent(id: String) async throws
}

extension CoreClient: PreferencesProviding {
    public func settings() async throws -> AppSettings {
        AppSettings(wire: try await call("settings.get")["settings"] ?? .null)
    }

    public func saveSettings(_ settings: AppSettings) async throws -> AppSettings {
        AppSettings(wire: try await call("settings.set", params: ["settings": settings.wire])["settings"] ?? .null)
    }

    public func testConnection(baseURL: String) async throws -> ConnectionStatus {
        let result = try await call("settings.test_connection", params: ["base_url": .string(baseURL)])
        return ConnectionStatus(ok: result["ok"]?.boolValue ?? false, modelCount: result["model_count"]?.intValue ?? 0)
    }

    public func saveAgent(id: String?, name: String, icon: String, systemPrompt: String) async throws {
        var params: [String: JSONValue] = ["name": .string(name), "icon": .string(icon), "system_prompt": .string(systemPrompt)]
        if let id { params["id"] = .string(id) }
        _ = try await call("agents.save", params: params)
    }

    public func deleteAgent(id: String) async throws {
        _ = try await call("agents.delete", params: ["id": .string(id)])
    }
}
