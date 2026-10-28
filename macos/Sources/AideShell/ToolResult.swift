import AppKit
import Foundation
import SwiftUI

/// An app or file a lookup tool found. Straight from the tool's JSON, never from model wording.
public struct FoundItem: Equatable, Identifiable {
    public var id: String { path }
    public let kind: String      // app | file | folder | project | ...
    public let name: String
    public let path: String
    public let modified: String?
}

public enum ToolResult: Equatable {
    case apps([FoundItem])
    case files([FoundItem])
    case notes(total: Int, matching: Int, titles: [String])
    case reminders(open: Int, matching: Int, titles: [String])
}

public enum ToolResults {
    /// nil when the tool is not a lookup, the JSON is not what it should be, or nothing was found.
    public static func parse(tool: String, output: String) -> ToolResult? {
        guard !output.isEmpty, let data = output.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        switch tool {
        case "find_apps", "find_files":
            let items = (json["results"] as? [[String: Any]] ?? []).compactMap { row -> FoundItem? in
                guard let name = row["name"] as? String, let path = row["path"] as? String else { return nil }
                return FoundItem(kind: row["kind"] as? String ?? "file", name: name, path: path,
                                 modified: row["modified"] as? String)
            }
            if items.isEmpty { return nil }
            return tool == "find_apps" ? .apps(items) : .files(items)
        case "list_notes":
            let titles = (json["notes"] as? [[String: Any]] ?? []).compactMap { $0["title"] as? String }
            return .notes(total: json["total_notes"] as? Int ?? titles.count,
                          matching: json["matching"] as? Int ?? titles.count, titles: titles)
        case "list_reminders":
            let titles = (json["reminders"] as? [[String: Any]] ?? []).compactMap { $0["title"] as? String }
            return .reminders(open: json["total_open_reminders"] as? Int ?? titles.count,
                              matching: json["matching"] as? Int ?? titles.count, titles: titles)
        default:
            return nil
        }
    }
}

/// Clicks on a result open the real thing; `afterOpen` lets the Quick Panel step aside.
public struct ToolResultView: View {
    let result: ToolResult
    var afterOpen: () -> Void = {}
    private let limit = 5

    public init(result: ToolResult, afterOpen: @escaping () -> Void = {}) { self.result = result; self.afterOpen = afterOpen }

    public var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            switch result {
            case .apps(let items): list(items, revealable: false)
            case .files(let items): list(items, revealable: true)
            case .notes(let total, let matching, let titles):
                summary(titles, empty: "没有备忘录", total: total, matching: matching, unit: "条备忘录",
                        symbol: "note.text", bundle: "com.apple.Notes", app: "备忘录")
            case .reminders(let open, let matching, let titles):
                summary(titles, empty: "没有未完成的提醒", total: open, matching: matching, unit: "条未完成提醒",
                        symbol: "checklist", bundle: "com.apple.reminders", app: "提醒事项")
            }
        }
        .padding(6)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
    }

    private func list(_ items: [FoundItem], revealable: Bool) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(items.prefix(limit)) { item in
                HStack(spacing: 8) {
                    Image(nsImage: NSWorkspace.shared.icon(forFile: item.path)).resizable().frame(width: 22, height: 22)
                    VStack(alignment: .leading, spacing: 0) {
                        Text(item.name).font(.system(size: 13)).lineLimit(1)
                        Text(Self.detail(item)).font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                            .truncationMode(.middle)
                    }
                    Spacer(minLength: 6)
                    if revealable {
                        Button { Self.reveal(item.path); afterOpen() } label: { Image(systemName: "folder") }
                            .buttonStyle(.plain).foregroundStyle(.secondary).help("在 Finder 中显示")
                    }
                    Button("打开") { Self.open(item.path); afterOpen() }.controlSize(.small)
                }
                .padding(.vertical, 3).padding(.horizontal, 4)
            }
            if items.count > limit {
                Text("还有 \(items.count - limit) 个结果").font(.system(size: 11)).foregroundStyle(.secondary).padding(.horizontal, 4)
            }
        }
    }

    private func summary(_ titles: [String], empty: String, total: Int, matching: Int, unit: String,
                         symbol: String, bundle: String, app: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: symbol).foregroundStyle(.secondary)
                Text(total == 0 ? empty : "共 \(total) \(unit)").font(.system(size: 13))
                Spacer()
                Button("打开\(app)") { Self.openApp(bundle); afterOpen() }.controlSize(.small)
            }
            ForEach(Array(titles.prefix(limit).enumerated()), id: \.offset) { _, title in
                Text("· \(title)").font(.system(size: 12)).foregroundStyle(.secondary).lineLimit(1)
            }
            if matching > limit { Text("还有 \(matching - limit) 条").font(.system(size: 11)).foregroundStyle(.secondary) }
        }
        .padding(.vertical, 3).padding(.horizontal, 4)
    }

    static func detail(_ item: FoundItem) -> String {
        let home = NSHomeDirectory()
        let parent = (item.path as NSString).deletingLastPathComponent
        let shown = parent.hasPrefix(home) ? "~" + parent.dropFirst(home.count) : parent
        return [item.modified, shown].compactMap { $0 }.joined(separator: " · ")
    }

    static func open(_ path: String) { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }

    static func reveal(_ path: String) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }

    static func openApp(_ bundleID: String) {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) else { return }
        NSWorkspace.shared.openApplication(at: url, configuration: NSWorkspace.OpenConfiguration())
    }
}
