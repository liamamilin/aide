import SwiftUI

/// Reading layout for an answer: prose flows as plain text; only fenced code gets a card.
public struct MessageText: View {
    enum Block: Equatable { case prose(String), code(language: String, body: String) }

    let content: String
    public init(_ content: String) { self.content = content }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(Self.blocks(content).enumerated()), id: \.offset) { _, block in
                switch block {
                case .prose(let text): ProseView(text: text)
                case .code(let language, let body): CodeCard(language: language, code: body)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Splits on ``` fences. An unterminated fence (still streaming) renders as code so it doesn't flicker.
    static func blocks(_ text: String) -> [Block] {
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
