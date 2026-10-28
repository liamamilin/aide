import AideCore
import SwiftUI

public struct WorkspaceView: View {
    @Bindable var model: WorkspaceModel
    @State private var sidebarSelection: Int?

    public init(model: WorkspaceModel) { self.model = model }

    public var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 320)
        } detail: {
            detail
        }
        .navigationTitle(model.title)
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button { model.newChat() } label: { Label("新对话", systemImage: "square.and.pencil") }
                    .help("新对话 ⌘N")
                    .keyboardShortcut("n")
            }
            ToolbarItem(placement: .primaryAction) { modelMenu }
        }
        .task { await model.start() }
        .onChange(of: model.selectedID) { _, id in sidebarSelection = id }
        .onChange(of: sidebarSelection) { _, id in if let id { Task { await model.select(id) } } }
        .frame(minWidth: 760, minHeight: 480)
    }

    // MARK: Sidebar

    private var sidebar: some View {
        List(selection: $sidebarSelection) {
            ForEach(ConversationGrouping.group(model.conversations), id: \.title) { group in
                Section(group.title) {
                    ForEach(group.items) { item in
                        Text(item.title.isEmpty ? "新对话" : item.title)
                            .lineLimit(1)
                            .tag(item.id)
                            .contextMenu {
                                Button("删除", role: .destructive) { Task { await model.delete(item.id) } }
                            }
                    }
                }
            }
        }
        .searchable(text: $model.search, placement: .sidebar, prompt: "搜索对话")
        .overlay {
            if model.conversations.isEmpty {
                Text(model.search.isEmpty ? "还没有对话" : "没有匹配的对话").foregroundStyle(.secondary)
            }
        }
    }

    // MARK: Detail

    private var detail: some View {
        VStack(spacing: 0) {
            if model.turns.isEmpty { emptyState } else { transcript }
            if let error = model.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.system(size: 12)).foregroundStyle(.red)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 24).padding(.bottom, 6)
            }
            Composer(model: model)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Text("有什么可以帮你？").font(.system(size: 20, weight: .medium))
            Text(model.currentModel).font(.system(size: 12)).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(model.turns) { turn in TurnView(turn: turn).id(turn.id) }
                }
                .frame(maxWidth: 720)
                .padding(.horizontal, 24)
                .padding(.vertical, 20)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: model.turns.last?.content) { _, _ in
                if let last = model.turns.last { proxy.scrollTo(last.id, anchor: .bottom) }
            }
            .onChange(of: model.turns.count) { _, _ in
                if let last = model.turns.last { proxy.scrollTo(last.id, anchor: .bottom) }
            }
        }
    }

    private var modelMenu: some View {
        Menu {
            ForEach(model.models, id: \.self) { name in
                Button { Task { await model.choose(model: name) } } label: {
                    if name == model.currentModel { Label(name, systemImage: "checkmark") } else { Text(name) }
                }
            }
        } label: {
            Text(model.currentModel.isEmpty ? "模型" : model.currentModel).font(.system(size: 12))
        }
        .help("选择模型")
    }
}

private struct TurnView: View {
    let turn: Turn

    var body: some View {
        switch turn.role {
        case .user:
            HStack {
                Spacer(minLength: 60)
                Text(turn.content)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Color.accentColor.opacity(0.14), in: RoundedRectangle(cornerRadius: 12))
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 6) {
                if turn.content.isEmpty && turn.streaming {
                    ProgressView().controlSize(.small)
                } else {
                    MessageText(turn.content)
                }
                if turn.stopped { Text("已停止").font(.system(size: 11)).foregroundStyle(.secondary) }
            }
            .font(.system(size: 14))
        }
    }
}

private struct Composer: View {
    @Bindable var model: WorkspaceModel
    @FocusState private var focused: Bool

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("输入消息，↩ 发送，⇧↩ 换行", text: $model.draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...8)
                .focused($focused)
                .onKeyPress(.return, phases: .down) { press in
                    if press.modifiers.contains(.shift) { return .ignored }
                    model.send()
                    return .handled
                }
            Button {
                if model.isSending { model.stop() } else { model.send() }
            } label: {
                Image(systemName: model.isSending ? "stop.circle.fill" : "arrow.up.circle.fill").font(.system(size: 22))
            }
            .buttonStyle(.plain)
            .foregroundStyle(canSend ? Color.accentColor : .secondary)
            .disabled(!model.isSending && !canSend)
            .help(model.isSending ? "停止" : "发送")
        }
        .font(.system(size: 14))
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.quaternary))
        .frame(maxWidth: 720)
        .padding(.horizontal, 24).padding(.bottom, 16).padding(.top, 4)
        .frame(maxWidth: .infinity)
        .onAppear { focused = true }
    }

    private var canSend: Bool { !model.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
}
