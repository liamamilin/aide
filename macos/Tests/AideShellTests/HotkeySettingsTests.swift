import AppKit
import Carbon.HIToolbox
import XCTest
@testable import AideShell

@MainActor
final class HotkeySettingsTests: XCTestCase {
    private func defaults() -> UserDefaults {
        let suite = "aide.test.\(UUID().uuidString)"
        let d = UserDefaults(suiteName: suite)!
        addTeardownBlock { d.removePersistentDomain(forName: suite) }
        return d
    }

    func testComboNeedsAModifierAndShowsInMenuOrder() {
        XCTAssertNil(Hotkey.Combo.from(keyCode: 37, flags: [.shift], characters: "l"))
        XCTAssertEqual(Hotkey.Combo.from(keyCode: 37, flags: [.command, .control], characters: "l")?.display, "⌃⌘L")
        XCTAssertEqual(Hotkey.Combo.from(keyCode: 1, flags: [.option, .shift, .command], characters: "s")?.display, "⌥⇧⌘S")
    }

    func testChangeIsRememberedAndTakenComboKeepsTheOldOne() {
        let store = defaults()
        var taken: Set<UInt32> = []
        let settings = HotkeySettings(defaults: store) { !taken.contains($0.keyCode) }
        XCTAssertEqual(settings.combo, .quickPanel)

        let space = Hotkey.Combo(keyCode: 49, modifiers: UInt32(optionKey), label: "Space")
        XCTAssertTrue(settings.apply(space))
        XCTAssertEqual(HotkeySettings(defaults: store) { _ in true }.combo, space)

        taken = [40]
        let clash = Hotkey.Combo(keyCode: 40, modifiers: UInt32(cmdKey), label: "K")
        XCTAssertFalse(settings.apply(clash))
        XCTAssertEqual(settings.combo, space)
        XCTAssertNotNil(settings.error)
        settings.reset()
        XCTAssertEqual(settings.combo, .quickPanel)
    }

    func testActivateFallsBackToDefaultWhenSavedComboIsNoLongerFree() {
        let store = defaults()
        _ = HotkeySettings(defaults: store) { _ in true }.apply(Hotkey.Combo(keyCode: 40, modifiers: UInt32(cmdKey), label: "K"))
        let blocked = HotkeySettings(defaults: store) { $0.keyCode != 40 }
        blocked.activate()
        XCTAssertEqual(blocked.combo, .quickPanel)
        XCTAssertNotNil(blocked.error)
    }
}
