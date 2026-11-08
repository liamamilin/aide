import XCTest
@testable import AideShell

private final class FakeSynth: SpeechSynthesizing {
    var voices: [SpeechVoiceInfo] = []
    var spoken: [(text: String, voice: String?)] = []
    var batches: [[SpeechPart]] = []
    var pending: [@MainActor () -> Void] = []
    var stops = 0
    func speak(_ parts: [SpeechPart], finished: @escaping @MainActor () -> Void) {
        batches.append(parts)
        spoken.append((parts.map(\.text).joined(), parts.first?.voiceIdentifier)); pending.append(finished)
    }
    func stop() { stops += 1 }
}

@MainActor
final class SpeechReaderTests: XCTestCase {
    func testPicksBestQualityThenRegionForDetectedLanguage() {
        let voices = [
            SpeechVoiceInfo(identifier: "en-gb-std", language: "en-GB", quality: .standard),
            SpeechVoiceInfo(identifier: "en-us-std", language: "en-US", quality: .standard),
            SpeechVoiceInfo(identifier: "en-gb-premium", language: "en-GB", quality: .premium),
            SpeechVoiceInfo(identifier: "zh-cn", language: "zh-CN", quality: .enhanced),
        ]
        XCTAssertEqual(SpeechReader.pickVoice(for: "en", among: voices, preferredRegion: "US")?.identifier, "en-gb-premium")
        XCTAssertEqual(SpeechReader.pickVoice(for: "en", among: Array(voices.prefix(2)), preferredRegion: "US")?.identifier, "en-us-std")
        XCTAssertEqual(SpeechReader.pickVoice(for: "zh", among: voices, preferredRegion: "US")?.identifier, "zh-cn")
        XCTAssertNil(SpeechReader.pickVoice(for: "fr", among: voices, preferredRegion: "US"))
        XCTAssertNil(SpeechReader.pickVoice(for: nil, among: voices, preferredRegion: "US"))
    }

    func testDetectsLanguage() {
        XCTAssertEqual(SpeechReader.language(of: "The quick brown fox jumps over the lazy dog."), "en")
        XCTAssertEqual(SpeechReader.language(of: "今天天气很好，我们去公园散步吧。"), "zh")
    }

    func testSpeaksTrimmedTextWithMatchingVoiceAndToggleStops() {
        let synth = FakeSynth()
        synth.voices = [SpeechVoiceInfo(identifier: "en", language: "en-US", quality: .standard)]
        let reader = SpeechReader(synthesizer: synth)
        reader.speak("   ")
        XCTAssertTrue(synth.spoken.isEmpty)

        reader.toggle("  Hello there, how are you doing today?  ")
        XCTAssertTrue(reader.isSpeaking)
        XCTAssertEqual(synth.spoken.first?.text, "Hello there, how are you doing today?")
        XCTAssertEqual(synth.spoken.first?.voice, "en")

        reader.toggle("ignored")
        XCTAssertFalse(reader.isSpeaking)
        XCTAssertEqual(synth.stops, 1)
        XCTAssertEqual(synth.spoken.count, 1)
    }

    func testStaleFinishDoesNotEndNewerSpeech() {
        let synth = FakeSynth()
        let reader = SpeechReader(synthesizer: synth)
        reader.speak("first sentence to read")
        reader.speak("second sentence to read")   // replaces the first
        synth.pending[0]()                        // the first one's late cancel callback
        XCTAssertTrue(reader.isSpeaking)
        synth.pending[1]()
        XCTAssertFalse(reader.isSpeaking)
    }

    func testMarkdownIsSpokenAsPlainText() {
        let md = """
        ## 动词
        1. **doubt** (怀疑) - 例: *I doubt his story.*
        - `skeptical` 是形容词
        > quoted
        | 词 | 意思 |
        |---|---|
        | doubt | 怀疑 |
        ```
        code
        ```
        """
        XCTAssertEqual(SpeechReader.plainText(fromMarkdown: md),
                       "动词\n1. doubt (怀疑) - 例: I doubt his story.\nskeptical 是形容词\nquoted\n词, 意思\ndoubt, 怀疑\ncode")
    }

    func testOnlyTheMessageBeingReadReportsReading() {
        let synth = FakeSynth()
        let reader = SpeechReader(synthesizer: synth)
        reader.toggle(markdown: "**Hello** world, nice to meet you.", source: "a")
        XCTAssertEqual(synth.spoken.last?.text, "Hello world, nice to meet you.")
        XCTAssertTrue(reader.isReading("a"))
        XCTAssertFalse(reader.isReading("b"))
        reader.toggle(markdown: "Another message entirely here.", source: "b")   // switches, does not just stop
        XCTAssertTrue(reader.isReading("b"))
        XCTAssertEqual(synth.spoken.count, 2)
        reader.toggle(markdown: "ignored", source: "b")
        XCTAssertFalse(reader.isSpeaking)
    }

    func testMixedTextIsReadWithAVoicePerLanguage() {
        let runs = SpeechReader.segments(of: "例句：She remained skeptical despite his promises. 很好")
        XCTAssertEqual(runs.map(\.text), ["例句：", "She remained skeptical despite his promises. ", "很好"])
        XCTAssertEqual(runs.map(\.language), ["zh", "en", "zh"])
        XCTAssertEqual(SpeechReader.segments(of: "Bonjour, comment allez-vous aujourd'hui ?").count, 1)

        let synth = FakeSynth()
        synth.voices = [SpeechVoiceInfo(identifier: "ava", language: "en-US", quality: .premium),
                        SpeechVoiceInfo(identifier: "tingting", language: "zh-CN", quality: .standard)]
        SpeechReader(synthesizer: synth).speak("doubt (怀疑) - 例: I doubt his story.")
        XCTAssertEqual(synth.batches.first?.map(\.voiceIdentifier), ["ava", "tingting", "ava"])
    }

    func testButtonReadsSelectionOnlyWhenItComesFromThatMessage() {
        let md = "1. **question** (质疑) - 例: *She questioned the validity of the data.*"
        XCTAssertTrue(SpeechReader.selection("She questioned the validity\nof the data.", isPartOf: md))
        XCTAssertFalse(SpeechReader.selection("something typed in the composer", isPartOf: md))
        XCTAssertFalse(SpeechReader.selection("  ...  ", isPartOf: md))

        let synth = FakeSynth()
        let reader = SpeechReader(synthesizer: synth)
        reader.toggle(markdown: md, selection: "She questioned the validity of the data.", source: "m")
        XCTAssertEqual(synth.spoken.last?.text, "She questioned the validity of the data.")
        reader.stop()
        reader.toggle(markdown: md, selection: "unrelated words", source: "m")
        XCTAssertEqual(synth.spoken.last?.text, "1. question (质疑) - 例: She questioned the validity of the data.")
    }
}
