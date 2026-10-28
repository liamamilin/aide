import AppKit
import SwiftUI

/// The accent used for highlights and controls: the system accent unless the person sets their own with
/// `defaults write com.milin.aide accentHex -string RRGGBB`. Kept as a plain per-user preference so the app
/// carries no fixed palette of its own.
public enum Theme {
    public static var accent: Color {
        guard let hex = UserDefaults.standard.string(forKey: "accentHex"), let value = parse(hex) else { return .accentColor }
        return value
    }

    static func parse(_ text: String) -> Color? {
        let hex = text.trimmingCharacters(in: CharacterSet(charactersIn: "# ").union(.whitespaces))
        guard hex.count == 6, let number = UInt32(hex, radix: 16) else { return nil }
        return Color(red: Double((number >> 16) & 0xFF) / 255, green: Double((number >> 8) & 0xFF) / 255,
                     blue: Double(number & 0xFF) / 255)
    }
}
