import AVFoundation
import NaturalLanguage
import Observation

/// What the reader needs from a voice; lets voice choice be tested without the system's installed voices.
public struct SpeechVoiceInfo: Equatable, Sendable {
    public enum Quality: Int, Comparable, Sendable {
        case standard = 1, enhanced = 2, premium = 3
        public static func < (a: Quality, b: Quality) -> Bool { a.rawValue < b.rawValue }
    }
    public var identifier: String
    public var language: String   // BCP-47, e.g. "en-US"
    public var quality: Quality
    public init(identifier: String, language: String, quality: Quality) {
        self.identifier = identifier; self.language = language; self.quality = quality
    }
}

/// One stretch of text and the voice to read it with (nil: the system default voice).
public struct SpeechPart: Equatable, Sendable {
    public var text: String
    public var voiceIdentifier: String?
    public init(text: String, voiceIdentifier: String?) { self.text = text; self.voiceIdentifier = voiceIdentifier }
}

public protocol SpeechSynthesizing: AnyObject {
    var voices: [SpeechVoiceInfo] { get }
    /// Reads the parts back to back; `finished` is called once, on the main actor, when the last one ends or
    /// the reading is stopped.
    func speak(_ parts: [SpeechPart], finished: @escaping @MainActor () -> Void)
    func stop()
}

/// Reads text aloud with the system's own voices (AVSpeechSynthesizer): entirely on this Mac, nothing
/// downloaded or sent anywhere. Mixed Chinese/English text is split into runs so each is read by a voice of its
/// own language (an English example inside a Chinese explanation gets an English voice), and the best installed
/// voice per language is used, so a Premium/Enhanced voice downloaded in System Settings is picked up automatically.
@MainActor @Observable
public final class SpeechReader {
    public private(set) var isSpeaking = false
    /// Who asked for the current reading (e.g. a message id), so only that button shows "stop".
    public private(set) var source: String?

    private let synthesizer: SpeechSynthesizing
    private var utterance = 0

    public init(synthesizer: SpeechSynthesizing = SystemSpeechSynthesizer()) { self.synthesizer = synthesizer }

    /// Speaks `text`, replacing anything already being read. Blank text does nothing.
    public func speak(_ text: String, source: String? = nil) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        stop()
        utterance += 1
        let mine = utterance
        let voices = synthesizer.voices
        let region = Locale.current.region?.identifier
        let parts = Self.segments(of: trimmed).map { run in
            SpeechPart(text: run.text,
                       voiceIdentifier: Self.pickVoice(for: run.language, among: voices, preferredRegion: region)?.identifier)
        }
        isSpeaking = true
        self.source = source
        synthesizer.speak(parts) { [weak self] in
            guard let self, self.utterance == mine else { return }   // a newer request owns isSpeaking now
            self.isSpeaking = false
            self.source = nil
        }
    }

    public func stop() {
        guard isSpeaking else { return }
        utterance += 1
        isSpeaking = false
        source = nil
        synthesizer.stop()
    }

    /// One entry point for menu, hotkey and button: stop if reading, else read `text`.
    public func toggle(_ text: String) {
        if isSpeaking { stop() } else { speak(text) }
    }

    public func isReading(_ source: String) -> Bool { isSpeaking && self.source == source }

    /// A message's button: stops if that message is being read, otherwise reads it (replacing any other reading).
    /// Markdown is spoken as plain text, so "**doubt**" is not read with its asterisks.
    public func toggle(markdown: String, source: String) {
        if isReading(source) { stop() } else { speak(Self.plainText(fromMarkdown: markdown), source: source) }
    }

    /// Drops Markdown markup line by line (headings, quotes, bullets, emphasis, code ticks, table pipes, fences)
    /// and keeps line breaks so the voice still pauses between paragraphs and list items.
    nonisolated static func plainText(fromMarkdown markdown: String) -> String {
        markdown.components(separatedBy: .newlines).compactMap { raw -> String? in
            var line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("```") { return nil }
            if line.hasPrefix("|") {
                let cells = line.split(separator: "|").map { $0.trimmingCharacters(in: .whitespaces) }
                if cells.allSatisfy({ $0.allSatisfy { "-: ".contains($0) } }) { return nil }   // |---|---|
                line = cells.filter { !$0.isEmpty }.joined(separator: ", ")
            }
            for prefix in ["###### ", "##### ", "#### ", "### ", "## ", "# ", "> ", "- ", "* ", "+ "]
            where line.hasPrefix(prefix) {
                line.removeFirst(prefix.count)
                break
            }
            if let inline = try? AttributedString(
                markdown: line, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                line = String(inline.characters)
            }
            return line
        }
        .joined(separator: "\n")
    }

    /// Reads `selection` if it is a piece of this message (the person selected part of it), else the whole message.
    /// A selection from somewhere else (the composer, another message) does not count.
    public func toggle(markdown: String, selection: String?, source: String) {
        if isReading(source) { stop(); return }
        if let selection, Self.selection(selection, isPartOf: markdown) { speak(selection, source: source) } else {
            speak(Self.plainText(fromMarkdown: markdown), source: source)
        }
    }

    /// Compares letters only, so list numbers, Markdown markup and line wrapping in the rendered text don't matter.
    nonisolated static func selection(_ selection: String, isPartOf markdown: String) -> Bool {
        let picked = String(selection.unicodeScalars.filter(CharacterSet.letters.contains).map(Character.init))
        guard !picked.isEmpty else { return false }
        let whole = String(plainText(fromMarkdown: markdown).unicodeScalars.filter(CharacterSet.letters.contains)
            .map(Character.init))
        return whole.contains(picked)
    }

    /// Splits text into runs of CJK and Latin script, each with the language to read it in. Digits, spaces and
    /// punctuation stay with the run they are in. Text with no CJK at all is one run in its detected language
    /// (so French stays French); in mixed text Latin runs are read as English, CJK runs in the CJK language detected.
    nonisolated static func segments(of text: String) -> [(text: String, language: String?)] {
        guard text.unicodeScalars.contains(where: isCJK) else { return [(text, language(of: text))] }
        let cjkLanguage: String = {
            let detected = language(of: String(text.unicodeScalars.filter(isCJK).map(Character.init)))
            return ["zh", "ja", "ko"].contains(detected ?? "") ? detected! : "zh"
        }()
        var runs: [(text: String, language: String?)] = []
        var current = ""
        var currentLanguage: String?
        for char in text {
            let scalar = char.unicodeScalars.first!
            let language: String? = isCJK(scalar) ? cjkLanguage : CharacterSet.letters.contains(scalar) ? "en" : nil
            if let language, let open = currentLanguage, language != open {
                runs.append((current, open))
                current = ""
            }
            if let language { currentLanguage = language }
            current.append(char)
        }
        if !current.isEmpty { runs.append((current, currentLanguage)) }
        return runs.filter { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// Han, kana, hangul and the CJK/full-width punctuation that goes with them.
    nonisolated static func isCJK(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 0x3000...0x303F, 0x3040...0x30FF, 0x3400...0x4DBF, 0x4E00...0x9FFF, 0xAC00...0xD7AF,
             0xF900...0xFAFF, 0xFF00...0xFFEF: return true
        default: return false
        }
    }

    /// Dominant language of the text as a bare code ("en", "zh"); nil when it cannot tell.
    nonisolated static func language(of text: String) -> String? {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        guard let raw = recognizer.dominantLanguage?.rawValue else { return nil }
        return String(raw.split(separator: "-").first ?? Substring(raw))
    }

    /// Best voice for `language`: highest quality first, then the person's region (en-US over en-GB for a US Mac).
    /// Nil (system default voice) when no installed voice matches.
    nonisolated static func pickVoice(for language: String?, among voices: [SpeechVoiceInfo],
                                      preferredRegion: String?) -> SpeechVoiceInfo? {
        guard let language else { return nil }
        let matching = voices.filter { $0.language.split(separator: "-").first.map(String.init) == language }
        func inRegion(_ v: SpeechVoiceInfo) -> Bool {
            guard let preferredRegion else { return false }
            return v.language.hasSuffix("-\(preferredRegion)")
        }
        return matching.max { a, b in
            if a.quality != b.quality { return a.quality < b.quality }
            return !inRegion(a) && inRegion(b)
        }
    }
}

/// AVSpeechSynthesizer behind `SpeechSynthesizing`.
/// Driven from the main thread only (delegate callbacks hop to main before touching state).
public final class SystemSpeechSynthesizer: NSObject, SpeechSynthesizing, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let synthesizer = AVSpeechSynthesizer()
    /// One reading = several queued utterances. Keyed per utterance: a stopped reading reports didCancel after the
    /// next one has started, and must not end it. The reading ends when its last utterance finishes or any is cancelled.
    private final class Reading {
        let last: ObjectIdentifier
        let members: Set<ObjectIdentifier>
        let finished: @MainActor () -> Void
        init(last: ObjectIdentifier, members: Set<ObjectIdentifier>, finished: @escaping @MainActor () -> Void) {
            self.last = last; self.members = members; self.finished = finished
        }
    }
    private var readings: [ObjectIdentifier: Reading] = [:]

    public override init() {
        super.init()
        synthesizer.delegate = self
    }

    public var voices: [SpeechVoiceInfo] {
        AVSpeechSynthesisVoice.speechVoices().map { voice in
            let quality: SpeechVoiceInfo.Quality
            switch voice.quality {
            case .premium: quality = .premium
            case .enhanced: quality = .enhanced
            default: quality = .standard
            }
            return SpeechVoiceInfo(identifier: voice.identifier, language: voice.language, quality: quality)
        }
    }

    public func speak(_ parts: [SpeechPart], finished: @escaping @MainActor () -> Void) {
        let utterances = parts.map { part -> AVSpeechUtterance in
            let utterance = AVSpeechUtterance(string: part.text)
            if let id = part.voiceIdentifier { utterance.voice = AVSpeechSynthesisVoice(identifier: id) }
            return utterance
        }
        guard let last = utterances.last else { MainActor.assumeIsolated { finished() }; return }
        let reading = Reading(last: ObjectIdentifier(last), members: Set(utterances.map(ObjectIdentifier.init)),
                              finished: finished)
        for utterance in utterances {
            readings[ObjectIdentifier(utterance)] = reading
            synthesizer.speak(utterance)
        }
    }

    public func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        done(utterance, cancelled: false)
    }

    public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        done(utterance, cancelled: true)
    }

    /// Delegate callbacks may arrive off the main thread; `readings` is only touched on main (speak runs there).
    private func done(_ utterance: AVSpeechUtterance, cancelled: Bool) {
        let key = ObjectIdentifier(utterance)
        DispatchQueue.main.async { [self] in
            guard let reading = readings[key] else { return }
            readings[key] = nil
            guard cancelled || key == reading.last else { return }   // a middle part finished: keep going
            for member in reading.members { readings[member] = nil }
            MainActor.assumeIsolated { reading.finished() }
        }
    }
}
