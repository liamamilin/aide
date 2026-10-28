import Foundation

/// Spawns the Python core as a child process and speaks NDJSON over its stdin/stdout.
/// No network port is opened; the core dies with the shell (it exits on stdin EOF).
public actor CoreClient {
    public struct Launch: Sendable {
        public var executable: URL
        public var arguments: [String]
        public var environment: [String: String]
        public init(executable: URL, arguments: [String], environment: [String: String] = [:]) {
            self.executable = executable
            self.arguments = arguments
            self.environment = environment
        }
    }

    private let launch: Launch
    private var process: Process?
    private var input: FileHandle?
    private var pending: [String: CheckedContinuation<JSONValue, Error>] = [:]
    private var nextID = 0
    private var router = RunRouter()
    private var eventContinuation: AsyncStream<CoreEvent>.Continuation?

    /// Core-originated notifications (`core.ready`, run events, ...).
    public nonisolated let events: AsyncStream<CoreEvent>

    public init(launch: Launch) {
        self.launch = launch
        var continuation: AsyncStream<CoreEvent>.Continuation!
        self.events = AsyncStream { continuation = $0 }
        self.eventContinuation = continuation
    }

    public func start() throws {
        guard process == nil else { return }
        let proc = Process()
        proc.executableURL = launch.executable
        proc.arguments = launch.arguments
        proc.environment = ProcessInfo.processInfo.environment.merging(launch.environment) { $1 }
        let stdin = Pipe(), stdout = Pipe()
        proc.standardInput = stdin
        proc.standardOutput = stdout
        proc.standardError = FileHandle.standardError
        proc.terminationHandler = { [weak self] _ in
            Task { await self?.handleTermination() }
        }
        try proc.run()
        process = proc
        input = stdin.fileHandleForWriting
        let reader = stdout.fileHandleForReading
        Task.detached { [weak self] in
            // availableData blocks, so this runs off the actor on its own task.
            var buffer = Data()
            while true {
                let chunk = reader.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 0x0A) {
                    let line = buffer.subdata(in: buffer.startIndex..<newline)
                    buffer.removeSubrange(buffer.startIndex...newline)
                    await self?.receive(line)
                }
            }
        }
    }

    public func call(_ method: String, params: [String: JSONValue] = [:]) async throws -> JSONValue {
        guard let input, process?.isRunning == true else { throw CoreError.notRunning }
        nextID += 1
        let id = String(nextID)
        var message = WireMessage()
        message.id = id
        message.method = method
        message.params = .object(params)
        var line = try JSONEncoder().encode(message)
        line.append(0x0A)
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do { try input.write(contentsOf: line) }
            catch { pending[id] = nil; continuation.resume(throwing: error) }
        }
    }

    /// Ask the core to stop, then close its stdin so it exits. Waits up to ~2 s, then terminates it.
    /// (`waitUntilExit` is avoided: it can block indefinitely even after the child has gone.)
    public func stop() async {
        _ = try? await call("shutdown")
        try? input?.close()
        let proc = process
        // `process` is cleared by the termination handler, which is the reliable exit signal.
        for _ in 0..<40 where process != nil { try? await Task.sleep(nanoseconds: 50_000_000) }
        if process != nil { proc?.terminate() }
    }

    func registerRun(_ runID: String) -> AsyncStream<RunUpdate> { router.register(runID: runID) }

    private func receive(_ line: Data) {
        guard let message = try? JSONDecoder().decode(WireMessage.self, from: line) else { return }
        if let name = message.event {
            let event = CoreEvent(name: name, data: message.data ?? .null)
            router.route(event)
            eventContinuation?.yield(event)
        } else if let id = message.id, let continuation = pending.removeValue(forKey: id) {
            if let failure = message.error {
                continuation.resume(throwing: CoreError.remote(code: failure.code, message: failure.message))
            } else {
                continuation.resume(returning: message.result ?? .null)
            }
        }
    }

    private func handleTermination() {
        for continuation in pending.values { continuation.resume(throwing: CoreError.terminated) }
        pending.removeAll()
        router.failAll(message: "Aide 核心已退出")
        eventContinuation?.finish()
        process = nil
        input = nil
    }
}
