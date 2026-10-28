import Foundation

/// One line of the NDJSON protocol (see ai_desktop/core/protocol.py). Exactly one shape is populated.
struct WireMessage: Codable {
    struct Failure: Codable, Sendable { let code: String; let message: String }

    var id: String?
    var method: String?
    var params: JSONValue?
    var result: JSONValue?
    var error: Failure?
    var event: String?
    var data: JSONValue?
}

public struct CoreEvent: Sendable, Equatable {
    public let name: String
    public let data: JSONValue
}

public enum CoreError: Error, Equatable {
    case notRunning
    case terminated
    case remote(code: String, message: String)
}
