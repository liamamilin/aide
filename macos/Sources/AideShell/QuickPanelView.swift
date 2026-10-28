import AideCore
import SwiftUI

/// Plain system styling only (system fonts, semantic colors, SF Symbols) so the panel follows
/// light/dark and the user's accent. Brand styling is applied elsewhere, never committed here.
public struct QuickPanelView: View {
    @Bindable var model: QuickPanelModel

    public init(model: QuickPanelModel) { self.model = model }

    public var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().opacity(0.5)
            content
            Divider().opacity(0.5)
            footer
        }
        .frame(width: 560)
        .background(.regularMaterial)
        .focusable()
        .focusEffectDisabled()
        .onKeyPress(.escape) { model.escape(); return .handled }
        .onKeyPress(.return) { model.confirm(); return .handled }
        .onKeyPress(.upArrow) { model.moveHighlight(-1); return .handled }
        .onKeyPress(.downArrow) { model.moveHighlight(1); return .handled }
        .onKeyPress(characters: .decimalDigits) { press in
            guard let n = Int(press.characters), model.phase == .ready else { return .ignored }
            model.choose(index: n - 1)
            return .handled
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "text.cursor").foregroundStyle(.secondary)
            Text(model.selection.isEmpty ? "未选中文字" : model.selection)
                .lineLimit(2)
                .truncationMode(.tail)
                .foregroundStyle(model.selection.isEmpty ? .secondary : .primary)
            Spacer(minLength: 0)
        }
        .font(.system(size: 13))
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    @ViewBuilder private var content: some View {
        switch model.phase {
        case .loading:
            ProgressView().controlSize(.small).frame(maxWidth: .infinity, minHeight: 80)
        case .ready:
            actionList
        case .noSelection:
            message("先在任意应用里选中一段文字，再按快捷键。", symbol: "cursorarrow.rays")
        case .needsAccessibility:
            message("需要「辅助功能」权限才能读取其他应用里的选中文字。请在 系统设置 → 隐私与安全性 → 辅助功能 中允许 Aide。",
                    symbol: "lock.shield")
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
                .background(index == model.highlighted ? Color.accentColor.opacity(0.18) : .clear,
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
                if model.output.isEmpty, model.phase == .running {
                    Text(model.isThinking ? "思考中…" : "等待响应…").foregroundStyle(.secondary)
                }
                // Plain reading text; a card is only for code/quotes, which arrive with the Workspace.
                Text(model.output).textSelection(.enabled).lineSpacing(3)
            }
            .font(.system(size: 14))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
        }
        .frame(minHeight: 120, maxHeight: 360)
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
            case .ready: hint("↩", "运行")
            case .done: hint("↩", "复制并返回")
            case .failed, .cancelled: hint("↩", "重试")
            default: EmptyView()
            }
            hint("esc", model.phase == .running ? "停止" : "关闭")
        }
        .font(.system(size: 12))
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var statusText: String {
        switch model.phase {
        case .running: return model.activeAction.map { "\($0.name)中…" } ?? ""
        case .done: return model.activeAction?.name ?? ""
        case .cancelled: return "已停止"
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
