import AideCore
import SwiftUI

public struct SettingsView: View {
    @Bindable var model: SettingsModel
    let hotkey: HotkeySettings?
    let system: SystemModel?
    let version: String

    public init(model: SettingsModel, hotkey: HotkeySettings? = nil, system: SystemModel? = nil, version: String = "") {
        self.model = model; self.hotkey = hotkey; self.system = system; self.version = version
    }

    public var body: some View {
        TabView {
            GeneralPane(model: model, hotkey: hotkey)
                .tabItem { Label("通用", systemImage: "gearshape") }
            AgentsPane(model: model)
                .tabItem { Label("助手", systemImage: "person.crop.square") }
            ActionsPane(model: model)
                .tabItem { Label("动作", systemImage: "bolt") }
            if let system {
                ModelsPane(model: system)
                    .tabItem { Label("模型", systemImage: "cpu") }
                AboutPane(model: system, version: version)
                    .tabItem { Label("关于", systemImage: "info.circle") }
            }
        }
        .frame(width: 560, height: 480)
        .tint(Theme.accent)
        .task { await model.load() }
    }
}

private struct GeneralPane: View {
    @Bindable var model: SettingsModel
    let hotkey: HotkeySettings?
    @State private var showAdvanced = false

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section("模型服务") {
                    HStack {
                        TextField("Ollama 地址", text: $model.settings.baseURL)
                            .textFieldStyle(.roundedBorder)
                        Button("检测") { Task { await model.testConnection() } }
                            .disabled(model.isTesting)
                    }
                    connectionRow
                    Picker("思考", selection: $model.settings.think) {
                        Text("模型默认").tag(ThinkMode.modelDefault)
                        Text("开启").tag(ThinkMode.on)
                        Text("关闭（更快）").tag(ThinkMode.off)
                    }
                }
                Section("快捷键") {
                    if let hotkey {
                        HotkeyRow(hotkey: hotkey)
                    } else {
                        LabeledContent("快捷面板", value: "⌘⌃L")
                    }
                }
                Section {
                    Button {
                        withAnimation(.easeInOut(duration: 0.15)) { showAdvanced.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "chevron.right").rotationEffect(.degrees(showAdvanced ? 90 : 0))
                                .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                            Text("高级")
                            Spacer()
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if showAdvanced {
                        number("上下文长度", value: $model.settings.numCtx)
                        number("最大输出", value: $model.settings.numPredict)
                        number("超时（秒）", value: $model.settings.timeout)
                        number("保留对话轮数", value: $model.settings.maxRounds)
                        decimal("Temperature", value: $model.settings.temperature)
                        decimal("Top P", value: $model.settings.topP)
                        number("Top K", value: $model.settings.topK)
                        decimal("重复惩罚", value: $model.settings.repeatPenalty)
                    }
                }
            }
            .formStyle(.grouped)
            Divider()
            HStack {
                if let error = model.errorMessage {
                    Text(error).foregroundStyle(.red).font(.callout).lineLimit(2)
                }
                Spacer()
                Button("还原") { model.revert() }.disabled(!model.isDirty)
                Button("保存") { Task { await model.save() } }
                    .keyboardShortcut("s")
                    .disabled(!model.isDirty)
            }
            .padding(12)
        }
    }

    @ViewBuilder private var connectionRow: some View {
        if model.isTesting {
            Label("正在连接…", systemImage: "ellipsis.circle").foregroundStyle(.secondary)
        } else if let status = model.connection {
            if status.ok {
                Label("已连接，\(status.modelCount) 个模型可用", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
            } else {
                Label("无法连接，或没有可用模型。请确认 ollama serve 已运行。", systemImage: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
            }
        }
    }

    private func number(_ title: String, value: Binding<Int>) -> some View {
        LabeledContent(title) {
            TextField("", value: value, format: .number.grouping(.never)).multilineTextAlignment(.trailing).frame(width: 110)
        }
    }

    private func decimal(_ title: String, value: Binding<Double>) -> some View {
        LabeledContent(title) {
            TextField("", value: value, format: .number).multilineTextAlignment(.trailing).frame(width: 110)
        }
    }
}

private struct AgentsPane: View {
    @Bindable var model: SettingsModel

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                List(model.agents, selection: Binding(
                    get: { model.editing?.id },
                    set: { id in if let agent = model.agents.first(where: { $0.id == id }) { model.edit(agent) } }
                )) { agent in
                    Text("\(agent.icon) \(agent.name)")
                    .tag(agent.id as String?)
                }
                Divider()
                HStack {
                    Button { model.newAgent() } label: { Image(systemName: "plus") }
                    Button {
                        if let id = model.editing?.id { Task { await model.deleteAgent(id) } }
                    } label: { Image(systemName: "minus") }
                    .disabled(model.editing?.id == nil || model.editing?.builtin == true)
                    Spacer()
                }
                .buttonStyle(.borderless)
                .padding(8)
            }
            .frame(width: 170)
            Divider()
            editor
        }
    }

    @ViewBuilder private var editor: some View {
        if let draft = model.editing {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    iconMenu(draft)
                    TextField("名称", text: binding(\.name))
                }
                .disabled(draft.builtin)
                Text("提示词").font(.callout).foregroundStyle(.secondary)
                TextEditor(text: binding(\.systemPrompt))
                    .font(.body)
                    .disabled(draft.builtin)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(.separator))
                if draft.builtin {
                    Text("内置助手不能修改。想要不同的行为，可以新建一个自定义助手。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                HStack {
                    if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout).lineLimit(2) }
                    Spacer()
                    Button("保存") { Task { await model.saveAgent() } }
                        .disabled(!model.canSaveAgent)
                }
            }
            .padding(14)
        } else {
            VStack {
                Spacer()
                Text("选择一个助手，或点 + 新建").foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity)
        }
    }

    private static let icons = ["🤖", "💡", "📝", "📚", "🧠", "🔍", "💻", "🌐", "✍️", "📊", "🎨", "🎯", "🗂️", "🧪", "🛠️", "😎"]

    private func iconMenu(_ draft: AgentDraft) -> some View {
        Menu {
            ForEach(Self.icons, id: \.self) { icon in
                Button(icon) { model.editing?.icon = icon }
            }
        } label: {
            Text(draft.icon.isEmpty ? "🤖" : draft.icon).font(.system(size: 16))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("选择图标")
    }

    private func binding(_ keyPath: WritableKeyPath<AgentDraft, String>) -> Binding<String> {
        Binding(get: { model.editing?[keyPath: keyPath] ?? "" }, set: { model.editing?[keyPath: keyPath] = $0 })
    }
}

private struct HotkeyRow: View {
    let hotkey: HotkeySettings

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            LabeledContent("快捷面板") {
                HStack {
                    Button(hotkey.isRecording ? "请按下新的快捷键…（Esc 取消）" : hotkey.combo.display) {
                        hotkey.isRecording ? hotkey.stopRecording() : hotkey.startRecording()
                    }
                    if hotkey.combo != .quickPanel && !hotkey.isRecording {
                        Button("恢复默认") { hotkey.reset() }
                    }
                }
            }
            if let error = hotkey.error {
                Text(error).font(.system(size: 11)).foregroundStyle(.orange)
            }
        }
    }
}

private struct ActionsPane: View {
    @Bindable var model: SettingsModel

    var body: some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                List(model.actions, selection: Binding(
                    get: { model.editingAction?.source?.id },
                    set: { id in if let action = model.actions.first(where: { $0.id == id }) { model.edit(action) } }
                )) { action in
                    Text(action.name).foregroundStyle(action.enabled ? .primary : .secondary).tag(action.id as String?)
                }
                Divider()
                HStack {
                    Button { model.newAction() } label: { Image(systemName: "plus") }
                    Spacer()
                }
                .buttonStyle(.borderless).padding(8)
            }
            .frame(width: 170)
            Divider()
            editor
        }
    }

    @ViewBuilder private var editor: some View {
        if let draft = model.editingAction {
            VStack(alignment: .leading, spacing: 10) {
                TextField("名称，例如：润色成邮件语气", text: binding(\.name)).textFieldStyle(.roundedBorder)
                Picker("使用助手", selection: binding(\.agentID)) {
                    ForEach(model.agents) { Text("\($0.icon) \($0.name)").tag($0.id) }
                }
                Text("指令").font(.system(size: 12)).foregroundStyle(.secondary)
                TextEditor(text: binding(\.instruction))
                    .font(.system(size: 13))
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
                Text("选中的文字会作为材料交给助手，按这条指令处理。").font(.system(size: 11)).foregroundStyle(.secondary)
                Toggle("在快捷面板中显示", isOn: binding(\.enabled))
                if let error = model.errorMessage { Text(error).font(.system(size: 12)).foregroundStyle(.orange) }
                HStack {
                    Button(draft.builtin ? "恢复默认" : "删除", role: .destructive) {
                        if let id = draft.source?.id { Task { await model.deleteAction(id) } } else { model.editingAction = nil }
                    }
                    Spacer()
                    Button("保存") { Task { await model.saveAction() } }
                        .keyboardShortcut(.defaultAction).disabled(!model.canSaveAction)
                }
            }
            .padding(16)
        } else {
            VStack(spacing: 6) {
                Image(systemName: "bolt").font(.system(size: 22)).foregroundStyle(.secondary)
                Text("选一个动作编辑，或点 + 新建").foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func binding<V>(_ path: WritableKeyPath<ActionDraft, V>) -> Binding<V> {
        Binding(get: { model.editingAction![keyPath: path] }, set: { model.editingAction?[keyPath: path] = $0 })
    }
}
