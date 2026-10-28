import AppKit
import SwiftUI

private final class KeyPanel: NSPanel {
    override var canBecomeKey: Bool { true }
}

/// Shows the Quick Panel without activating Aide, so the app you came from keeps its selection and
/// is brought straight back when the panel closes.
@MainActor
public final class QuickPanelController {
    private let model: QuickPanelModel
    private let reader: SelectionReading
    private let panel: KeyPanel
    private var previousApp: NSRunningApplication?

    public init(model: QuickPanelModel, reader: SelectionReading) {
        self.model = model
        self.reader = reader
        panel = KeyPanel(contentRect: NSRect(x: 0, y: 0, width: 560, height: 200),
                         styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                         backing: .buffered, defer: true)
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.isMovableByWindowBackground = true
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.hidesOnDeactivate = false
        let host = NSHostingController(rootView: QuickPanelView(model: model))
        host.sizingOptions = [.preferredContentSize]
        host.view.wantsLayer = true
        host.view.layer?.cornerRadius = 14
        host.view.layer?.masksToBounds = true
        panel.contentViewController = host
        model.onClose = { [weak self] copied in self?.close(copying: copied) }
    }

    public var isVisible: Bool { panel.isVisible }

    /// ⌘⌃L with nothing selected has nothing to act on, so it opens the Workspace instead of an empty panel.
    /// A missing Accessibility permission is not "nothing selected": that still shows the permission hint.
    public var onNoSelection: () -> Void = {}

    /// Hotkey entry point: capture first (the source app is still frontmost), then show.
    public func toggle() {
        if panel.isVisible { model.escape(); if panel.isVisible { close(copying: nil) }; return }
        previousApp = NSWorkspace.shared.frontmostApplication
        NSLog("Aide hotkey: panel requested, front=%@", previousApp?.localizedName ?? "?")
        Task {
            let selection = await reader.read()
            if selection == .empty {
                previousApp = nil
                onNoSelection()
                return
            }
            if selection == .needsAccessibility { SelectionReader.requestAccessibility() }
            show()
            await model.present(selection)
        }
    }

    private func show() {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { $0.frame.contains(mouse) } ?? NSScreen.main
        if let frame = screen?.visibleFrame {
            panel.layoutIfNeeded()
            let size = panel.frame.size
            panel.setFrameOrigin(NSPoint(x: frame.midX - size.width / 2, y: frame.maxY - frame.height * 0.28 - size.height))
        }
        panel.makeKeyAndOrderFront(nil)
    }

    private func close(copying text: String?) {
        if let text {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(text, forType: .string)
        }
        panel.orderOut(nil)
        previousApp?.activate()
        previousApp = nil
    }
}
