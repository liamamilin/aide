import AideCore
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// "模型" tab: what is installed, what each model can do, and a way to download another one.
struct ModelsPane: View {
    @Bindable var model: SystemModel
    @State private var custom = ""

    var body: some View {
        Form {
            Section("已安装") {
                if let catalog = model.catalog {
                    if !catalog.reachable {
                        Label("无法连接 Ollama。请先运行 ollama serve。", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    } else if catalog.models.isEmpty {
                        Text("还没有模型，请在下面下载一个。").foregroundStyle(.secondary)
                    }
                    ForEach(catalog.models) { ModelRow(info: $0) }
                } else {
                    ProgressView().controlSize(.small)
                }
            }
            Section("下载模型") {
                ForEach(SystemModel.suggested, id: \.name) { item in
                    LabeledContent {
                        Button("下载") { Task { await model.pull(item.name) } }.disabled(model.pulling != nil)
                    } label: {
                        VStack(alignment: .leading) { Text(item.name); Text(item.note).font(.caption).foregroundStyle(.secondary) }
                    }
                }
                HStack {
                    TextField("其他模型名，如 llama3.2:3b", text: $custom).textFieldStyle(.roundedBorder)
                    Button("下载") { Task { await model.pull(custom) } }
                        .disabled(model.pulling != nil || custom.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if let name = model.pulling { PullRow(name: name, progress: model.progress) }
            }
            if let notice = model.notice { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
        }
        .formStyle(.grouped)
        .task { await model.refresh() }
    }
}

struct ModelRow: View {
    let info: ModelInfo
    var body: some View {
        LabeledContent {
            HStack(spacing: 6) {
                badge("图片", info.vision)
                badge("工具", info.tools)
                badge("思考", info.thinking)
            }
        } label: {
            VStack(alignment: .leading) {
                Text(info.name)
                if info.size > 0 {
                    Text(ByteCountFormatter.string(fromByteCount: info.size, countStyle: .file))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder private func badge(_ title: String, _ on: Bool?) -> some View {
        if on == true {
            Text(title).font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                .background(.quaternary, in: Capsule())
        }
    }
}

struct PullRow: View {
    let name: String
    let progress: PullProgress?
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("正在下载 \(name)").font(.callout)
            if let fraction = progress?.fraction { ProgressView(value: fraction) } else { ProgressView().controlSize(.small) }
            if let status = progress?.status, !status.isEmpty { Text(status).font(.caption).foregroundStyle(.secondary) }
        }
    }
}

/// "关于" tab: version and updates, history export, and a copyable diagnostics summary.
struct AboutPane: View {
    @Bindable var model: SystemModel
    let version: String

    var body: some View {
        Form {
            Section("版本") {
                LabeledContent("Aide", value: version)
                HStack {
                    Button("检查更新") { Task { await model.checkForUpdate() } }.disabled(model.isChecking)
                    if model.isChecking { ProgressView().controlSize(.small) }
                }
                if let update = model.update { updateRow(update) }
                Text("检查更新只会读取 GitHub 上公开的版本信息，不会上传任何内容。没有自动更新：下载新版后替换 Applications 里的 Aide。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("数据") {
                HStack {
                    Button("导出为 Markdown…") { export(.markdown) }
                    Button("导出为 JSON…") { export(.json) }
                }
                Button("复制诊断信息") {
                    Task {
                        guard let text = await model.copyDiagnostics() else { return }
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(text, forType: .string)
                    }
                }
                Text("诊断信息只含版本、系统和 Ollama 状态，不含对话内容。").font(.caption).foregroundStyle(.secondary)
            }
            if let notice = model.notice { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder private func updateRow(_ update: UpdateInfo) -> some View {
        if update.available, let latest = update.latest {
            VStack(alignment: .leading, spacing: 4) {
                Label("有新版本 \(latest)", systemImage: "arrow.down.circle.fill").foregroundStyle(Theme.accent)
                if let url = update.download ?? update.page { Link("下载", destination: url) }
            }
        } else if update.latest == nil {
            Text("还没有发布的版本信息。").foregroundStyle(.secondary)
        } else {
            Label("已是最新版本", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        }
    }

    private func export(_ format: ExportFormat) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = format == .json ? "aide-history.json" : "aide-history.md"
        panel.allowedContentTypes = [format == .json ? .json : (UTType(filenameExtension: "md") ?? .plainText)]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await model.export(to: url, format: format) }
    }
}

/// "联网搜索" tab. Search is off in every conversation until switched on in the Workspace toolbar.
struct SearchPane: View {
    @Bindable var model: SearchSettingsModel

    var body: some View {
        Form {
            Section {
                Text("默认关闭。在 Workspace 的某个对话里打开「联网搜索」后，模型才可能把搜索词发送到下面选择的搜索服务；其他对话和快捷面板不会联网。对话内容、剪贴板和选中的文字不会被发送，只有模型生成的搜索词。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section("搜索服务") {
                Picker("服务", selection: $model.provider) {
                    ForEach(model.status?.providers ?? ["exa", "parallel"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                LabeledContent("密钥") {
                    if model.status?.hasKey == true {
                        Label("已保存", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else {
                        Text("未保存").foregroundStyle(.secondary)
                    }
                }
                SecureField("粘贴 API 密钥", text: $model.keyDraft).textFieldStyle(.roundedBorder)
                HStack {
                    Button("保存到钥匙串") { Task { await model.saveKey() } }.disabled(!model.canSave)
                    Button("移除密钥") { Task { await model.removeKey() } }.disabled(model.status?.hasKey != true)
                }
                Text("密钥只保存在 macOS 钥匙串，Aide 不会再显示它。").font(.caption).foregroundStyle(.secondary)
            }
            if let notice = model.notice { Label(notice, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
        }
        .formStyle(.grouped)
        .task { await model.load() }
    }
}

/// "命令行" tab. Off in every conversation until switched on in the Workspace toolbar.
struct ShellPane: View {
    @Bindable var model: ShellSettingsModel

    var body: some View {
        Form {
            Section {
                Text("默认关闭。在 Workspace 的某个对话里打开「命令行」后，模型才能在下面选定的文件夹里运行命令。命令不会继承你的环境变量和密钥。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Section("工作文件夹") {
                if let status = model.status, !status.workspace.isEmpty {
                    LabeledContent("位置") {
                        Text(status.workspace).lineLimit(1).truncationMode(.middle).textSelection(.enabled)
                    }
                    if !status.usable {
                        Label("这个文件夹已不存在或已变更，请重新选择。", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                } else {
                    Text("还没有选择").foregroundStyle(.secondary)
                }
                HStack {
                    Button("选择文件夹…") { pick() }
                    Button("清除") { Task { await model.clear() } }
                        .disabled((model.status?.workspace ?? "").isEmpty)
                }
                Text("请选一个具体的项目文件夹，不能选根目录或整个个人文件夹。").font(.caption).foregroundStyle(.secondary)
            }
            Section("确认方式") {
                Picker("策略", selection: $model.policy) {
                    Text("只读命令自动运行，其余先确认").tag(ShellPolicy.readonlyAuto)
                    Text("每条命令都先确认").tag(ShellPolicy.confirmAll)
                }
                .pickerStyle(.radioGroup)
                .onChange(of: model.policy) { _, _ in Task { await model.applyPolicy() } }
                Text("自动运行的只有 pwd、ls、cat、head、tail、wc，且只读取工作文件夹里的文件。其余命令会显示完整命令和所在文件夹，你批准后才运行。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
        }
        .formStyle(.grouped)
        .task { await model.load() }
    }

    private func pick() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await model.choose(url.path) }
    }
}
