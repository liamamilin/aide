import AppKit
import XCTest
@testable import AideShell

final class ScreenCaptureTests: XCTestCase {
    private func writePNG(width: Int, height: Int) throws -> URL {
        let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width, pixelsHigh: height, bitsPerSample: 8,
                                   samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                   bytesPerRow: 0, bitsPerPixel: 0)!
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("sc-\(UUID().uuidString).png")
        try rep.representation(using: .png, properties: [:])!.write(to: url)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testLargeScreenshotsAreBoundedAndStayPNG() throws {
        let out = try XCTUnwrap(ScreenCapture.prepare(try writePNG(width: 3000, height: 1000), maxEdge: 1024))
        addTeardownBlock { try? FileManager.default.removeItem(at: out) }
        let rep = try XCTUnwrap(NSBitmapImageRep(data: try Data(contentsOf: out)))
        XCTAssertEqual(max(rep.pixelsWide, rep.pixelsHigh), 1024)
        XCTAssertEqual(try Data(contentsOf: out).prefix(4), Data([0x89, 0x50, 0x4E, 0x47]))
    }

    func testNonImagesAreRejected() throws {
        let junk = FileManager.default.temporaryDirectory.appendingPathComponent("junk-\(UUID().uuidString).png")
        try Data("not an image".utf8).write(to: junk)
        addTeardownBlock { try? FileManager.default.removeItem(at: junk) }
        XCTAssertNil(ScreenCapture.prepare(junk))
    }
}
