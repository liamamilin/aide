import AideCore
import AppKit
import SwiftUI

public struct WorkspaceView: View {
    @Bindable var model: WorkspaceModel
    @State private var sidebarSelection: Int?
    @State private var followsBottom = true

    public init(model: WorkspaceModel) { self.model = model }

    public var body: some View {
        NavigationSplitView {
            sidebar
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 320)
        } detail: {
            detail
        }
        .navigationTitle(model.title)
        .tint(Theme.accent)
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button { model.newChat() } label: { Label("新对话", systemImage: "square.and.pencil") }
                    .help("新对话 ⌘N")
                    .keyboardShortcut("n")
            }
            ToolbarItem(placement: .primaryAction) { searchToggle }
            ToolbarItem(placement: .primaryAction) { agentMenu }
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
                HStack(spacing: 10) {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.system(size: 12)).foregroundStyle(.red)
                    if model.errorOffersSettings {
                        Button("打开设置") { model.onOpenSettings() }.controlSize(.small)
                    }
                    Spacer()
                }
                .padding(.horizontal, 24).padding(.bottom, 6)
            }
            if let request = model.pendingConfirmation { ConfirmationCard(request: request) { model.respond(approved: $0) } }
            else if let activity = model.activity {
                Text(activity)
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 24).padding(.bottom, 6)
            }
            Composer(model: model)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            if model.modelServiceUnavailable {
                Text("没有连上模型服务").font(.system(size: 20, weight: .medium))
                Text("请确认 Ollama 已运行并装有模型，或在设置里检查服务地址。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                HStack {
                    Button(model.isCheckingModels ? "检测中…" : "重新检测") { Task { await model.refreshModels(manual: true) } }
                        .disabled(model.isCheckingModels)
                    Button("打开设置") { model.onOpenSettings() }
                }
                .padding(.top, 4)
                if model.recheckFailed {
                    Text("仍然没有检测到可用的模型。").font(.system(size: 12)).foregroundStyle(.orange)
                }
            } else {
                Text("有什么可以帮你？").font(.system(size: 20, weight: .medium))
                Text(model.currentModel).font(.system(size: 12)).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 22) {
                    ForEach(model.turns) { turn in TurnView(turn: turn, model: model).id(turn.id) }
                }
                .frame(maxWidth: 720)
                .padding(.horizontal, 24)
                .padding(.vertical, 20)
                .frame(maxWidth: .infinity)
                .background(ScrollFollower { followsBottom = $0 })
            }
            // New text follows only while the reader is at the bottom; scrolling up to read stops it.
            .onChange(of: model.turns.last?.content) { _, _ in
                if followsBottom { scrollToLatest(proxy) }
            }
            .onChange(of: model.turns.count) { _, _ in
                if followsBottom || model.turns.last?.role == .user { scrollToLatest(proxy) }
            }
            .onChange(of: model.selectedID) { _, _ in
                followsBottom = true
                DispatchQueue.main.async { scrollToLatest(proxy) }
            }
            .overlay(alignment: .bottom) {
                if !followsBottom {
                    Button { followsBottom = true; scrollToLatest(proxy) } label: {
                        Label("回到最新", systemImage: "arrow.down").font(.system(size: 12))
                    }
                    .buttonStyle(.borderedProminent).controlSize(.small).clipShape(Capsule())
                    .padding(.bottom, 10)
                    .transition(.opacity)
                }
            }
            .animation(.easeOut(duration: 0.15), value: followsBottom)
        }
    }

    private func scrollToLatest(_ proxy: ScrollViewProxy) {
        if let last = model.turns.last { proxy.scrollTo(last.id, anchor: .bottom) }
    }

    /// Applies from the next message on, in new and existing conversations alike.
    private var agentMenu: some View {
        Menu {
            ForEach(model.agents) { agent in
                Button { Task { await model.chooseAgent(agent.id) } } label: {
                    if agent.id == model.selectedAgentID { Label("\(agent.icon) \(agent.name)", systemImage: "checkmark") }
                    else { Text("\(agent.icon) \(agent.name)") }
                }
            }
        } label: {
            let current = model.agents.first { $0.id == model.selectedAgentID }
            Text(current.map { "\($0.icon) \($0.name)" } ?? "助手").font(.system(size: 12))
        }
        .disabled(model.isSending)
        .help(model.isSending ? "正在回答，完成后可切换助手" : "选择助手，下一条消息起生效")
    }

    @ViewBuilder private var searchToggle: some View {
        if model.searchAvailable {
            Button { Task { await model.toggleSearch() } } label: {
                Label(model.searchEnabled ? "联网搜索：开" : "联网搜索", systemImage: "globe")
                    .font(.system(size: 12))
                    .foregroundStyle(model.searchEnabled ? Theme.accent : Color.secondary)
            }
            .disabled(model.isSending)
            .help(model.searchEnabled
                  ? "已开启：这个对话里模型可能把搜索词发送到搜索服务。点击关闭。"
                  : "默认关闭。开启后，模型可能把搜索词发送到你配置的搜索服务。")
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

private struct StepRow: View {
    let step: ToolStep

    var body: some View {
        HStack(spacing: 6) {
            switch step.state {
            case .running: ProgressView().controlSize(.mini)
            case .succeeded: Image(systemName: "checkmark.circle").foregroundStyle(.secondary)
            case .failed: Image(systemName: "xmark.circle").foregroundStyle(.orange)
            }
            Text(step.state == .failed ? "\(ToolLabels.title(step.name)) 未成功" : ToolLabels.title(step.name))
                .foregroundStyle(step.state == .failed ? Color.orange : Color.secondary)
        }
        .font(.system(size: 12))
        if let result = step.result { ToolResultView(result: result) }
    }
}

private struct TurnView: View {
    let turn: Turn
    let model: WorkspaceModel
    @State private var copied = false
    @State private var editing = false
    @State private var editText = ""

    private var isLast: Bool { model.turns.last?.id == turn.id }
    private var isLastQuestion: Bool { model.turns.dropLast().last?.id == turn.id && model.canRedo }

    var body: some View {
        switch turn.role {
        case .user:
            if editing {
                VStack(alignment: .trailing, spacing: 6) {
                    TextField("", text: $editText, axis: .vertical)
                        .textFieldStyle(.roundedBorder).lineLimit(1...8)
                    HStack {
                        Button("取消") { editing = false }
                        Button("重新发送") { editing = false; model.editLast(editText) }
                            .keyboardShortcut(.defaultAction).disabled(editText.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            } else {
                HStack(alignment: .center, spacing: 6) {
                    Spacer(minLength: 60)
                    if isLastQuestion {
                        Button { editText = turn.content; editing = true } label: { Image(systemName: "pencil") }
                            .buttonStyle(.plain).foregroundStyle(.secondary).help("编辑并重新发送")
                    }
                    VStack(alignment: .trailing, spacing: 6) {
                        if let url = turn.imageURL, let picture = NSImage(contentsOf: url) {
                            Image(nsImage: picture).resizable().scaledToFit().frame(maxWidth: 280, maxHeight: 200)
                                .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                        Text(turn.content).textSelection(.enabled)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(Theme.accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 12))
                }
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 6) {
                ForEach(turn.steps) { StepRow(step: $0) }
                if turn.content.isEmpty && turn.streaming && turn.steps.isEmpty {
                    ProgressView().controlSize(.small)
                } else {
                    MessageText(turn.content)
                }
                if turn.stopped { Text(turn.steps.contains { $0.state == .succeeded && !ToolLabels.isLookup($0.name) }
                                        ? "已停止后续执行；已完成的操作不会撤销" : "已停止").font(.system(size: 11)).foregroundStyle(.secondary) }
                if !turn.streaming && !turn.content.isEmpty {
                    HStack(spacing: 14) {
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(turn.content, forType: .string)
                            copied = true
                            Task { try? await Task.sleep(nanoseconds: 1_500_000_000); copied = false }
                        } label: { Label(copied ? "已复制" : "复制", systemImage: copied ? "checkmark" : "doc.on.doc") }
                        if isLast && model.canRedo {
                            Button { model.regenerate() } label: { Label("重新生成", systemImage: "arrow.clockwise") }
                        }
                    }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(.secondary)
                }
            }
            .font(.system(size: 14))
        }
    }
}

/// SwiftUI's `.scrollIndicators(.hidden)` leaves the legacy scroller track (a white bar) on an NSTextView
/// when the system shows scroll bars permanently. The composer is capped at 8 lines and still scrolls with
/// the wheel, so the scroller is removed from the editor's own scroll view.
struct HiddenScroller: NSViewRepresentable {
    /// Re-applies on every layout pass: SwiftUI adds the editor's scroll view after this view and may reset it.
    final class Probe: NSView {
        private var observation: NSKeyValueObservation?
        private weak var observed: NSScrollView?

        override func layout() {
            super.layout()
            guard let scroll = HiddenScroller.scrollView(near: self) else { return }
            scroll.hasVerticalScroller = false
            guard observed !== scroll else { return }
            observed = scroll
            // SwiftUI re-enables the scroller whenever it updates the editor, so keep it off.
            observation = scroll.observe(\.hasVerticalScroller, options: [.new]) { scroll, change in
                if change.newValue == true { scroll.hasVerticalScroller = false }
            }
        }
    }

    func makeNSView(context: Context) -> NSView { Probe() }

    func updateNSView(_ view: NSView, context: Context) { view.needsLayout = true }

    /// Walks up a few levels to the first small scroll view beneath this view's ancestors.
    static func scrollView(near view: NSView) -> NSScrollView? {
        var node = view.superview
        for _ in 0..<4 {
            guard let current = node else { return nil }
            if let scroll = firstScrollView(in: current), scroll.frame.height <= 300 { return scroll }
            node = current.superview
        }
        return nil
    }

    private static func firstScrollView(in view: NSView) -> NSScrollView? {
        if let scroll = view as? NSScrollView { return scroll }
        for child in view.subviews { if let found = firstScrollView(in: child) { return found } }
        return nil
    }
}

/// Asks before a tool changes anything. The text shown is the exact action; Esc denies, and nothing
/// is approved by Return, so a stray keypress cannot allow it.
struct ConfirmationCard: View {
    let request: ConfirmRequest
    let answer: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("需要你确认", systemImage: "exclamationmark.shield").font(.system(size: 13, weight: .semibold))
            Text(request.preview)
                .font(.system(size: 13, design: .monospaced))
                .textSelection(.enabled)
                .padding(8)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            if !request.reason.isEmpty { Text(request.reason).font(.system(size: 12)).foregroundStyle(.secondary) }
            HStack {
                Spacer()
                Button("拒绝") { answer(false) }.keyboardShortcut(.cancelAction)
                Button("允许一次") { answer(true) }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.6)))
        .frame(maxWidth: 720)
        .padding(.horizontal, 24).padding(.bottom, 6)
        .frame(maxWidth: .infinity)
    }
}

struct Composer: View {
    @Bindable var model: WorkspaceModel
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            if let image = model.attachment { attachmentChip(image) }
            field
            // The hint lives outside the field: text being composed by an input method is not in the
            // binding yet, so an in-field placeholder would draw underneath the composition.
            Text("↩ 发送  ·  ⇧↩ 换行")
                .font(.system(size: 11)).foregroundStyle(.tertiary)
                .padding(.top, 4).padding(.bottom, 10)
        }
        .onAppear { focused = true }
        .onChange(of: model.focusRequest) { _, _ in focused = true }
    }

    private func attachmentChip(_ image: URL) -> some View {
        HStack {
            HStack(spacing: 8) {
                if let picture = NSImage(contentsOf: image) {
                    Image(nsImage: picture).resizable().scaledToFill().frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                }
                Text("截图").font(.system(size: 12)).foregroundStyle(.secondary)
                Button { model.clearAttachment() } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary).help("移除截图")
            }
            .padding(6)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
            Spacer()
        }
        .frame(maxWidth: 720)
        .padding(.horizontal, 24).padding(.bottom, 4)
        .frame(maxWidth: .infinity)
    }

    private var field: some View {
        HStack(alignment: .bottom, spacing: 8) {
            // TextEditor (an NSTextView) gives native ⇧↩ newline; ↩ is intercepted to send.
            TextEditor(text: $model.draft)
                .font(.system(size: 14))
                .scrollContentBackground(.hidden)
                .background(HiddenScroller())
                .focused($focused)
                .frame(height: editorHeight)
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
            .foregroundStyle(canSend || model.isSending ? Theme.accent : .secondary)
            .disabled(!model.isSending && !canSend)
            .help(model.isSending ? "停止" : "发送")
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.quaternary))
        .frame(maxWidth: 720)
        .padding(.horizontal, 24).padding(.top, 4)
        .frame(maxWidth: .infinity)
    }

    /// Grows with explicit lines (1…8); long wrapped lines scroll inside the editor.
    private var editorHeight: CGFloat {
        let lines = max(1, min(8, model.draft.components(separatedBy: "\n").count))
        return CGFloat(lines) * 19 + 8
    }

    private var canSend: Bool { model.canSend }
}

/// Reports whether the enclosing scroll view sits at the bottom. It reacts to the scroll position only,
/// so content growing underneath a reader who scrolled up never reads as "back at the bottom".
struct ScrollFollower: NSViewRepresentable {
    let onChange: (Bool) -> Void

    final class Probe: NSView {
        var onChange: (Bool) -> Void = { _ in }
        private var token: NSObjectProtocol?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard window != nil, token == nil else { return }
            DispatchQueue.main.async { [weak self] in self?.attach() }
        }

        private func attach() {
            guard let scroll = enclosingScrollView else { return }
            scroll.contentView.postsBoundsChangedNotifications = true
            token = NotificationCenter.default.addObserver(forName: NSView.boundsDidChangeNotification,
                                                           object: scroll.contentView, queue: .main) { [weak self, weak scroll] _ in
                guard let scroll, let document = scroll.documentView else { return }
                let distance = document.frame.height - scroll.contentView.bounds.maxY
                self?.onChange(distance < 60)
            }
        }

        deinit { if let token { NotificationCenter.default.removeObserver(token) } }
    }

    func makeNSView(context: Context) -> Probe { let probe = Probe(); probe.onChange = onChange; return probe }

    func updateNSView(_ view: Probe, context: Context) { view.onChange = onChange }
}
