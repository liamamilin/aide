import AideCore
import AppKit
import ApplicationServices
import SwiftUI

/// First-run checklist: Ollama running, one model installed, Accessibility granted. Every row can be skipped;
/// typing a request in the Quick Panel works without Accessibility.
struct OnboardingView: View {
    @Bindable var model: SystemModel
    var onDone: () -> Void
    @State private var trusted = AXIsProcessTrusted()
    private let poll = Timer.publish(every: 1.5, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            VStack(alignment: .leading, spacing: 4) {
                Text("欢迎使用 Aide").font(.title2.weight(.semibold))
                Text("三步就能开始。一切都在你的 Mac 上运行。").foregroundStyle(.secondary)
            }
            step(1, "Ollama", done: model.catalog?.reachable == true) {
                if model.catalog?.reachable == true {
                    Text("已连接").foregroundStyle(.secondary)
                } else {
                    Text("没有检测到 Ollama。").foregroundStyle(.secondary)
                    HStack {
                        Link("下载 Ollama", destination: URL(string: "https://ollama.com/download")!)
                        Text("安装后打开它，或在终端运行 ollama serve").font(.caption).foregroundStyle(.secondary)
                    }
                    Button("重新检测") { Task { await model.refresh() } }
                }
            }
            step(2, "模型", done: !(model.catalog?.models.isEmpty ?? true)) {
                if let models = model.catalog?.models, !models.isEmpty {
                    Text("已安装 \(models.count) 个，例如 \(models[0].name)").foregroundStyle(.secondary)
                } else if let name = model.pulling {
                    PullRow(name: name, progress: model.progress)
                } else {
                    ForEach(SystemModel.suggested, id: \.name) { item in
                        HStack {
                            Button("下载 \(item.name)") { Task { await model.pull(item.name) } }
                                .disabled(model.catalog?.reachable != true)
                            Text(item.note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            step(3, "辅助功能（可选）", done: trusted) {
                Text(trusted ? "已允许" : "用来读取你在其他 App 里选中的文字。不开启也能直接输入请求。")
                    .foregroundStyle(.secondary)
                if !trusted {
                    Button("打开系统设置") {
                        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
                    }
                }
            }
            if let error = model.errorMessage { Text(error).foregroundStyle(.red).font(.callout) }
            HStack {
                Spacer()
                Button(model.needsSetup ? "稍后再说" : "开始使用", action: onDone).keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 480)
        .task { await model.refresh() }
        .onReceive(poll) { _ in
            trusted = AXIsProcessTrusted()
            if model.pulling == nil { Task { await model.refresh() } }
        }
    }

    private func step<Content: View>(_ number: Int, _ title: String, done: Bool,
                                     @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: done ? "checkmark.circle.fill" : "\(number).circle")
                .font(.title3).foregroundStyle(done ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                content()
            }
        }
    }
}

/// Shown once at launch when Aide cannot do anything yet; "稍后再说" still counts as seen.
@MainActor
public final class OnboardingWindowController: NSObject, NSWindowDelegate {
    private static let seenKey = "onboardingSeen"
    private let model: SystemModel
    private let defaults: UserDefaults
    private var window: NSWindow?

    public init(model: SystemModel, defaults: UserDefaults = .standard) {
        self.model = model; self.defaults = defaults
    }

    /// Opens once, on a fresh install. The menu bar item reopens it any time.
    public func showIfNeeded() async {
        await model.refresh()
        guard !defaults.bool(forKey: Self.seenKey) else { return }
        show()
    }

    public func show() {
        if window == nil {
            let view = OnboardingView(model: model) { [weak self] in self?.finish() }
            let window = NSWindow(contentViewController: NSHostingController(rootView: view))
            window.styleMask = [.titled, .closable]
            window.title = "欢迎使用 Aide"
            window.isReleasedWhenClosed = false
            window.delegate = self
            window.center()
            self.window = window
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    private func finish() {
        defaults.set(true, forKey: Self.seenKey)
        window?.close()
    }

    public func windowWillClose(_ notification: Notification) {
        defaults.set(true, forKey: Self.seenKey)
        NSApp.setActivationPolicy(.accessory)
    }
}
