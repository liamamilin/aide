import Foundation

public struct QuickAction: Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let agentID: String?
    public init(id: String, name: String, agentID: String? = nil) { self.id = id; self.name = name; self.agentID = agentID }
}

public enum RunUpdate: Sendable, Equatable {
    case delta(kind: String, text: String)
    case finished(status: String, text: String, error: String, errorCode: String)
}

/// A started run: updates arrive on the stream; `cancel` asks the core to stop it.
public struct RunHandle: Sendable {
    public let updates: AsyncStream<RunUpdate>
    public let cancel: @Sendable () async -> Void
    public init(updates: AsyncStream<RunUpdate>, cancel: @escaping @Sendable () async -> Void) {
        self.updates = updates
        self.cancel = cancel
    }
}

/// What the shell needs from the core for quick actions. Tests stub this.
public protocol RunProviding: Sendable {
    func actions() async throws -> [QuickAction]
    func start(actionID: String, material: String) async throws -> RunHandle
}

/// Routes `run.delta` / `run.finished` events to the stream of the run they belong to.
/// Events can arrive before the `run.start` response does, so early ones are buffered per run id.
struct RunRouter {
    private var listeners: [String: AsyncStream<RunUpdate>.Continuation] = [:]
    private var early: [String: [RunUpdate]] = [:]

    mutating func route(_ event: CoreEvent) {
        guard let runID = event.data["run_id"]?.stringValue, let update = Self.update(from: event) else { return }
        if let listener = listeners[runID] {
            listener.yield(update)
            if case .finished = update { listener.finish(); listeners[runID] = nil }
        } else {
            early[runID, default: []].append(update)
        }
    }

    mutating func register(runID: String) -> AsyncStream<RunUpdate> {
        var continuation: AsyncStream<RunUpdate>.Continuation!
        let stream = AsyncStream<RunUpdate> { continuation = $0 }
        var finished = false
        for update in early.removeValue(forKey: runID) ?? [] {
            continuation.yield(update)
            if case .finished = update { finished = true }
        }
        if finished { continuation.finish() } else { listeners[runID] = continuation }
        return stream
    }

    /// The core died: end every open run with a failure instead of leaving the UI spinning.
    mutating func failAll(message: String) {
        for listener in listeners.values {
            listener.yield(.finished(status: "failed", text: "", error: message, errorCode: "core"))
            listener.finish()
        }
        listeners.removeAll()
        early.removeAll()
    }

    private static func update(from event: CoreEvent) -> RunUpdate? {
        switch event.name {
        case "run.delta":
            return .delta(kind: event.data["kind"]?.stringValue ?? "content", text: event.data["text"]?.stringValue ?? "")
        case "run.finished":
            return .finished(status: event.data["status"]?.stringValue ?? "failed",
                             text: event.data["text"]?.stringValue ?? "",
                             error: event.data["error"]?.stringValue ?? "",
                             errorCode: event.data["error_code"]?.stringValue ?? "")
        default:
            return nil
        }
    }
}

extension CoreClient: RunProviding {
    public func actions() async throws -> [QuickAction] {
        let result = try await call("actions.list")
        guard case .array(let items)? = result["actions"] else { return [] }
        return items.compactMap { item in
            guard let id = item["id"]?.stringValue, let name = item["name"]?.stringValue else { return nil }
            return QuickAction(id: id, name: name, agentID: item["agent_id"]?.stringValue)
        }
    }

    public func start(actionID: String, material: String) async throws -> RunHandle {
        let result = try await call("run.start", params: ["action_id": .string(actionID), "material": .string(material)])
        guard let runID = result["run_id"]?.stringValue else { throw CoreError.remote(code: "protocol", message: "missing run_id") }
        return handle(for: runID)
    }

    func handle(for runID: String) -> RunHandle {
        RunHandle(updates: registerRun(runID),
                  cancel: { [self] in _ = try? await call("run.cancel", params: ["run_id": .string(runID)]) })
    }
}
