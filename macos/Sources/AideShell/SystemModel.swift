import AideCore
import Foundation
import Observation

/// Models, first-run state, export, diagnostics and updates: the parts of Settings that are about the
/// installation rather than preferences.
@MainActor @Observable
public final class SystemModel {
    public static let suggested: [(name: String, note: String)] = [
        ("qwen3:4b", "轻量，约 2.5 GB，适合内存较小的 Mac"),
        ("qwen3:14b", "推荐，约 9 GB，回答更稳"),
    ]

    public private(set) var catalog: ModelCatalog?
    public private(set) var isLoading = false
    public private(set) var pulling: String?
    public private(set) var progress: PullProgress?
    public private(set) var update: UpdateInfo?
    public private(set) var isChecking = false
    public private(set) var notice: String?
    public private(set) var errorMessage: String?
    public var baseURL: String?

    private let provider: SystemProviding

    public init(provider: SystemProviding) { self.provider = provider }

    /// Ollama not reachable, or reachable with nothing installed: the first-run screen has work to do.
    public var needsSetup: Bool {
        guard let catalog else { return false }
        return !catalog.reachable || catalog.models.isEmpty
    }

    public func refresh() async {
        isLoading = true
        defer { isLoading = false }
        do { catalog = try await provider.modelCatalog(baseURL: baseURL); errorMessage = nil }
        catch { errorMessage = Self.describe(error) }
    }

    public func pull(_ name: String) async {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard pulling == nil, !name.isEmpty else { return }
        pulling = name
        progress = nil
        defer { pulling = nil; progress = nil }
        do {
            try await provider.pullModel(name, baseURL: baseURL) { [weak self] value in
                Task { @MainActor in self?.progress = value }
            }
            errorMessage = nil
            notice = "已下载 \(name)"
            await refresh()
        } catch { errorMessage = Self.describe(error) }
    }

    public func export(to url: URL, format: ExportFormat) async {
        do {
            let done = try await provider.exportConversations(to: url, format: format)
            notice = "已导出 \(done.conversations) 个对话、\(done.messages) 条消息"
            errorMessage = nil
        } catch { errorMessage = Self.describe(error) }
    }

    public func copyDiagnostics() async -> String? {
        do { errorMessage = nil; return try await provider.diagnostics(baseURL: baseURL) }
        catch { errorMessage = Self.describe(error); return nil }
    }

    public func checkForUpdate() async {
        isChecking = true
        defer { isChecking = false }
        do { update = try await provider.checkForUpdate(); errorMessage = nil }
        catch { errorMessage = Self.describe(error) }
    }

    public func clearNotice() { notice = nil }

    static func describe(_ error: Error) -> String {
        if case CoreError.remote(_, let message) = error { return message }
        return "无法连接到 Aide 核心，请重启 Aide。"
    }
}
