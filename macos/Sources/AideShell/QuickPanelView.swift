import AideCore
import SwiftUI

/// Plain system styling only (system fonts, semantic colors, SF Symbols) so the panel follows
/// light/dark and the user's accent. Brand styling is applied elsewhere, never committed here.
public struct QuickPanelView: View {
    @Bindable var model: QuickPanelModel
    private enum Field { case input, panel }
    @FocusState private var focus: Field?

    public init(model: QuickPanelModel) { self.model = model }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showsInput {
                if !model.selection.isEmpty { header; Divider().opacity(0.5) }
                inputRow
            } else {
                header
            }
            Divider().opacity(0.5)
            content
            Divider().opacity(0.5)
            footer
        }
        .frame(width: 560)
        .background(.regularMaterial)
        .tint(Theme.accent)
        .focusable()
        .focusEffectDisabled()
        .focused($focus, equals: .panel)
        .onAppear { focus = showsInput ? .input : .panel }
        .onChange(of: model.focusRequest) { _, _ in focus = showsInput ? .input : .panel }
        .onChange(of: model.phase) { _, _ in focus = showsInput ? .input : .panel }
        .onKeyPress(.escape) { model.escape(); return .handled }
        .onKeyPress(.return, phases: .down) { press in
            // With the input field showing, plain Return belongs to it (its own submit, IME-safe).
            if showsInput && !press.modifiers.contains(.command) { return .ignored }
            if press.modifiers.contains(.command) {
                if model.pendingConfirmation != nil { model.respond(approved: true) } else { model.continueInWorkspace() }
            } else { model.confirm() }
            return .handled
        }
        // Arrows and digits only drive the action list when no text field is there to edit.
        .onKeyPress(.upArrow) { showsInput ? .ignored : { model.moveHighlight(-1); return .handled }() }
        .onKeyPress(.downArrow) { showsInput ? .ignored : { model.moveHighlight(1); return .handled }() }
        .onKeyPress(characters: .decimalDigits) { press in
            guard !showsInput, let n = Int(press.characters), model.phase == .ready else { return .ignored }
            model.choose(index: n - 1)
            return .handled
        }
    }

    /// Phases that take a typed request. Without the core's conversation path (tests) the field is hidden.
    private var showsInput: Bool {
        model.canAsk && [.ready, .noSelection, .needsAccessibility].contains(model.phase)
    }

    private var pickingAction: Bool {
        model.phase == .ready && model.askText.isEmpty && !model.selection.isEmpty
    }

    private var inputRow: some View {
        HStack(spacing: 10) {
            Image(systemName: "sparkle").foregroundStyle(.secondary)
            TextField(model.selection.isEmpty ? "想做什么？例如：打开备忘录、找最近修改的 PDF"
                                              : "或者直接说你想怎么处理这段文字", text: $model.askText)
                .textFieldStyle(.plain)
                .focused($focus, equals: .input)
                .onSubmit { model.confirm() }
                .onKeyPress(.escape) { model.escape(); return .handled }
                // With a selection and an empty field, a digit or an arrow picks an action. Once anything is
                // typed (or there is no selection), digits are text and arrows move the cursor.
                .onKeyPress(characters: .decimalDigits) { press in
                    guard pickingAction, let n = Int(press.characters) else { return .ignored }
                    model.choose(index: n - 1)
                    return .handled
                }
                .onKeyPress(.upArrow) { pickingAction ? { model.moveHighlight(-1); return .handled }() : .ignored }
                .onKeyPress(.downArrow) { pickingAction ? { model.moveHighlight(1); return .handled }() : .ignored }
        }
        .font(.system(size: 15))
        .padding(.horizontal, 16)
        .padding(.vertical, 13)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: headerSymbol).foregroundStyle(.secondary)
            Text(headerText)
                .lineLimit(2)
                .truncationMode(.tail)
                .foregroundStyle(model.selection.isEmpty && !model.isAsk ? .secondary : .primary)
            Spacer(minLength: 0)
        }
        .font(.system(size: 13))
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    private var headerSymbol: String { model.askedConversationID != nil || model.activeAction == nil && !model.askText.isEmpty ? "sparkle" : "text.cursor" }

    private var headerText: String {
        if model.activeAction == nil, !model.askText.isEmpty { return model.askText }
        return model.selection.isEmpty ? "未选中文字" : model.selection
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .loading:
            ProgressView().controlSize(.small).frame(maxWidth: .infinity, minHeight: 80)
        case .ready:
            actionList
        case .noSelection:
            if model.canAsk { EmptyView() } else {
                message("先在任意应用里选中一段文字，再按快捷键。", symbol: "cursorarrow.rays")
            }
        case .needsAccessibility:
            VStack(alignment: .leading, spacing: 10) {
                message("读取其他应用里选中的文字需要「辅助功能」权限；直接输入要做的事不受影响。", symbol: "lock.shield")
                Button("打开辅助功能设置") { SelectionReader.openAccessibilitySettings() }
                    .padding(.horizontal, 16).padding(.bottom, 12)
            }
        case .running, .done, .cancelled, .failed:
            result
        }
    }

    private var actionList: some View {
        VStack(spacing: 2) {
            ForEach(Array(model.actions.enumerated()), id: \.element.id) { index, action in
                HStack {
                    Text(action.name)
                    Spacer()
                    if index < 9 { Text("\(index + 1)").foregroundStyle(.tertiary).monospacedDigit() }
                }
                .font(.system(size: 14))
                .padding(.horizontal, 10)
                .frame(height: 32)
                .background(index == model.highlighted ? Theme.accent.opacity(0.18) : .clear,
                            in: RoundedRectangle(cornerRadius: 7))
                .contentShape(Rectangle())
                .onTapGesture { model.choose(index: index) }
            }
        }
        .padding(8)
    }

    private var result: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if case .failed(let reason) = model.phase {
                    Label(reason, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
                }
                ForEach(model.steps) { step in
                    HStack(spacing: 6) {
                        switch step.state {
                        case .running: ProgressView().controlSize(.mini)
                        case .succeeded: Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
                        case .failed: Image(systemName: "exclamationmark.circle").foregroundStyle(.orange)
                        }
                        Text(step.state == .failed ? "\(ToolLabels.title(step.name)) 未成功"
                             : step.state == .running ? ToolLabels.running(step.name) : ToolLabels.title(step.name))
                            .foregroundStyle(step.state == .failed ? Color.orange : Color.secondary)
                    }
                    .font(.system(size: 12))
                    if let result = step.result { ToolResultView(result: result) { model.onDismissKeepingFocus() } }
                }
                if let notice = model.notice { Text(notice).font(.system(size: 12)).foregroundStyle(.secondary) }
                if let request = model.pendingConfirmation { confirmation(request) }
                if model.output.isEmpty, model.phase == .running, model.steps.isEmpty, model.pendingConfirmation == nil {
                    Text(model.isThinking ? "思考中…" : "等待响应…").foregroundStyle(.secondary)
                }
                MessageText(model.output)
            }
            .font(.system(size: 14))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
        }
        .frame(minHeight: 120, maxHeight: 360)
    }

    private func confirmation(_ request: ConfirmRequest) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("需要你确认", systemImage: "hand.raised").font(.system(size: 12, weight: .medium))
            Text(request.preview).font(.system(size: 13)).textSelection(.enabled)
            if !request.reason.isEmpty { Text(request.reason).font(.system(size: 11)).foregroundStyle(.secondary) }
            HStack {
                Button("拒绝") { model.respond(approved: false) }
                Button("允许") { model.respond(approved: true) }.buttonStyle(.borderedProminent)
            }
        }
        .padding(10)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    private func message(_ text: String, symbol: String) -> some View {
        Label(text, systemImage: symbol)
            .font(.system(size: 13))
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Text(statusText).foregroundStyle(.secondary)
            Spacer()
            switch model.phase {
            case .ready: hint("↩", model.askText.isEmpty ? "运行" : "发送")
            case .noSelection, .needsAccessibility: if model.canAsk { hint("↩", "发送") }
            case .done:
                hint("⌘↩", "继续追问")
                hint("↩", "复制并返回")
            case .failed, .cancelled: hint("↩", "重试")
            default: EmptyView()
            }
            if model.pendingConfirmation != nil {
                hint("⌘↩", "允许")
                hint("esc", "拒绝")
            } else {
                hint("esc", model.phase == .running ? "停止" : "关闭")
            }
        }
        .font(.system(size: 12))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var statusText: String {
        switch model.phase {
        case .running: return model.activeAction.map { "\($0.name)中…" } ?? "处理中…"
        case .done: return model.activeAction?.name ?? ""
        case .cancelled: return model.keptChanges ? "已停止后续执行；已完成的操作不会撤销" : "已停止"
        default: return ""
        }
    }

    private func hint(_ key: String, _ label: String) -> some View {
        HStack(spacing: 5) {
            Text(key)
                .padding(.horizontal, 5)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
            Text(label)
        }
        .foregroundStyle(.secondary)
    }
}
