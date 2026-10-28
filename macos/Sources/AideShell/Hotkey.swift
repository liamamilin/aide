import AppKit
import Carbon.HIToolbox
import Foundation

/// Global hotkey through Carbon `RegisterEventHotKey`: needs no Accessibility permission and,
/// unlike an event tap, is not delivered for key repeats.
public final class Hotkey {
    public struct Combo: Equatable, Sendable {
        public var keyCode: UInt32
        public var modifiers: UInt32
        /// The key as the person sees it ("L"); only used for display.
        public var label: String
        public init(keyCode: UInt32, modifiers: UInt32, label: String = "") {
            self.keyCode = keyCode; self.modifiers = modifiers; self.label = label
        }
        /// ⌘⌃L, the same default as the Qt app.
        public static let quickPanel = Combo(keyCode: UInt32(kVK_ANSI_L), modifiers: UInt32(cmdKey | controlKey), label: "L")

        /// "⌃⌥⌘L" in the order macOS menus use.
        public var display: String {
            var text = ""
            if modifiers & UInt32(controlKey) != 0 { text += "⌃" }
            if modifiers & UInt32(optionKey) != 0 { text += "⌥" }
            if modifiers & UInt32(shiftKey) != 0 { text += "⇧" }
            if modifiers & UInt32(cmdKey) != 0 { text += "⌘" }
            return text + label
        }

        /// A global shortcut needs ⌘, ⌃ or ⌥ (Shift alone would swallow ordinary typing).
        public static func from(keyCode: UInt16, flags: NSEvent.ModifierFlags, characters: String?) -> Combo? {
            var modifiers: UInt32 = 0
            if flags.contains(.command) { modifiers |= UInt32(cmdKey) }
            if flags.contains(.control) { modifiers |= UInt32(controlKey) }
            if flags.contains(.option) { modifiers |= UInt32(optionKey) }
            guard modifiers != 0 else { return nil }
            if flags.contains(.shift) { modifiers |= UInt32(shiftKey) }
            let label = (characters ?? "").trimmingCharacters(in: .whitespaces).uppercased()
            return Combo(keyCode: UInt32(keyCode), modifiers: modifiers, label: label.isEmpty ? "key\(keyCode)" : label)
        }
    }

    private var ref: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    private let action: () -> Void

    public init?(_ combo: Combo, action: @escaping () -> Void) {
        self.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData else { return noErr }
            Unmanaged<Hotkey>.fromOpaque(userData).takeUnretainedValue().action()
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &handlerRef)
        guard status == noErr else { return nil }
        let id = EventHotKeyID(signature: OSType(0x41494445), id: 1)  // 'AIDE'
        guard RegisterEventHotKey(combo.keyCode, combo.modifiers, id, GetApplicationEventTarget(), 0, &ref) == noErr
        else { return nil }
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handlerRef { RemoveEventHandler(handlerRef) }
    }
}
