import AideCore
import AideShell
import AppKit

/// Spawns the Python core: the frozen helper inside the .app when packaged, else from the source tree (dev).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var core: CoreClient?
    private var hotkey: Hotkey?
    private var shotKey: Hotkey?
    private var controller: QuickPanelController?
    private var workspace: WorkspaceWindowController?
    private var settings: SettingsWindowController?
    private var onboarding: OnboardingWindowController?
    private var statusItem: NSStatusItem?
    private var panelItem: NSMenuItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let client = CoreClient(launch: Self.coreLaunch())
        core = client
        Task { @MainActor in
            do { try await client.start() } catch { NSLog("Aide core failed to start: \(error)") }
        }
        let model = QuickPanelModel(provider: client, ask: client)
        let controller = QuickPanelController(model: model, reader: SelectionReader())
        self.controller = controller
        let workspace = WorkspaceWindowController(model: WorkspaceModel(provider: client))
        self.workspace = workspace
        let hotkeys = HotkeySettings { [weak self] combo in
            guard let registered = Hotkey(combo, action: { Task { @MainActor in controller.toggle() } }) else { return false }
            self?.hotkey = registered
            self?.panelItem?.title = "快捷面板  \(combo.display)"
            return true
        }
        let system = SystemModel(provider: client)
        let onboarding = OnboardingWindowController(model: system)
        self.onboarding = onboarding
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 600_000_000)   // let the core finish starting
            await onboarding.showIfNeeded()
        }
        let settings = SettingsWindowController(
            model: SettingsModel(provider: client), hotkey: hotkeys, system: system,
            version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "开发版")
        self.settings = settings
        workspace.model.onOpenSettings = { settings.show() }
        model.onOpenConversation = { id in Task { @MainActor in workspace.show(conversationID: id) } }
        model.onContinue = { selection, action, answer in
            Task { @MainActor in
                let id = try? await client.createConversation(
                    agentID: action.agentID, seed: [(.user, selection), (.assistant, answer)])
                workspace.show(conversationID: id)
            }
        }

        shotKey = Hotkey(.screenshot, id: 2) { Task { @MainActor in self.askAboutScreen() } }
        if shotKey == nil { NSLog("Aide: ⌘⌃S is already taken by another app") }

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "Aide")
        let menu = NSMenu()
        let panelItem = menu.addItem(withTitle: "快捷面板  \(hotkeys.combo.display)", action: #selector(showPanel), keyEquivalent: "")
        panelItem.target = self
        menu.addItem(withTitle: "截图提问  ⌘⌃S", action: #selector(askAboutScreenFromMenu), keyEquivalent: "").target = self
        self.panelItem = panelItem
        hotkeys.activate()
        menu.addItem(withTitle: "打开 Aide", action: #selector(showWorkspace), keyEquivalent: "").target = self
        menu.addItem(withTitle: "设置…", action: #selector(showSettings), keyEquivalent: ",").target = self
        menu.addItem(withTitle: "新手引导…", action: #selector(showOnboarding), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出 Aide", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu
        statusItem = item
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard let core else { return }
        let done = DispatchSemaphore(value: 0)
        Task { await core.stop(); done.signal() }
        _ = done.wait(timeout: .now() + 2)
    }

    @objc private func askAboutScreenFromMenu() { askAboutScreen() }

    /// Region screenshot → a new Workspace chat with the picture attached, ready for the question.
    private func askAboutScreen() {
        Task { @MainActor in
            guard let image = await ScreenCapture.captureRegion() else { return }   // Esc cancels
            workspace?.model.startScreenshotChat(image)
            workspace?.show()
        }
    }

    @objc private func showWorkspace() { workspace?.show() }

    @objc private func showSettings() { settings?.show() }

    @objc private func showOnboarding() { onboarding?.show() }

    @objc private func showPanel() { controller?.toggle() }

    private static func coreLaunch() -> CoreClient.Launch {
        let env = ProcessInfo.processInfo.environment
        let helper = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/aide-core/aide-core")
        guard FileManager.default.isExecutableFile(atPath: helper.path) else { return devLaunch() }
        var childEnv: [String: String] = [:]
        for key in ["AIDE_DATA_DIR", "AIDE_OLLAMA_URL", "AIDE_LOG_DIR"] { if let v = env[key] { childEnv[key] = v } }
        return .init(executable: helper, arguments: [], environment: childEnv)
    }

    private static func devLaunch() -> CoreClient.Launch {
        let env = ProcessInfo.processInfo.environment
        let repo = env["AIDE_REPO"] ?? URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().path
        var childEnv = ["PYTHONPATH": repo]
        for key in ["AIDE_DATA_DIR", "AIDE_OLLAMA_URL", "AIDE_LOG_DIR"] { if let v = env[key] { childEnv[key] = v } }
        return .init(executable: URL(fileURLWithPath: "/usr/bin/env"),
                     arguments: [env["AIDE_PYTHON"] ?? "python3", "-m", "ai_desktop.core"], environment: childEnv)
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)   // menu-bar only: no Dock icon, never steals focus
    app.run()
}
