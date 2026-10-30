import Foundation

public enum ShellPolicy: String, Sendable, CaseIterable { case readonlyAuto = "readonly_auto", confirmAll = "confirm_all" }

public struct ShellStatus: Sendable, Equatable {
    public let workspace: String
    public let usable: Bool
    public let policy: ShellPolicy
    public init(workspace: String, usable: Bool, policy: ShellPolicy) {
        self.workspace = workspace; self.usable = usable; self.policy = policy
    }
}

/// Opt-in command line, inside one folder the person picked. Commands outside a short read-only list
/// always show the exact text and folder first.
public protocol ShellProviding: Sendable {
    func shellStatus() async throws -> ShellStatus
    func setShellWorkspace(path: String, policy: ShellPolicy) async throws -> ShellStatus
    func clearShellWorkspace() async throws -> ShellStatus
    func setBash(conversationID: Int, enabled: Bool) async throws
}

extension CoreClient: ShellProviding {
    public func shellStatus() async throws -> ShellStatus { Self.parseShell(try await call("shell.status")) }

    public func setShellWorkspace(path: String, policy: ShellPolicy) async throws -> ShellStatus {
        Self.parseShell(try await call("shell.set_workspace", params: ["path": .string(path), "policy": .string(policy.rawValue)]))
    }

    public func clearShellWorkspace() async throws -> ShellStatus { Self.parseShell(try await call("shell.clear_workspace")) }

    public func setBash(conversationID: Int, enabled: Bool) async throws {
        _ = try await call("conversations.set_bash",
                           params: ["conversation_id": .number(Double(conversationID)), "enabled": .bool(enabled)])
    }

    private static func parseShell(_ r: JSONValue) -> ShellStatus {
        ShellStatus(workspace: r["workspace"]?.stringValue ?? "", usable: r["usable"]?.boolValue ?? false,
                    policy: r["policy"]?.stringValue.flatMap(ShellPolicy.init(rawValue:)) ?? .readonlyAuto)
    }
}
