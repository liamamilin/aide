import AppKit

/// The app's main menu. Aide is a menu-bar app and used to have none, so ⌘C / ⌘V / ⌘X / ⌘A / ⌘Z in text fields
/// had no Edit menu to route through and macOS only beeped. The standard Edit items fix that for the Workspace
/// and Settings windows, and also show in the menu bar while the Workspace is open.
@MainActor
public enum AppMenu {
    public static func install() {
        let main = NSMenu()

        let app = NSMenu(title: "Aide")
        app.addItem(withTitle: "关于 Aide", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        app.addItem(withTitle: "隐藏 Aide", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "退出 Aide", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        main.addItem(submenu(app, title: "Aide"))

        main.addItem(submenu(editMenu(), title: "编辑"))

        let window = NSMenu(title: "窗口")
        window.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        window.addItem(withTitle: "关闭", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        main.addItem(submenu(window, title: "窗口"))
        NSApp.windowsMenu = window

        NSApp.mainMenu = main
        installPanelShortcuts()
    }

    static func editMenu() -> NSMenu {
        let edit = NSMenu(title: "编辑")
        edit.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        return edit
    }

    /// The Quick Panel never activates Aide (so the app you came from keeps focus), and an inactive app's main menu
    /// gets no key equivalents. While one of our windows is key but Aide is not active, the edit shortcuts are sent
    /// down the responder chain directly; anything nobody handles passes through unchanged.
    static func installPanelShortcuts() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            guard !NSApp.isActive, NSApp.keyWindow != nil, let action = editAction(for: event) else { return event }
            return NSApp.sendAction(action, to: nil, from: nil) ? nil : event
        }
    }

    /// ⌘X/C/V/A/Z and ⇧⌘Z (no other modifiers), as the matching responder action.
    nonisolated static func editAction(for event: NSEvent) -> Selector? {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        let shift = flags.contains(.shift)
        guard flags.subtracting(.shift).subtracting(.capsLock) == .command else { return nil }
        switch (event.charactersIgnoringModifiers?.lowercased(), shift) {
        case ("x", false): return #selector(NSText.cut(_:))
        case ("c", false): return #selector(NSText.copy(_:))
        case ("v", false): return #selector(NSText.paste(_:))
        case ("a", false): return #selector(NSText.selectAll(_:))
        case ("z", false): return Selector(("undo:"))
        case ("z", true): return Selector(("redo:"))
        default: return nil
        }
    }

    private static func submenu(_ menu: NSMenu, title: String) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }
}
