import AppKit
import SwiftUI

/// The Settings window (⌘,). Like the Workspace, it shows Aide in the Dock only while open.
@MainActor
public final class SettingsWindowController: NSObject, NSWindowDelegate {
    private let model: SettingsModel
    private var window: NSWindow?

    public init(model: SettingsModel) { self.model = model }

    public func show() {
        if window == nil {
            let window = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(model: model)))
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
    }

    public func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
