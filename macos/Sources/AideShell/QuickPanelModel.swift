import AideCore
import Foundation
import Observation

/// State machine behind the Quick Panel: pick an action for the selection, watch the result
/// stream in, then copy it and go back to where you were.
@MainActor @Observable
public final class QuickPanelModel {
    public enum Phase: Equatable {
        case loading
        case ready
        case noSelection
        case needsAccessibility
        case running
        case done
        case failed(String)
        case cancelled
    }

    public private(set) var phase: Phase = .loading
    public private(set) var selection = ""
    public private(set) var actions: [QuickAction] = []
    public private(set) var activeAction: QuickAction?
    public private(set) var output = ""
    public private(set) var isThinking = false
    public var highlighted = 0
    /// What the person types: a request ("打开备忘录"), with the selection as context when there is one.
    public var askText = ""
    public private(set) var steps: [ToolStep] = []
    public private(set) var pendingConfirmation: ConfirmRequest?
    /// Why this request ran without tools, or a similar one-line note.
    public private(set) var notice: String?
    /// The conversation a typed request created; ⌘↩ continues it in the Workspace.
    public private(set) var askedConversationID: Int?
    /// Bumped when the input field should take keyboard focus.
    public private(set) var focusRequest = 0

    private let provider: RunProviding
    private let asker: WorkspaceProviding?
    private var runTask: Task<Void, Never>?
    private var handle: RunHandle?
    private var generation = 0
    private var lastAsk: String?

    /// Called when the panel should close; `restoreFocus` is false when the user is still reading.
    public var onClose: (_ copied: String?) -> Void = { _ in }
    /// ⌘↩ on a finished result: hand selection + answer to the Workspace as a new conversation.
    public var onContinue: (_ selection: String, _ action: QuickAction, _ answer: String) -> Void = { _, _, _ in }
    /// ⌘↩ after a typed request: open that conversation in the Workspace.
    public var onOpenConversation: (_ id: Int) -> Void = { _ in }

    /// `ask` runs typed requests through the normal conversation path (same core, tools and confirmations).
    public init(provider: RunProviding, ask: WorkspaceProviding? = nil) { self.provider = provider; asker = ask }

    public var isAsk: Bool { activeAction == nil && askedConversationID != nil }
    public var canAsk: Bool { asker != nil }

    public func present(_ capture: SelectionResult) async {
        cancelRun()
        generation += 1
        output = ""
        activeAction = nil
        highlighted = 0
        askText = ""
        steps = []
        pendingConfirmation = nil
        notice = nil
        askedConversationID = nil
        lastAsk = nil
        focusRequest += 1
        switch capture {
        case .empty:
            selection = ""
            phase = .noSelection
        case .needsAccessibility:
            selection = ""
            phase = .needsAccessibility
        case .text(let text):
            selection = text
            phase = .loading
            do {
                actions = try await provider.actions()
                phase = actions.isEmpty ? .failed("没有可用的快捷动作") : .ready
            } catch {
                phase = .failed("无法连接 Aide 核心")
            }
        }
    }

    /// The hotkey pressed again while the panel is open, with something new selected: keep the action the
    /// person already chose and run it on the new text (to switch action, Esc first). With no action
    /// chosen yet, show the action list for the new selection.
    public func reselect(_ text: String) async {
        guard let action = activeAction else { await present(.text(text)); return }
        selection = text
        run(action)
    }

    public func moveHighlight(_ delta: Int) {
        guard phase == .ready, !actions.isEmpty else { return }
        highlighted = (highlighted + delta + actions.count) % actions.count
    }

    public func chooseHighlighted() { choose(index: highlighted) }

    public func choose(index: Int) {
        guard phase == .ready, actions.indices.contains(index) else { return }
        run(actions[index])
    }

    public func retry() {
        if let action = activeAction { run(action) } else if let text = lastAsk { startAsk(text) }
    }

    /// Return in the input field: run the typed request (the selection, if any, is its context).
    public func submitAsk() {
        let text = askText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard canAsk, !text.isEmpty, [.ready, .noSelection, .needsAccessibility].contains(phase) else { return }
        lastAsk = text
        startAsk(text)
    }

    private func startAsk(_ text: String) {
        guard let asker else { return }
        cancelRun()
        generation += 1
        let mine = generation
        activeAction = nil
        askedConversationID = nil
        output = ""
        steps = []
        notice = nil
        pendingConfirmation = nil
        isThinking = false
        phase = .running
        let material = selection.isEmpty ? text : "\(text)\n\n选中的文字：\n\(selection)"
        runTask = Task {
            do {
                let id = try await asker.createConversation(agentID: nil, seed: [])
                await MainActor.run { if self.generation == mine { self.askedConversationID = id } }
                let handle = try await asker.send(conversationID: id, text: material)
                await MainActor.run {
                    guard self.generation == mine else { return }
                    self.handle = handle
                    if !handle.note.isEmpty { self.notice = handle.note }
                }
                for await update in handle.updates {
                    await MainActor.run { if self.generation == mine { self.apply(update) } }
                }
            } catch {
                await MainActor.run {
                    if self.generation == mine { self.phase = .failed(Self.describe(error)) }
                }
            }
        }
    }

    /// Answers the pending confirmation; the core runs the action only on an explicit approval.
    public func respond(approved: Bool) {
        guard let request = pendingConfirmation, let handle else { return }
        pendingConfirmation = nil
        Task { await handle.respond(request.id, approved) }
    }

    private static func describe(_ error: Error) -> String {
        if case CoreError.remote(_, let message) = error { return message }
        return "无法启动：\(error)"
    }

    private func run(_ action: QuickAction) {
        cancelRun()
        generation += 1
        let mine = generation
        activeAction = action
        output = ""
        isThinking = false
        phase = .running
        runTask = Task { [provider, selection] in
            do {
                let handle = try await provider.start(actionID: action.id, material: selection)
                await MainActor.run { if self.generation == mine { self.handle = handle } }
                for await update in handle.updates {
                    await MainActor.run { if self.generation == mine { self.apply(update) } }
                }
            } catch {
                await MainActor.run { if self.generation == mine { self.phase = .failed("无法启动：\(error)") } }
            }
        }
    }

    private func apply(_ update: RunUpdate) {
        switch update {
        case .delta(let kind, let text):
            if kind == "content" { output += text; isThinking = false } else { isThinking = true }
        case .tool(let callID, let name, let state, let status):
            let outcome: ToolStep.State = state != "tool_finished" ? .running : (status == "succeeded" ? .succeeded : .failed)
            if let index = steps.firstIndex(where: { $0.id == callID }) {
                steps[index].state = outcome
            } else {
                steps.append(ToolStep(id: callID, name: name, state: outcome))
            }
        case .confirm(let request):
            pendingConfirmation = request
        case .finished(let status, let text, let error, _):
            isThinking = false
            pendingConfirmation = nil
            switch status {
            case "succeeded": output = text; phase = .done
            case "cancelled": phase = .cancelled
            case "limited": phase = output.isEmpty ? .failed(error) : .done   // partial text is still usable
            default: phase = .failed(error.isEmpty ? "请求失败" : error)
            }
        }
    }

    /// Esc: stop a running request first; otherwise close the panel.
    public func escape() {
        if pendingConfirmation != nil { respond(approved: false); return }   // declining is not cancelling
        if phase == .running { cancelRun(); phase = .cancelled } else { onClose(nil) }
    }

    public func continueInWorkspace() {
        guard phase == .done else { return }
        if let action = activeAction {
            onContinue(selection, action, output)
        } else if let id = askedConversationID {
            onOpenConversation(id)
        } else { return }
        onClose(nil)
    }

    /// Return: once there is a result, copy it and close.
    public func confirm() {
        switch phase {
        case .ready where !askText.trimmingCharacters(in: .whitespaces).isEmpty: submitAsk()
        case .noSelection, .needsAccessibility: submitAsk()
        case .ready: chooseHighlighted()
        case .done: onClose(output)
        case .failed, .cancelled: retry()
        default: break
        }
    }

    private func cancelRun() {
        runTask?.cancel()
        let handle = self.handle
        self.handle = nil
        if let handle { Task { await handle.cancel() } }
    }
}
