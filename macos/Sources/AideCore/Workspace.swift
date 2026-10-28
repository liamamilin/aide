import Foundation

public struct ConversationSummary: Sendable, Equatable, Identifiable {
    public let id: Int
    public var title: String
    public let agentID: String
    public let createdAt: Date
    public let messageCount: Int
    public init(id: Int, title: String, agentID: String, createdAt: Date, messageCount: Int) {
        self.id = id; self.title = title; self.agentID = agentID; self.createdAt = createdAt; self.messageCount = messageCount
    }
}

public struct ChatMessage: Sendable, Equatable, Identifiable {
    public enum Role: String, Sendable { case user, assistant }
    public let id: Int
    public let role: Role
    public var content: String
    public init(id: Int, role: Role, content: String) { self.id = id; self.role = role; self.content = content }
}

public struct AgentInfo: Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let icon: String
    public let systemPrompt: String
    public let builtin: Bool
    public init(id: String, name: String, icon: String = "", systemPrompt: String = "", builtin: Bool = true) {
        self.id = id; self.name = name; self.icon = icon; self.systemPrompt = systemPrompt; self.builtin = builtin
    }
}

public struct ModelList: Sendable, Equatable {
    public let models: [String]
    public let current: String
    public init(models: [String], current: String) { self.models = models; self.current = current }
}

public struct ConversationDetail: Sendable, Equatable {
    public let id: Int
    public let title: String
    public let agentID: String
    public let messages: [ChatMessage]
    public init(id: Int, title: String, agentID: String, messages: [ChatMessage]) {
        self.id = id; self.title = title; self.agentID = agentID; self.messages = messages
    }
}

/// What the Workspace window needs from the core. Tests stub this.
public protocol WorkspaceProviding: Sendable {
    func conversations(query: String) async throws -> [ConversationSummary]
    func conversation(id: Int) async throws -> ConversationDetail
    func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int
    func deleteConversation(id: Int) async throws
    func renameConversation(id: Int, title: String) async throws
    func agents() async throws -> [AgentInfo]
    func setAgent(conversationID: Int, agentID: String) async throws
    func models() async throws -> ModelList
    func selectModel(_ model: String) async throws
    func send(conversationID: Int, text: String) async throws -> RunHandle
}

extension CoreClient: WorkspaceProviding {
    public func conversations(query: String) async throws -> [ConversationSummary] {
        let result = try await call("conversations.list", params: ["query": .string(query)])
        return (result["conversations"]?.arrayValue ?? []).compactMap { row in
            guard let id = row["id"]?.intValue, let agent = row["agent_id"]?.stringValue else { return nil }
            return ConversationSummary(id: id, title: row["title"]?.stringValue ?? "", agentID: agent,
                                       createdAt: Date(timeIntervalSince1970: row["created_at"]?.doubleValue ?? 0),
                                       messageCount: row["message_count"]?.intValue ?? 0)
        }
    }

    public func conversation(id: Int) async throws -> ConversationDetail {
        let result = try await call("conversations.get", params: ["id": .number(Double(id))])
        let messages = (result["messages"]?.arrayValue ?? []).compactMap { row -> ChatMessage? in
            guard let id = row["id"]?.intValue, let role = row["role"]?.stringValue.flatMap(ChatMessage.Role.init(rawValue:))
            else { return nil }
            return ChatMessage(id: id, role: role, content: row["content"]?.stringValue ?? "")
        }
        return ConversationDetail(id: id, title: result["title"]?.stringValue ?? "",
                                  agentID: result["agent_id"]?.stringValue ?? "", messages: messages)
    }

    public func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int {
        var params: [String: JSONValue] = [
            "messages": .array(seed.map { .object(["role": .string($0.role.rawValue), "content": .string($0.content)]) }),
        ]
        if let agentID { params["agent_id"] = .string(agentID) }
        let result = try await call("conversations.create", params: params)
        guard let id = result["id"]?.intValue else { throw CoreError.remote(code: "protocol", message: "missing id") }
        return id
    }

    public func deleteConversation(id: Int) async throws {
        _ = try await call("conversations.delete", params: ["id": .number(Double(id))])
    }

    public func renameConversation(id: Int, title: String) async throws {
        _ = try await call("conversations.rename", params: ["id": .number(Double(id)), "title": .string(title)])
    }

    public func agents() async throws -> [AgentInfo] {
        let result = try await call("agents.list")
        return (result["agents"]?.arrayValue ?? []).compactMap { row in
            guard let id = row["id"]?.stringValue, let name = row["name"]?.stringValue else { return nil }
            return AgentInfo(id: id, name: name, icon: row["icon"]?.stringValue ?? "",
                             systemPrompt: row["system_prompt"]?.stringValue ?? "",
                             builtin: row["builtin"]?.boolValue ?? true)
        }
    }

    public func setAgent(conversationID: Int, agentID: String) async throws {
        _ = try await call("conversations.set_agent",
                           params: ["id": .number(Double(conversationID)), "agent_id": .string(agentID)])
    }

    public func models() async throws -> ModelList {
        let result = try await call("models.list")
        return ModelList(models: (result["models"]?.arrayValue ?? []).compactMap(\.stringValue),
                         current: result["current"]?.stringValue ?? "")
    }

    public func selectModel(_ model: String) async throws {
        _ = try await call("models.select", params: ["model": .string(model)])
    }

    public func send(conversationID: Int, text: String) async throws -> RunHandle {
        let result = try await call("chat.send", params: ["conversation_id": .number(Double(conversationID)), "text": .string(text)])
        guard let runID = result["run_id"]?.stringValue else { throw CoreError.remote(code: "protocol", message: "missing run_id") }
        return handle(for: runID)
    }
}
