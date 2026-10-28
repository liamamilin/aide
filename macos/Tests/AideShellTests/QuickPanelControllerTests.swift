import AideCore
import XCTest
@testable import AideShell

private struct FixedReader: SelectionReading {
    let result: SelectionResult
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
        var opened = false
        controller.onNoSelection = { opened = true }
        controller.toggle()
        for _ in 0..<20 { try? await Task.sleep(nanoseconds: 10_000_000) }
        let visible = controller.isVisible
        if visible { controller.toggle() }
        return (opened, visible)
    }

    func testNothingSelectedOpensTheWorkspaceInsteadOfAnEmptyPanel() async {
        let outcome = await run(.empty)
        XCTAssertTrue(outcome.opened)
        XCTAssertFalse(outcome.visible)
    }

    func testSelectedTextShowsThePanelNotTheWorkspace() async {
        let outcome = await run(.text("hello"))
        XCTAssertFalse(outcome.opened)
        XCTAssertTrue(outcome.visible)
    }
}
