# UI-V2-S1 — macOS popover foundation

- Baseline: `93e9ddcddbc109666e4c9b93c8da155ee7f74ccf`, `p0/desktop-probe`, Draft PR #1.
- The user's subsequent instruction authorizes completing S1–S8 in this run. Each slice retains its own checks and commit.

Implemented a 420×600pt NSPopover with local WKWebView assets, Python acquisition on a worker thread, task search, health diagnostics and last-success time. No HTTP server, model call or external web content is required. Existing `observer.menu` remains available as the NSMenu fallback. Task detail is a navigation placeholder at this slice; editing and later screen requirements are not claimed.

Native NSStackView/NSScrollView objects and WKWebView were both instantiated. WKWebView was selected for flexible long text, keyed DOM updates and the familiar JS stack; Python retains all state access. Swift and Electron were not added. WebKit 12.2.2 is installed beside the existing Cocoa bridge. The installed PyObjC formal WKNavigationDelegate declaration lost completion-block signatures; using its registered NSObject selector metadata fixed the callback without bypassing navigation checks. An actual allowed local load, script message and rejected other-file navigation were verified.

## Verification (2026-10-31)

- 40 Python tests passed: popover, menu/setup and S0 projection regressions.
- Node frontend checks: 18 assertions passed.
- Actual macOS NSPopover: local page loaded, 7 synthetic tasks visible, task entry/back, search and search retention after refresh verified through accessibility and screenshot.
- Native probe: 420×600, popover shown, WebKit error null, native stack and scroll objects created.
- Large nanosecond timestamps are serialized losslessly as decimal strings across the JS boundary.
- Evidence kept locally in `/tmp/devin-ui-v2/`; fixtures are synthetic. No real Devin session was changed.

Keyboard/focus, light appearance, writes, restart and 5–10 session acceptance will be checked with the completed interface in S8. This slice does not make the popover the installed app's default entry point yet.
