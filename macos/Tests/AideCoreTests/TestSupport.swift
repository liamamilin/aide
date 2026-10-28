import Foundation
import XCTest
@testable import AideCore

/// Spawns tests/fake_ollama_cli.py and returns its loopback URL.
final class FakeOllamaProcess {
    let process = Process()
    let stdin = Pipe()
    let url: String

    init(mode: String) throws {
        let python = ProcessInfo.processInfo.environment["AIDE_PYTHON"] ?? "python3"
        let out = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [python, repoRoot.appendingPathComponent("tests/fake_ollama_cli.py").path, mode]
        process.standardInput = stdin
        process.standardOutput = out
        try process.run()
        let line = String(decoding: out.fileHandleForReading.availableData, as: UTF8.self)
        url = line.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Terminate rather than wait for stdin EOF (and never use waitUntilExit, which can hang even after the child is gone): another child can inherit the pipe's write end,
    /// so EOF is not guaranteed and a wait here could hang the whole test run.
    func stop() {
        process.terminate()
        for _ in 0..<100 where process.isRunning { Thread.sleep(forTimeInterval: 0.03) }
    }
}

let repoRoot: URL = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent()

func makeCore(ollamaURL: String? = nil) throws -> (CoreClient, URL) {
    let dataDir = FileManager.default.temporaryDirectory.appendingPathComponent("aide-test-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
    var env = ["AIDE_DATA_DIR": dataDir.path, "PYTHONPATH": repoRoot.path]
    if let ollamaURL { env["AIDE_OLLAMA_URL"] = ollamaURL }
    let python = ProcessInfo.processInfo.environment["AIDE_PYTHON"] ?? "python3"
    let client = CoreClient(launch: .init(executable: URL(fileURLWithPath: "/usr/bin/env"),
                                          arguments: [python, "-m", "ai_desktop.core"], environment: env))
    return (client, dataDir)
}

func collect(_ handle: RunHandle) async -> [RunUpdate] {
    var all: [RunUpdate] = []
    for await update in handle.updates { all.append(update) }
    return all
}
