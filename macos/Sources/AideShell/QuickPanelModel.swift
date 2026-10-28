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

    private let provider: RunProviding
    private var runTask: Task<Void, Never>?
    private var handle: RunHandle?
    private var generation = 0

    /// Called when the panel should close; `restoreFocus` is false when the user is still reading.
    public var onClose: (_ copied: String?) -> Void = { _ in }

    public init(provider: RunProviding) { self.provider = provider }

    public func present(_ capture: SelectionResult) async {
        cancelRun()
        generation += 1
        output = ""
        activeAction = nil
        highlighted = 0
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
        guard let action = activeAction else { return }
        run(action)
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
        case .finished(let status, let text, let error, _):
            isThinking = false
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
        if phase == .running { cancelRun(); phase = .cancelled } else { onClose(nil) }
    }

    /// Return: once there is a result, copy it and close.
    public func confirm() {
        switch phase {
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
