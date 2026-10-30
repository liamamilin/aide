import AideCore
import XCTest
@testable import AideShell

private final class StubSystem: SystemProviding, @unchecked Sendable {
    var catalog = ModelCatalog(reachable: true, models: [])
    var exported: (URL, ExportFormat)?
    var failPull = false

    func modelCatalog(baseURL: String?) async throws -> ModelCatalog { catalog }

    func pullModel(_ name: String, baseURL: String?, progress: @escaping @Sendable (PullProgress) -> Void) async throws {
        if failPull { throw CoreError.remote(code: "invalid", message: "拉取失败") }
        progress(PullProgress(model: name, status: "pulling", completed: 5, total: 10))
        catalog = ModelCatalog(reachable: true, models: [ModelInfo(name: name, size: 1, vision: false)])
    }

    func exportConversations(to url: URL, format: ExportFormat) async throws -> (conversations: Int, messages: Int) {
        exported = (url, format)
        return (2, 6)
    }

    func diagnostics(baseURL: String?) async throws -> String { "Aide test" }

    func checkForUpdate() async throws -> UpdateInfo {
        UpdateInfo(current: "1.5.0", latest: "1.6.0", available: true)
    }
}

@MainActor
final class SystemModelTests: XCTestCase {
    func testNeedsSetupUntilReachableWithAModel() async {
        let stub = StubSystem()
        let model = SystemModel(provider: stub)
        XCTAssertFalse(model.needsSetup)             // nothing known yet: don't flash the first-run screen
        await model.refresh()
        XCTAssertTrue(model.needsSetup)              // reachable but empty
        stub.catalog = ModelCatalog(reachable: false, models: [])
        await model.refresh()
        XCTAssertTrue(model.needsSetup)
        stub.catalog = ModelCatalog(reachable: true, models: [ModelInfo(name: "m", size: 1)])
        await model.refresh()
        XCTAssertFalse(model.needsSetup)
    }

    func testPullRefreshesCatalogAndClearsProgress() async {
        let stub = StubSystem()
        let model = SystemModel(provider: stub)
        await model.pull("qwen3:4b")
        XCTAssertNil(model.pulling)
        XCTAssertEqual(model.catalog?.models.map(\.name), ["qwen3:4b"])
        XCTAssertEqual(model.notice, "已下载 qwen3:4b")
    }

    func testFailedPullShowsTheCoreMessage() async {
        let stub = StubSystem()
        stub.failPull = true
        let model = SystemModel(provider: stub)
        await model.pull("nope")
        XCTAssertEqual(model.errorMessage, "拉取失败")
        XCTAssertNil(model.pulling)
    }

    func testExportAndUpdate() async {
        let stub = StubSystem()
        let model = SystemModel(provider: stub)
        let url = URL(fileURLWithPath: "/tmp/x.md")
        await model.export(to: url, format: .markdown)
        XCTAssertEqual(stub.exported?.0, url)
        XCTAssertEqual(model.notice, "已导出 2 个对话、6 条消息")
        await model.checkForUpdate()
        XCTAssertEqual(model.update?.latest, "1.6.0")
        XCTAssertEqual(PullProgress(model: "m", status: "", completed: 5, total: 10).fraction, 0.5)
    }
}
