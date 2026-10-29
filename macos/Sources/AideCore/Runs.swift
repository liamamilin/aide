import Foundation

public struct QuickAction: Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let agentID: String?
    public init(id: String, name: String, agentID: String? = nil) { self.id = id; self.name = name; self.agentID = agentID }
}

/// A tool wants to do something the person must approve. `preview` is the exact action text built by
/// trusted code in the core, never model-written prose.
public struct ConfirmRequest: Sendable, Equatable, Identifiable {
    public let id: String
    public let callID: String
    public let preview: String
    public let reason: String
    public init(id: String, callID: String, preview: String, reason: String) {
        self.id = id; self.callID = callID; self.preview = preview; self.reason = reason
    }
}

public enum RunUpdate: Sendable, Equatable {
    case delta(kind: String, text: String)
    /// `state` is tool_started / tool_updated / tool_finished; `status` carries the tool's own phase or result.
    /// `output` is the lookup tool's own JSON (apps, files, notes, reminders); empty for everything else.
    case tool(callID: String, name: String, state: String, status: String, output: String = "")
    case confirm(ConfirmRequest)
    case finished(status: String, text: String, error: String, errorCode: String)
}

/// A started run: updates arrive on the stream; `cancel` asks the core to stop it.
public struct RunHandle: Sendable {
    public let updates: AsyncStream<RunUpdate>
    public let cancel: @Sendable () async -> Void
    /// Answers a pending confirmation by id.
    public let respond: @Sendable (_ confirmationID: String, _ approved: Bool) async -> Void
    /// Why this run has no tools (shown to the person), empty when tools are on or not applicable.
    public let note: String
    public init(updates: AsyncStream<RunUpdate>, cancel: @escaping @Sendable () async -> Void,
                respond: @escaping @Sendable (String, Bool) async -> Void = { _, _ in }, note: String = "") {
        self.updates = updates
        self.cancel = cancel
        self.respond = respond
        self.note = note
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
    /// Streamed text waiting for the next flush: many tiny deltas become one update, so the UI redraws a
    /// few dozen times a second instead of once per token.
    private var pendingText: [String: (kind: String, text: String)] = [:]
    /// Runs that no one registered for (their start failed) must not pile up buffered events.
    private static let maxEarlyRuns = 8

    /// True when a delta was held back and a flush should be scheduled.
    @discardableResult
    mutating func route(_ event: CoreEvent) -> Bool {
        guard let runID = event.data["run_id"]?.stringValue, let update = Self.update(from: event) else { return false }
        if case .delta(let kind, let text) = update {
            if let held = pendingText[runID], held.kind != kind { flush(runID) }
            pendingText[runID, default: (kind, "")].text += text
            return true
        }
        flush(runID)                                         // keep order: held text goes out before this update
        deliver(update, to: runID)
        return false
    }

    mutating func flushAll() { for runID in Array(pendingText.keys) { flush(runID) } }

    private mutating func flush(_ runID: String) {
        guard let held = pendingText.removeValue(forKey: runID), !held.text.isEmpty else { return }
        deliver(.delta(kind: held.kind, text: held.text), to: runID)
    }

    private mutating func deliver(_ update: RunUpdate, to runID: String) {
        if let listener = listeners[runID] {
            listener.yield(update)
            if case .finished = update { listener.finish(); listeners[runID] = nil }
        } else {
            if early[runID] == nil, early.count >= Self.maxEarlyRuns, let oldest = early.keys.first { early[oldest] = nil }
            early[runID, default: []].append(update)
        }
    }

    mutating func register(runID: String) -> AsyncStream<RunUpdate> {
        flush(runID)
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
        pendingText.removeAll()
    }

    private static func update(from event: CoreEvent) -> RunUpdate? {
        switch event.name {
        case "run.delta":
            return .delta(kind: event.data["kind"]?.stringValue ?? "content", text: event.data["text"]?.stringValue ?? "")
        case "run.tool":
            return .tool(callID: event.data["call_id"]?.stringValue ?? "", name: event.data["tool"]?.stringValue ?? "",
                         state: event.data["state"]?.stringValue ?? "", status: event.data["status"]?.stringValue ?? "",
                         output: event.data["output"]?.stringValue ?? "")
        case "run.confirm_request":
            guard let id = event.data["confirmation_id"]?.stringValue else { return nil }
            return .confirm(ConfirmRequest(id: id, callID: event.data["call_id"]?.stringValue ?? "",
                                           preview: event.data["preview"]?.stringValue ?? "",
                                           reason: event.data["reason"]?.stringValue ?? ""))
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

    func handle(for runID: String, note: String = "") -> RunHandle {
        RunHandle(updates: registerRun(runID),
                  cancel: { [self] in _ = try? await call("run.cancel", params: ["run_id": .string(runID)]) },
                  respond: { [self] confirmationID, approved in
                      _ = try? await call("run.confirm", params: [
                          "run_id": .string(runID), "confirmation_id": .string(confirmationID), "approved": .bool(approved)])
                  },
                  note: note)
    }
}
