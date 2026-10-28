import AideCore
import AppKit
import SwiftUI
import XCTest
@testable import AideShell

private struct EmptyWorkspace: WorkspaceProviding {
    func conversations(query: String) async throws -> [ConversationSummary] { [] }
    func conversation(id: Int) async throws -> ConversationDetail { throw CoreError.remote(code: "x", message: "") }
    func createConversation(agentID: String?, seed: [(role: ChatMessage.Role, content: String)]) async throws -> Int { 1 }
    func deleteConversation(id: Int) async throws {}
    func renameConversation(id: Int, title: String) async throws {}
    func agents() async throws -> [AgentInfo] { [] }
    func models() async throws -> ModelList { ModelList(models: [], current: "") }
    func selectModel(_ model: String) async throws {}
    func send(conversationID: Int, text: String) async throws -> RunHandle { throw CoreError.remote(code: "x", message: "") }
}

@MainActor
final class ComposerScrollerTests: XCTestCase {
    func testComposerEditorHasNoVisibleScroller() async throws {
        let model = WorkspaceModel(provider: EmptyWorkspace())
        let host = NSHostingView(rootView: Composer(model: model).frame(width: 600, height: 120))
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: 120), styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = host
        window.orderFront(nil)
        for _ in 0..<10 { try await Task.sleep(nanoseconds: 30_000_000); host.layoutSubtreeIfNeeded() }
        func scrollViews(_ view: NSView) -> [NSScrollView] {
            (view as? NSScrollView).map { [$0] } ?? view.subviews.flatMap(scrollViews)
        }
        let found = scrollViews(host)
        XCTAssertFalse(found.isEmpty, "composer should contain the editor's scroll view")
        XCTAssertTrue(found.allSatisfy { !$0.hasVerticalScroller })
        window.close()
    }
}
