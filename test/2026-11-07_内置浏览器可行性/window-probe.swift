import Foundation
import CoreGraphics

// Window metadata only. Does not capture pixels or request accessibility access.
let pid = Int(CommandLine.arguments.dropFirst().first ?? "-1") ?? -1
let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
let matches = windows.filter { ($0[kCGWindowOwnerPID as String] as? Int) == pid }.map { window -> [String: Any] in
    var result: [String: Any] = [:]
    for key in [kCGWindowNumber, kCGWindowOwnerPID, kCGWindowLayer, kCGWindowBounds, kCGWindowIsOnscreen] {
        result[key as String] = window[key as String]
    }
    return result
}
let data = try JSONSerialization.data(withJSONObject: matches, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
