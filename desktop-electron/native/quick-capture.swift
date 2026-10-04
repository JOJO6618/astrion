import AppKit
import ScreenCaptureKit

// Window discovery uses public WindowServer metadata. Capture requires the
// existing screen-recording permission, but discovery never prompts for it.
@main
@MainActor
struct QuickCapture {
    static func main() async {
        // Each command is a fresh process. Discovery happened to initialize
        // AppKit through NSScreen, but capture previously reached SCK without
        // connecting to WindowServer (CGS_REQUIRE_INIT / did_initialize).
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        _ = NSScreen.screens
        do {
            let args = CommandLine.arguments
            if args.count == 3, args[1] == "permission" {
                let granted = args[2] == "request" ? CGRequestScreenCaptureAccess() : CGPreflightScreenCaptureAccess()
                FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["granted": granted]))
                return
            }
            if args.count == 3, args[1] == "windows", let clientPID = Int32(args[2]) {
                try writeWindows(excluding: clientPID)
                return
            }
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let filter: SCContentFilter
            let config = SCStreamConfiguration()
            config.showsCursor = false
            config.captureResolution = .best
            config.ignoreShadowsSingleWindow = true

            if args.count == 4, args[1] == "window",
               let windowID = UInt32(args[2]), let clientPID = Int32(args[3]) {
                guard let window = content.windows.first(where: {
                    $0.windowID == windowID
                        && ($0.owningApplication?.processID != clientPID || $0.windowLayer == 0)
                }) else { throw failure("The selected window is no longer available") }
                filter = SCContentFilter(desktopIndependentWindow: window)
                config.width = max(1, Int(ceil(Double(filter.contentRect.width) * Double(filter.pointPixelScale))))
                config.height = max(1, Int(ceil(Double(filter.contentRect.height) * Double(filter.pointPixelScale))))
            } else {
                guard args.count == 7,
                      let displayID = UInt32(args[1]), let clientPID = Int32(args[2]),
                      let x = Double(args[3]), let y = Double(args[4]),
                      let width = Double(args[5]), let height = Double(args[6]),
                      [x, y, width, height].allSatisfy({ $0.isFinite }),
                      width >= 3, height >= 3 else { throw failure("Invalid capture region") }
                guard let display = content.displays.first(where: { $0.displayID == displayID }),
                      let client = content.applications.first(where: { $0.processID == clientPID }) else {
                    throw failure("The selected display is no longer available")
                }
                filter = SCContentFilter(display: display, excludingApplications: [client], exceptingWindows: [])
                config.sourceRect = CGRect(x: x, y: y, width: width, height: height)
                config.width = Int(ceil(width * Double(filter.pointPixelScale)))
                config.height = Int(ceil(height * Double(filter.pointPixelScale)))
            }
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
            let bitmap = NSBitmapImageRep(cgImage: image)
            guard let png = bitmap.representation(using: .png, properties: [:]) else {
                throw failure("Cannot encode the captured image")
            }
            FileHandle.standardOutput.write(png)
        } catch {
            let failure = error as NSError
            FileHandle.standardError.write(Data("Capture failed [\(failure.domain):\(failure.code)]: \(failure.localizedDescription)\n".utf8))
            exit(1)
        }
    }

    static func failure(_ message: String) -> NSError {
        NSError(domain: "QuickCapture", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    static func writeWindows(excluding clientPID: Int32) throws {
        guard let windows = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID
        ) as? [[String: Any]] else { throw failure("Cannot read the desktop window list") }
        var result: [[String: Any]] = []
        let displayBounds = NSScreen.screens.compactMap { screen -> CGRect? in
            guard let number = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber else {
                return nil
            }
            return CGDisplayBounds(number.uint32Value)
        }
        // CGWindowListCopyWindowInfo returns front-to-back ordering. Keep system
        // panels as occluders even when they do not qualify for capture buttons.
        for info in windows {
            guard let pid = (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
                  let number = info[kCGWindowNumber as String] as? NSNumber,
                  let layer = (info[kCGWindowLayer as String] as? NSNumber)?.intValue,
                  layer >= 0,
                  let bounds = info[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
                  rect.width > 0, rect.height > 0,
                  ((info[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1) > 0 else { continue }
            // Ignore our floating quick/capture layers, but keep ordinary Astrion
            // windows so they occlude lower applications and can be captured too.
            if pid == clientPID && layer > 0 { continue }
            let application = NSRunningApplication(processIdentifier: pid)
            // Dock owns screen-sized desktop/input surfaces whose CGWindowAlpha
            // is 1 even though the content is transparent. They precede app
            // windows and must not wipe out every application's button. Keep
            // ordinary Dock/menu rectangles in the occlusion list.
            if application?.bundleIdentifier == "com.apple.dock",
               displayBounds.contains(where: { rect.contains($0) }) { continue }
            let name = application?.localizedName ?? (info[kCGWindowOwnerName as String] as? String) ?? ""
            let candidate = layer == 0 && rect.width >= 140 && rect.height >= 80
                && application?.activationPolicy == .regular && !name.isEmpty
            result.append([
                "id": number.uint32Value, "pid": pid, "app": name,
                "candidate": candidate,
                "x": rect.origin.x, "y": rect.origin.y,
                "width": rect.width, "height": rect.height
            ])
        }
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result))
    }
}
