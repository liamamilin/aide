import AppKit
import Observation

/// The Quick Panel shortcut: remembered in UserDefaults and re-registered when changed. A combination
/// another app already owns is refused and the previous one stays active.
@MainActor @Observable
public final class HotkeySettings {
    public private(set) var combo: Hotkey.Combo
    public private(set) var error: String?
    public private(set) var isRecording = false

    private let defaults: UserDefaults
    /// Registers the shortcut; false when the system or another app refuses it.
    private let register: (Hotkey.Combo) -> Bool
    private var monitor: Any?

    private static let key = "quickPanelHotkey"

    public init(defaults: UserDefaults = .standard, register: @escaping (Hotkey.Combo) -> Bool) {
        self.defaults = defaults
        self.register = register
        combo = Self.load(defaults) ?? .quickPanel
    }

    /// Registers the saved combination at launch; falls back to the default when it can no longer be used.
    public func activate() {
        if register(combo) { return }
        error = "\(combo.display) 已被其他应用占用"
        if combo != .quickPanel, register(.quickPanel) { combo = .quickPanel; save() }
    }

    @discardableResult
    public func apply(_ new: Hotkey.Combo) -> Bool {
        guard new.keyCode != combo.keyCode || new.modifiers != combo.modifiers else { error = nil; return true }
        guard register(new) else {
            error = "\(new.display) 已被其他应用占用，请换一个"
            return false
        }
        combo = new
        error = nil
        save()
        return true
    }

    public func reset() { apply(.quickPanel) }

    /// Next key press with a modifier becomes the shortcut; Esc cancels.
    public func startRecording() {
        guard monitor == nil else { return }
        isRecording = true
        error = nil
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            if event.keyCode == 53 { self.stopRecording(); return nil }
            if let new = Hotkey.Combo.from(keyCode: event.keyCode, flags: event.modifierFlags,
                                           characters: event.charactersIgnoringModifiers) {
                self.stopRecording()
                self.apply(new)
            } else {
                self.error = "快捷键需要包含 ⌘、⌃ 或 ⌥"
            }
            return nil
        }
    }

    public func stopRecording() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        isRecording = false
    }

    private func save() {
        defaults.set(["keyCode": Int(combo.keyCode), "modifiers": Int(combo.modifiers), "label": combo.label],
                     forKey: Self.key)
    }

    private static func load(_ defaults: UserDefaults) -> Hotkey.Combo? {
        guard let raw = defaults.dictionary(forKey: key), let code = raw["keyCode"] as? Int,
              let modifiers = raw["modifiers"] as? Int else { return nil }
        return Hotkey.Combo(keyCode: UInt32(code), modifiers: UInt32(modifiers), label: raw["label"] as? String ?? "")
    }
}
