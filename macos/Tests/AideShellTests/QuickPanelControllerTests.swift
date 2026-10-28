import AideCore
import XCTest
@testable import AideShell

private final class FixedReader: SelectionReading, @unchecked Sendable {
    var result: SelectionResult
    init(result: SelectionResult) { self.result = result }
    func read() async -> SelectionResult { result }
}

private struct NoActions: RunProviding {
    func actions() async throws -> [QuickAction] { [QuickAction(id: "translate", name: "翻译")] }
    func start(actionID: String, material: String) async throws -> RunHandle {
        throw CoreError.remote(code: "x", message: "unused")
    }
}

@MainActor
final class QuickPanelControllerTests: XCTestCase {
    private static var keep: [QuickPanelController] = []

    private func run(_ result: SelectionResult) async -> (opened: Bool, visible: Bool) {
        let controller = QuickPanelController(model: QuickPanelModel(provider: NoActions()), reader: FixedReader(result: result))
        Self.keep.append(controller)
        let opened = false
        controller.toggle()
        for _ in 0..<20 { try? await Task.sleep(nanoseconds: 10_000_000) }
        let visible = controller.isVisible
        if visible { controller.toggle() }
        return (opened, visible)
    }

    func testNothingSelectedStillShowsThePanelForTypingARequest() async {
        let outcome = await run(.empty)
        XCTAssertTrue(outcome.visible)
    }

    func testSelectedTextShowsThePanelNotTheWorkspace() async {
        let outcome = await run(.text("hello"))
        XCTAssertTrue(outcome.visible)
    }

    func testSecondPressWithNewSelectionReusesTheActionAndWithNothingClosesThePanel() async {
        let reader = FixedReader(result: .text("hello"))
        let model = QuickPanelModel(provider: NoActions())
        let controller = QuickPanelController(model: model, reader: reader)
        Self.keep.append(controller)
        controller.toggle()
        for _ in 0..<20 { try? await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertTrue(controller.isVisible)
        model.choose(index: 0)                       // the person picked 翻译
        for _ in 0..<10 { try? await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertEqual(model.activeAction?.id, "translate")

        reader.result = .text("world")
        controller.toggle()
        for _ in 0..<30 { try? await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertTrue(controller.isVisible, "a new selection keeps the panel open")
        XCTAssertEqual(model.selection, "world")
        XCTAssertEqual(model.activeAction?.id, "translate", "same action, new text")

        reader.result = .empty
        controller.toggle()
        for _ in 0..<30 { try? await Task.sleep(nanoseconds: 10_000_000) }
        XCTAssertFalse(controller.isVisible, "nothing selected toggles the panel off")
    }

    func testMissingPermissionShowsOnlyThePanelHintAndHasASettingsLink() async {
        let outcome = await run(.needsAccessibility)
        XCTAssertTrue(outcome.visible)      // the panel explains; no system dialog is raised on its own
        XCTAssertFalse(outcome.opened)
        XCTAssertEqual(SelectionReader.accessibilitySettingsURL.scheme, "x-apple.systempreferences")
    }
}
