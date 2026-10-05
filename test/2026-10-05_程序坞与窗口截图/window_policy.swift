import Foundation

@main
struct WindowPolicyTests {
    static func main() {
        let screen = CGRect(x: 0, y: 0, width: 1470, height: 956)
        let external = CGRect(x: -1920, y: 0, width: 1920, height: 1080)
        let displays = [screen, external]
        let checks: [(String, Bool)] = [
            ("screenshot full-screen carrier", isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: screen, displays: displays)),
            ("Dock full-screen carrier", isSystemScreenCarrier(bundleIdentifier: "com.apple.dock", bounds: screen, displays: displays)),
            ("screenshot toolbar is retained", !isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: CGRect(x: 400, y: 800, width: 600, height: 80), displays: displays)),
            ("ordinary Dock is retained", !isSystemScreenCarrier(bundleIdentifier: "com.apple.dock", bounds: CGRect(x: 300, y: 860, width: 900, height: 96), displays: displays)),
            ("ordinary full-screen app is retained", !isSystemScreenCarrier(bundleIdentifier: "com.example.editor", bounds: screen, displays: displays)),
            ("unknown full-screen app is retained", !isSystemScreenCarrier(bundleIdentifier: nil, bounds: screen, displays: displays)),
            ("negative-coordinate display carrier", isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: external, displays: displays)),
            ("partial screen overlap is retained", !isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: CGRect(x: 0, y: 1, width: 1470, height: 955), displays: displays)),
            ("multi-display carrier", isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: CGRect(x: -1920, y: 0, width: 3390, height: 1080), displays: displays)),
            ("no display cannot identify a carrier", !isSystemScreenCarrier(bundleIdentifier: "com.apple.screencaptureui", bounds: screen, displays: []))
        ]
        for (name, passed) in checks {
            precondition(passed, name)
            print("PASS: \(name)")
        }
    }
}
