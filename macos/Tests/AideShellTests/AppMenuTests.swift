import AppKit
import XCTest
@testable import AideShell

@MainActor
final class AppMenuTests: XCTestCase {
    private func key(_ chars: String, _ flags: NSEvent.ModifierFlags) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: flags, timestamp: 0, windowNumber: 0,
                         context: nil, characters: chars, charactersIgnoringModifiers: chars, isARepeat: false, keyCode: 0)!
    }

    func testEditShortcutsMapToResponderActions() {
        XCTAssertEqual(AppMenu.editAction(for: key("c", .command)), #selector(NSText.copy(_:)))
        XCTAssertEqual(AppMenu.editAction(for: key("v", .command)), #selector(NSText.paste(_:)))
        XCTAssertEqual(AppMenu.editAction(for: key("Z", [.command, .shift])), Selector(("redo:")))
        XCTAssertNil(AppMenu.editAction(for: key("c", [.command, .control])))   // ⌘⌃C is not copy
        XCTAssertNil(AppMenu.editAction(for: key("v", [])))
    }

    func testEditMenuHasStandardKeyEquivalents() {
        let items = AppMenu.editMenu().items.filter { !$0.isSeparatorItem }
        XCTAssertEqual(items.map(\.keyEquivalent), ["z", "Z", "x", "c", "v", "a"])
        XCTAssertTrue(items.contains { $0.action == #selector(NSText.paste(_:)) })
    }
}
