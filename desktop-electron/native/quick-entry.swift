import AppKit
import CoreGraphics

// Listen only: no keystrokes are intercepted or recorded. Native modifier taps
// are unavailable through Electron's accelerator API.
let requestedKey = CommandLine.arguments.dropFirst().first ?? "option"
let target: CGEventFlags = requestedKey == "control" ? .maskControl : requestedKey == "command" ? .maskCommand : .maskAlternate
var pressed = false
var lastRelease: TimeInterval = 0
var cleanTap = false
func emit(_ value: String) { print(value); fflush(stdout) }
var activeTap: CFMachPort?
let mask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.leftMouseDown.rawValue) | (1 << CGEventType.rightMouseDown.rawValue)
let callback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let tap = activeTap { CGEvent.tapEnable(tap: tap, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    if type == .keyDown || type == .leftMouseDown || type == .rightMouseDown { cleanTap = false; lastRelease = 0 }
    if type == .flagsChanged {
        let down = event.flags.contains(target)
        let others = event.flags.intersection([.maskCommand, .maskControl, .maskAlternate, .maskShift]).subtracting(target)
        if down && !pressed { cleanTap = others.isEmpty }
        if !others.isEmpty { cleanTap = false; lastRelease = 0 }
        if !down && pressed && cleanTap {
            let now = ProcessInfo.processInfo.systemUptime
            if lastRelease > 0 && now - lastRelease < 0.4 { emit("toggle"); lastRelease = 0 }
            else { lastRelease = now }
        }
        pressed = down
    }
    return Unmanaged.passUnretained(event)
}
if !CGPreflightListenEventAccess() && !CGRequestListenEventAccess() {
    emit("permission-required")
    exit(2)
}
guard let tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: CGEventMask(mask), callback: callback, userInfo: nil) else {
    emit("permission-required")
    exit(2)
}
activeTap = tap
let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
CGEvent.tapEnable(tap: tap, enable: true)
emit("ready")
CFRunLoopRun()
