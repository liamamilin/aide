import AideCore
import AideShell
import AppKit

/// Dev launcher: spawns the Python core from the source tree. Packaged builds embed the core (P2).
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var core: CoreClient?
    private var hotkey: Hotkey?
    private var controller: QuickPanelController?
    private var workspace: WorkspaceWindowController?
    private var settings: SettingsWindowController?
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let client = CoreClient(launch: Self.devLaunch())
        core = client
        Task { @MainActor in
            do { try await client.start() } catch { NSLog("Aide core failed to start: \(error)") }
        }
        let model = QuickPanelModel(provider: client)
        let controller = QuickPanelController(model: model, reader: SelectionReader())
        self.controller = controller
        controller.onNoSelection = { [weak self] in self?.workspace?.show() }
        let workspace = WorkspaceWindowController(model: WorkspaceModel(provider: client))
        self.workspace = workspace
        let settings = SettingsWindowController(model: SettingsModel(provider: client))
        self.settings = settings
        workspace.model.onOpenSettings = { settings.show() }
        model.onContinue = { selection, action, answer in
            Task { @MainActor in
                let id = try? await client.createConversation(
                    agentID: action.agentID, seed: [(.user, selection), (.assistant, answer)])
                workspace.show(conversationID: id)
            }
        }
        hotkey = Hotkey(.quickPanel) { Task { @MainActor in controller.toggle() } }
        if hotkey == nil { NSLog("Aide: ⌘⌃L is already taken by another app") }

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "Aide")
        let menu = NSMenu()
        menu.addItem(withTitle: "快捷面板  ⌘⌃L", action: #selector(showPanel), keyEquivalent: "").target = self
        menu.addItem(withTitle: "打开 Aide", action: #selector(showWorkspace), keyEquivalent: "").target = self
        menu.addItem(withTitle: "设置…", action: #selector(showSettings), keyEquivalent: ",").target = self
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

    @objc private func showWorkspace() { workspace?.show() }

    @objc private func showSettings() { settings?.show() }

    @objc private func showPanel() { controller?.toggle() }

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
