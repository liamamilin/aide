import Carbon.HIToolbox
import Foundation

/// Global hotkey through Carbon `RegisterEventHotKey`: needs no Accessibility permission and,
/// unlike an event tap, is not delivered for key repeats.
public final class Hotkey {
    public struct Combo: Equatable, Sendable {
        public var keyCode: UInt32
        public var modifiers: UInt32
        public init(keyCode: UInt32, modifiers: UInt32) { self.keyCode = keyCode; self.modifiers = modifiers }
        /// ⌘⌃L, the same default as the Qt app.
        public static let quickPanel = Combo(keyCode: UInt32(kVK_ANSI_L), modifiers: UInt32(cmdKey | controlKey))
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
