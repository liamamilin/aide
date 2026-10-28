import SwiftUI

/// Reading layout for an answer: prose flows as plain text; only fenced code gets a card.
public struct MessageText: View {
    enum Block: Equatable {
        case prose(String)
        case code(language: String, body: String)
        case heading(level: Int, text: String)
        case list(ordered: Bool, items: [String])
        case quote(String)
        case table(header: [String], rows: [[String]])
    }

    let content: String
    public init(_ content: String) { self.content = content }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(Self.blocks(content).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let text): ProseView(text: text)
                case .code(let language, let body): CodeCard(language: language, code: body)
                case .heading(let level, let text): HeadingView(level: level, text: text)
                case .list(let ordered, let items): ListView(ordered: ordered, items: items)
                case .quote(let text): QuoteView(text: text)
                case .table(let header, let rows): TableView(header: header, rows: rows)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Splits on ``` fences, then reads headings, lists, quotes and tables out of the prose between them.
    static func blocks(_ text: String) -> [Block] {
        fenced(text).flatMap { block -> [Block] in
            if case .prose(let text) = block { return structure(text) }
            return [block]
        }
    }

    private static func cells(_ line: String) -> [String] {
        var trimmed = line.trimmingCharacters(in: .whitespaces)
        if trimmed.hasPrefix("|") { trimmed.removeFirst() }
        if trimmed.hasSuffix("|") { trimmed.removeLast() }
        return trimmed.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }

    private static func isTableSeparator(_ line: String) -> Bool {
        let parts = cells(line)
        return parts.count >= 1 && line.contains("-") && parts.allSatisfy { cell in
            !cell.isEmpty && cell.allSatisfy { "-: ".contains($0) } && cell.contains("-")
        }
    }

    private static func listMarker(_ line: String) -> (ordered: Bool, text: String)? {
        let t = line.trimmingCharacters(in: .whitespaces)
        for bullet in ["- ", "* ", "+ ", "• "] where t.hasPrefix(bullet) { return (false, String(t.dropFirst(bullet.count))) }
        if let dot = t.firstIndex(where: { $0 == "." || $0 == ")" }), dot != t.startIndex,
           t[t.startIndex..<dot].allSatisfy(\.isNumber), t[t.index(after: dot)...].hasPrefix(" ") {
            return (true, String(t[t.index(dot, offsetBy: 2)...]))
        }
        return nil
    }

    static func structure(_ text: String) -> [Block] {
        let lines = text.components(separatedBy: "\n")
        var result: [Block] = []
        var paragraph: [String] = []
        func flush() {
            let joined = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { result.append(.prose(joined)) }
            paragraph = []
        }
        var i = 0
        while i < lines.count {
            let line = lines[i]
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("#"), let space = t.firstIndex(of: " "), (1...6).contains(t.distance(from: t.startIndex, to: space)),
               t[t.startIndex..<space].allSatisfy({ $0 == "#" }) {
                flush()
                result.append(.heading(level: t.distance(from: t.startIndex, to: space), text: String(t[t.index(after: space)...])))
                i += 1
            } else if t.contains("|"), i + 1 < lines.count, isTableSeparator(lines[i + 1]) {
                flush()
                let header = cells(line)
                var rows: [[String]] = []
                i += 2
                while i < lines.count, lines[i].contains("|"), !lines[i].trimmingCharacters(in: .whitespaces).isEmpty {
                    rows.append(cells(lines[i])); i += 1
                }
                result.append(.table(header: header, rows: rows))
            } else if t.hasPrefix(">") {
                flush()
                var quoted: [String] = []
                while i < lines.count, lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                    quoted.append(String(lines[i].trimmingCharacters(in: .whitespaces).dropFirst()).trimmingCharacters(in: .whitespaces))
                    i += 1
                }
                result.append(.quote(quoted.joined(separator: "\n")))
            } else if let first = listMarker(line) {
                flush()
                var items = [first.text]
                while i + 1 < lines.count, let next = listMarker(lines[i + 1]), next.ordered == first.ordered {
                    items.append(next.text); i += 1
                }
                result.append(.list(ordered: first.ordered, items: items))
                i += 1
            } else {
                paragraph.append(line)
                i += 1
            }
        }
        flush()
        return result
    }

    static func fenced(_ text: String) -> [Block] {
        var result: [Block] = []
        var prose: [String] = []
        var code: [String] = []
        var language = ""
        var inCode = false
        func flushProse() {
            let joined = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { result.append(.prose(joined)) }
            prose = []
        }
        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    result.append(.code(language: language, body: code.joined(separator: "\n")))
                    code = []
                    inCode = false
                } else {
                    flushProse()
                    language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    inCode = true
                }
            } else if inCode {
                code.append(line)
            } else {
                prose.append(line)
            }
        }
        if inCode { result.append(.code(language: language, body: code.joined(separator: "\n"))) }
        flushProse()
        return result
    }
}

private struct ProseView: View {
    let text: String

    var body: some View {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        Text((try? AttributedString(markdown: text, options: options)) ?? AttributedString(text))
            .lineSpacing(4)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct CodeCard: View {
    let language: String
    let code: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(language.isEmpty ? "代码" : language).foregroundStyle(.secondary)
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                } label: { Image(systemName: "doc.on.doc") }
                    .buttonStyle(.plain)
                    .help("复制代码")
            }
            .font(.system(size: 11))
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            Divider().opacity(0.5)
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code).font(.system(size: 12.5, design: .monospaced)).textSelection(.enabled)
                    .padding(12)
            }
        }
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary))
    }
}


private func inline(_ text: String) -> AttributedString {
    let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
}

private struct HeadingView: View {
    let level: Int
    let text: String

    var body: some View {
        Text(inline(text))
            .font(.system(size: [22, 19, 16.5, 15, 14, 14][min(level, 6) - 1], weight: .semibold))
            .textSelection(.enabled)
            .padding(.top, level <= 2 ? 6 : 2)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct ListView: View {
    let ordered: Bool
    let items: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(ordered ? "\(index + 1)." : "•").foregroundStyle(.secondary).monospacedDigit()
                        .frame(minWidth: 16, alignment: .trailing)
                    Text(inline(item)).lineSpacing(3).textSelection(.enabled)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct QuoteView: View {
    let text: String

    var body: some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 1.5).fill(.quaternary).frame(width: 3)
            Text(inline(text)).lineSpacing(3).foregroundStyle(.secondary).textSelection(.enabled)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct TableView: View {
    let header: [String]
    let rows: [[String]]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow { ForEach(Array(header.enumerated()), id: \.offset) { _, cell in cellView(cell, bold: true) } }
                    .background(.quaternary.opacity(0.4))
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    Divider()
                    GridRow {
                        ForEach(0..<header.count, id: \.self) { column in cellView(column < row.count ? row[column] : "", bold: false) }
                    }
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.quaternary))
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    private func cellView(_ text: String, bold: Bool) -> some View {
        Text(inline(text)).font(.system(size: 13, weight: bold ? .semibold : .regular))
            .textSelection(.enabled)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .frame(minWidth: 60, alignment: .leading)
    }
}
