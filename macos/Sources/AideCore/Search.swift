import Foundation

public struct SearchStatus: Sendable, Equatable {
    public let provider: String
    public let providers: [String]
    public let hasKey: Bool
    public init(provider: String, providers: [String], hasKey: Bool) {
        self.provider = provider; self.providers = providers; self.hasKey = hasKey
    }
}

/// Opt-in web search. The key goes to the macOS Keychain through the core and is never read back.
public protocol SearchProviding: Sendable {
    func searchStatus() async throws -> SearchStatus
    func saveSearchKey(provider: String, key: String) async throws -> SearchStatus
    func deleteSearchKey(provider: String) async throws -> SearchStatus
    func setSearch(conversationID: Int, enabled: Bool) async throws
}

extension CoreClient: SearchProviding {
    public func searchStatus() async throws -> SearchStatus { Self.parse(try await call("search.status")) }

    public func saveSearchKey(provider: String, key: String) async throws -> SearchStatus {
        Self.parse(try await call("search.save_key", params: ["provider": .string(provider), "key": .string(key)]))
    }

    public func deleteSearchKey(provider: String) async throws -> SearchStatus {
        Self.parse(try await call("search.delete_key", params: ["provider": .string(provider)]))
    }

    public func setSearch(conversationID: Int, enabled: Bool) async throws {
        _ = try await call("conversations.set_search",
                           params: ["conversation_id": .number(Double(conversationID)), "enabled": .bool(enabled)])
    }

    private static func parse(_ r: JSONValue) -> SearchStatus {
        SearchStatus(provider: r["provider"]?.stringValue ?? "parallel",
                     providers: (r["providers"]?.arrayValue ?? []).compactMap(\.stringValue),
                     hasKey: r["has_key"]?.boolValue ?? false)
    }
}
