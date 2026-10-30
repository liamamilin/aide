import AppKit
import SwiftUI

/// The Settings window (⌘,). Like the Workspace, it shows Aide in the Dock only while open.
@MainActor
public final class SettingsWindowController: NSObject, NSWindowDelegate {
    private let model: SettingsModel
    private var window: NSWindow?

    private let hotkey: HotkeySettings?
    private let system: SystemModel?
    private let search: SearchSettingsModel?
    private let shell: ShellSettingsModel?
    private let version: String

    public init(model: SettingsModel, hotkey: HotkeySettings? = nil, system: SystemModel? = nil,
                search: SearchSettingsModel? = nil, shell: ShellSettingsModel? = nil, version: String = "") {
        self.model = model; self.hotkey = hotkey; self.system = system; self.search = search
        self.shell = shell; self.version = version
    }

    public func show() {
        if window == nil {
            let window = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(model: model, hotkey: hotkey, system: system, search: search, shell: shell, version: version)))
            window.styleMask = [.titled, .closable]
            window.title = "Aide 设置"
            window.isReleasedWhenClosed = false
            window.delegate = self
            window.center()
            self.window = window
        }
        Task { await model.load() }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        window?.orderFrontRegardless()
    }

    public func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
