import Foundation

public struct ModelInfo: Sendable, Equatable, Identifiable {
    public let name: String
    public let size: Int64
    public let vision: Bool?
    public let tools: Bool?
    public let thinking: Bool?
    public var id: String { name }
    public init(name: String, size: Int64, vision: Bool? = nil, tools: Bool? = nil, thinking: Bool? = nil) {
        self.name = name; self.size = size; self.vision = vision; self.tools = tools; self.thinking = thinking
    }
}

public struct ModelCatalog: Sendable, Equatable {
    public let reachable: Bool
    public let models: [ModelInfo]
    public init(reachable: Bool, models: [ModelInfo]) { self.reachable = reachable; self.models = models }
}

public struct PullProgress: Sendable, Equatable {
    public let model: String
    public let status: String
    public let completed: Int64
    public let total: Int64
    public init(model: String, status: String, completed: Int64, total: Int64) {
        self.model = model; self.status = status; self.completed = completed; self.total = total
    }
    public var fraction: Double? { total > 0 ? min(1, Double(completed) / Double(total)) : nil }
}

public struct UpdateInfo: Sendable, Equatable {
    public let current: String
    public let latest: String?
    public let available: Bool
    public let page: URL?
    public let download: URL?
    public init(current: String, latest: String?, available: Bool, page: URL? = nil, download: URL? = nil) {
        self.current = current; self.latest = latest; self.available = available
        self.page = page; self.download = download
    }
}

public enum ExportFormat: String, Sendable, CaseIterable { case markdown, json }

/// What the model manager, export, diagnostics and update check need from the core. Tests stub this.
public protocol SystemProviding: Sendable {
    func modelCatalog(baseURL: String?) async throws -> ModelCatalog
    func pullModel(_ name: String, baseURL: String?, progress: @escaping @Sendable (PullProgress) -> Void) async throws
    func exportConversations(to url: URL, format: ExportFormat) async throws -> (conversations: Int, messages: Int)
    func diagnostics(baseURL: String?) async throws -> String
    func checkForUpdate() async throws -> UpdateInfo
}

extension CoreClient: SystemProviding {
    public func modelCatalog(baseURL: String?) async throws -> ModelCatalog {
        let result = try await call("models.detail", params: Self.base(baseURL))
        let rows = (result["models"]?.arrayValue ?? []).compactMap { row -> ModelInfo? in
            guard let name = row["name"]?.stringValue else { return nil }
            return ModelInfo(name: name, size: Int64(row["size"]?.doubleValue ?? 0), vision: row["vision"]?.boolValue,
                             tools: row["tools"]?.boolValue, thinking: row["thinking"]?.boolValue)
        }
        return ModelCatalog(reachable: result["reachable"]?.boolValue ?? false, models: rows)
    }

    public func pullModel(_ name: String, baseURL: String?, progress: @escaping @Sendable (PullProgress) -> Void) async throws {
        pullObserver = progress
        defer { pullObserver = nil }
        var params = Self.base(baseURL)
        params["model"] = .string(name)
        _ = try await call("models.pull", params: params)
    }

    public func exportConversations(to url: URL, format: ExportFormat) async throws -> (conversations: Int, messages: Int) {
        let result = try await call("export.conversations", params: ["path": .string(url.path), "format": .string(format.rawValue)])
        return (result["conversations"]?.intValue ?? 0, result["messages"]?.intValue ?? 0)
    }

    public func diagnostics(baseURL: String?) async throws -> String {
        try await call("diagnostics.report", params: Self.base(baseURL))["text"]?.stringValue ?? ""
    }

    public func checkForUpdate() async throws -> UpdateInfo {
        let r = try await call("updates.check")
        return UpdateInfo(current: r["current"]?.stringValue ?? "", latest: r["latest"]?.stringValue,
                          available: r["update"]?.boolValue ?? false,
                          page: r["page"]?.stringValue.flatMap(URL.init(string:)),
                          download: r["download"]?.stringValue.flatMap(URL.init(string:)))
    }

    private static func base(_ url: String?) -> [String: JSONValue] {
        url.map { ["base_url": .string($0)] } ?? [:]
    }
}
