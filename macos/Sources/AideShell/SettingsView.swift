import AideCore
import SwiftUI

public struct SettingsView: View {
    @Bindable var model: SettingsModel

    public init(model: SettingsModel) { self.model = model }

    public var body: some View {
        TabView {
            GeneralPane(model: model)
                .tabItem { Label("通用", systemImage: "gearshape") }
            AgentsPane(model: model)
                .tabItem { Label("助手", systemImage: "person.crop.square") }
        }
        .frame(width: 560, height: 480)
        .task { await model.load() }
    }
}

private struct GeneralPane: View {
    @Bindable var model: SettingsModel
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
                    LabeledContent("快捷面板", value: "⌘⌃L")
                }
                Section {
                    DisclosureGroup("高级", isExpanded: $showAdvanced) {
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
                    TextField("图标", text: binding(\.icon)).frame(width: 50)
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

    private func binding(_ keyPath: WritableKeyPath<AgentDraft, String>) -> Binding<String> {
        Binding(get: { model.editing?[keyPath: keyPath] ?? "" }, set: { model.editing?[keyPath: keyPath] = $0 })
    }
}
