import AppKit
import ApplicationServices

/// Text selected inside Aide's own windows, read without a simulated ⌘C: a synthetic keystroke that nothing
/// can answer (no selection) makes the system play its alert sound. Asks Accessibility for our own focused
/// element first, then the responder chain's `copy:` (silent when unhandled), restoring the clipboard after.
@MainActor
enum InAppSelection {
    static func read() -> String? {
        if let text = accessibilitySelection(), !text.isEmpty { return text }
        return copiedSelection()
    }

    private static func accessibilitySelection() -> String? {
        guard AXIsProcessTrusted() else { return nil }
        let app = AXUIElementCreateApplication(getpid())
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let element = focused else { return nil }
        var value: CFTypeRef?
        // swiftlint:disable:next force_cast
        guard AXUIElementCopyAttributeValue(element as! AXUIElement, kAXSelectedTextAttribute as CFString, &value) == .success
        else { return nil }
        return value as? String
    }

    private static func copiedSelection() -> String? {
        let board = NSPasteboard.general
        let snapshot = (board.pasteboardItems ?? []).map { item in
            item.types.compactMap { type in item.data(forType: type).map { (type, $0) } }
        }
        let before = board.changeCount
        guard NSApp.sendAction(#selector(NSText.copy(_:)), to: nil, from: nil), board.changeCount != before else {
            return nil
        }
        let text = board.string(forType: .string)
        board.clearContents()
        let restored = snapshot.map { entries -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (type, data) in entries { item.setData(data, forType: type) }
            return item
        }
        if !restored.isEmpty { board.writeObjects(restored) }
        return text
    }
}
