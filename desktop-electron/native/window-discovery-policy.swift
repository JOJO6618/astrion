import Foundation
import CoreGraphics

// These system apps expose screen-sized input/desktop surfaces as opaque
// WindowServer rectangles. Keep their smaller panels, and all ordinary apps.
func isSystemScreenCarrier(bundleIdentifier: String?, bounds: CGRect, displays: [CGRect]) -> Bool {
    guard bundleIdentifier == "com.apple.dock" || bundleIdentifier == "com.apple.screencaptureui" else {
        return false
    }
    return displays.contains { bounds.contains($0) }
}
