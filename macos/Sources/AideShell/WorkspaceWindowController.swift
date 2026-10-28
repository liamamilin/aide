import AppKit
import SwiftUI

/// The full window: native title bar and toolbar. Aide shows in the Dock / Cmd-Tab only while it is open.
@MainActor
public final class WorkspaceWindowController: NSObject, NSWindowDelegate {
    public let model: WorkspaceModel
    private var window: NSWindow?

    public init(model: WorkspaceModel) { self.model = model }

    public func show(conversationID: Int? = nil) {
        if window == nil {
            let host = NSHostingController(rootView: WorkspaceView(model: model))
            let window = NSWindow(contentViewController: host)
            window.styleMask = [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]
            window.toolbarStyle = .unified
            window.setContentSize(NSSize(width: 980, height: 680))
            window.setFrameAutosaveName("AideWorkspace")
            window.isReleasedWhenClosed = false
            window.delegate = self
            self.window = window
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        if let conversationID { Task { await model.open(conversationID: conversationID) } }
    }

    public func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }
}
