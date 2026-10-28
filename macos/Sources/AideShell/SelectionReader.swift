import AppKit
import ApplicationServices

public enum SelectionResult: Equatable, Sendable {
    case text(String)
    case empty
    case needsAccessibility
}

public protocol SelectionReading { func read() async -> SelectionResult }

/// Reads the frontmost app's selection. Accessibility first (no clipboard side effects); if the
/// app does not expose it, falls back to a simulated copy that waits for the hotkey modifiers to
/// be released (a held Control turns ⌘C into something else) and restores the clipboard.
public struct SelectionReader: SelectionReading {
    public init() {}

    public func read() async -> SelectionResult {
        let app = NSWorkspace.shared.frontmostApplication?.localizedName ?? "?"
        guard AXIsProcessTrusted() else {
            NSLog("Aide selection: front=%@ accessibility NOT trusted", app)
            return .needsAccessibility
        }
        if let text = accessibilitySelection(), !text.isEmpty {
            NSLog("Aide selection: front=%@ via accessibility (%d chars)", app, text.count)
            return .text(text)
        }
        if let text = await copiedSelection(), !text.isEmpty {
            NSLog("Aide selection: front=%@ via simulated copy (%d chars)", app, text.count)
            return .text(text)
        }
        NSLog("Aide selection: front=%@ nothing found (AX empty, copy gave no change)", app)
        return .empty
    }

    public static func requestAccessibility() {
        // Literal key: the C constant is a mutable global that Swift 6 flags.
        _ = AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary)
    }

    private func accessibilitySelection() -> String? {
        let system = AXUIElementCreateSystemWide()
        var focused: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
              let element = focused else { return nil }
        var value: CFTypeRef?
        // swiftlint:disable:next force_cast
        guard AXUIElementCopyAttributeValue(element as! AXUIElement, kAXSelectedTextAttribute as CFString, &value) == .success
        else { return nil }
        return value as? String
    }

    private func copiedSelection() async -> String? {
        let modifiers: CGEventFlags = [.maskCommand, .maskControl, .maskAlternate, .maskShift]
        let deadline = Date().addingTimeInterval(1.5)
        while CGEventSource.flagsState(.combinedSessionState).intersection(modifiers) != [] {
            if Date() > deadline { return nil }
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        let board = NSPasteboard.general
        let snapshot = Self.snapshot(board)
        let before = board.changeCount
        Self.postCopy()
        var after = before
        for _ in 0..<20 {   // up to ~400 ms for the app to answer
            try? await Task.sleep(nanoseconds: 20_000_000)
            after = board.changeCount
            if after != before { break }
        }
        guard after != before else { return nil }
        let text = board.string(forType: .string)
        // Only restore if nobody copied something newer in the meantime.
        if board.changeCount == after { Self.restore(snapshot, to: board) }
        return text
    }

    private static func postCopy() {
        let source = CGEventSource(stateID: .combinedSessionState)
        for down in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: 8, keyDown: down)  // 8 = C
            event?.flags = .maskCommand
            event?.post(tap: .cghidEventTap)
        }
    }

    private static func snapshot(_ board: NSPasteboard) -> [[(NSPasteboard.PasteboardType, Data)]] {
        (board.pasteboardItems ?? []).map { item in
            item.types.compactMap { type in item.data(forType: type).map { (type, $0) } }
        }
    }

    private static func restore(_ items: [[(NSPasteboard.PasteboardType, Data)]], to board: NSPasteboard) {
        board.clearContents()
        let restored = items.map { entries -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (type, data) in entries { item.setData(data, forType: type) }
            return item
        }
        if !restored.isEmpty { board.writeObjects(restored) }
    }
}
