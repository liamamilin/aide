import AppKit
import ImageIO
import UniformTypeIdentifiers

/// Region screenshot through the system's own `screencapture` (crosshair, Esc cancels), then a PNG whose
/// longest edge is bounded so the Qt-free core can hand it to the model without decoding anything.
public enum ScreenCapture {
    public static let maxEdge = 2048

    /// The prepared PNG, or nil when the person cancelled (or capture is not permitted).
    public static func captureRegion() async -> URL? {
        let raw = FileManager.default.temporaryDirectory.appendingPathComponent("aide-shot-\(UUID().uuidString).png")
        defer { try? FileManager.default.removeItem(at: raw) }
        let finished: Bool = await withCheckedContinuation { continuation in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            process.arguments = ["-i", "-x", raw.path]          // interactive selection, no shutter sound
            process.terminationHandler = { _ in continuation.resume(returning: true) }
            do { try process.run() } catch { continuation.resume(returning: false) }
        }
        guard finished, FileManager.default.fileExists(atPath: raw.path) else { return nil }
        return prepare(raw)
    }

    /// Downscales to `maxEdge` and writes a fresh PNG; nil for anything that is not a decodable image.
    public static func prepare(_ source: URL, maxEdge: Int = ScreenCapture.maxEdge) -> URL? {
        guard let input = CGImageSourceCreateWithURL(source as CFURL, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: maxEdge,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(input, 0, options as CFDictionary) else { return nil }
        let out = FileManager.default.temporaryDirectory.appendingPathComponent("aide-ask-\(UUID().uuidString).png")
        guard let destination = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(destination, image, nil)
        return CGImageDestinationFinalize(destination) ? out : nil
    }

    /// Whether macOS lets Aide capture other apps' windows (the first capture prompts for it).
    public static var hasScreenAccess: Bool { CGPreflightScreenCaptureAccess() }
}
