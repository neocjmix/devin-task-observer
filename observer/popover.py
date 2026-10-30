"""Optional macOS popover: local HTML/JS presentation, Python state and actions."""
import argparse
import copy
import json
from pathlib import Path
import signal
import threading

from .cooperative import CooperativeState
from .file_store import StateError
from .integration import session_activity
from .menu import DEFAULT_ROOT
from .runtime import CollectorRuntime, collector_status, service_running
from .state_observations import observations
from .ui_projection import project

ASSETS = Path(__file__).with_name('ui_v2')


def wire_value(value):
    """Preserve SQLite nanoseconds across the JavaScript safe-integer boundary."""
    if type(value) is int and abs(value) > 2 ** 53 - 1:
        return str(value)
    if isinstance(value, dict):
        return {key: wire_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [wire_value(child) for child in value]
    return value


def acquire(root, config, config_path, runtime=None):
    from .app import load_config
    rows, activity, errors, facts = [], {}, [], None
    try:
        if load_config(config_path) != config:
            errors.append({'code': 'configuration_changed', 'message': '설정이 변경됐습니다. Observer를 재시작하세요.'})
        else:
            facts = observations(config_path)
    except (ValueError, OSError) as error:
        errors.append({'code': 'configuration_error', 'message': str(error)})
    try:
        rows = CooperativeState(root, lock_timeout=.25).list()
        for row in rows:
            state = row.get('state') if row['health'] == 'ok' else row.get('last_good')
            for edge in (state or {}).get('contributions', []):
                sid = edge['session_id']
                if sid not in activity:
                    activity[sid] = session_activity(root, sid)
    except (StateError, OSError, ValueError) as error:
        errors.append({'code': getattr(error, 'code', 'state_unavailable'), 'message': str(error)})
    run = runtime.snapshot() if runtime else {'running': service_running(config), 'collector': collector_status(config)}
    return project(rows, observations=facts, session_activity=activity, runtime=run, state_errors=errors)


class ProjectionController:
    """State acquisition stays off the Cocoa thread; no HTTP listener or model."""
    def __init__(self, root=None, config=None, config_path=None, runtime=None, fixture=None, interval=2):
        self.root, self.config, self.config_path = root, config, config_path
        self.runtime, self.fixture, self.interval = runtime, fixture, interval
        self._stop, self._wake, self._guard = threading.Event(), threading.Event(), threading.Lock()
        self._value, self._thread = None, None

    def collect(self):
        if self.fixture is not None:
            return project(**json.loads(Path(self.fixture).read_text()))
        return acquire(self.root, self.config, self.config_path, self.runtime)

    def start(self):
        self._thread = threading.Thread(target=self._poll, name='observer-popover-state', daemon=True)
        self._thread.start()

    def _poll(self):
        while not self._stop.is_set():
            self._wake.clear()
            try:
                value = self.collect()
            except (ValueError, OSError, StateError, KeyError, TypeError) as error:
                value = project([], state_errors=[{'code': 'projection_unavailable', 'message': str(error)}])
            with self._guard:
                self._value = value
            self._wake.wait(self.interval)

    def latest(self):
        with self._guard:
            return copy.deepcopy(self._value)

    def refresh(self):
        if self.runtime:
            self.runtime.request_refresh()
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=4)


def run_native(controller, *, show=False, probe=None):
    """Local-only WKWebView in an NSPopover, never a browser window."""
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory, NSStatusBar,
                        NSVariableStatusItemLength, NSImage, NSViewController, NSPopover,
                        NSPopoverBehaviorTransient, NSMakeRect, NSMakeSize, NSMinYEdge, NSView,
                        NSTextField, NSScrollView, NSStackView)
    from Foundation import NSObject, NSTimer, NSURL
    from WebKit import WKWebView, WKWebViewConfiguration, WKWebsiteDataStore

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    stopped = threading.Event()
    asset_url = NSURL.fileURLWithPath_(str(ASSETS / 'index.html'))
    # Use WebKit's NSObject selector metadata for navigation. Explicit formal
    # WKNavigationDelegate conformance drops completion-block signatures on the
    # tested PyObjC versions. The selector still enforces the same URL policy.
    class PopoverDelegate(NSObject, protocols=[objc.protocolNamed('WKScriptMessageHandler')]):
        def toggle_(self, sender):
            if self.popover.isShown():
                self.popover.performClose_(None)
            else:
                screen = self.status.button().window().screen()
                height = min(600, screen.visibleFrame().size.height - 60) if screen else 600
                self.popover.setContentSize_(NSMakeSize(420, max(300, height)))
                self.popover.showRelativeToRect_ofView_preferredEdge_(self.status.button().bounds(), self.status.button(), NSMinYEdge)
                app.activateIgnoringOtherApps_(True)
                if self.web.window():
                    self.web.window().makeKeyWindow()
                    self.web.window().makeFirstResponder_(self.web)

        def userContentController_didReceiveScriptMessage_(self, user_controller, message):
            # Never accept script messages from external/subframe documents.
            if not message.frameInfo().isMainFrame():
                return
            request_url = message.frameInfo().request().URL()
            if request_url is None or request_url.absoluteString() != asset_url.absoluteString():
                return
            try:
                body = dict(message.body())
            except (TypeError, ValueError):
                return
            action = body.get('action')
            if action == 'ready':
                self.ready = True
            elif action == 'refresh':
                controller.refresh()
            elif action == 'close':
                self.popover.performClose_(None)
            elif action == 'quit':
                stopped.set()

        def webView_decidePolicyForNavigationAction_preferences_decisionHandler_(self, web, action, preferences, decide):
            url = action.request().URL()
            decide(1 if url and url.absoluteString() == asset_url.absoluteString() else 0, preferences)

        def tick_(self, timer):
            if stopped.is_set():
                controller.stop()
                if controller.runtime:
                    controller.runtime.stop()
                app.stop_(None)
                return
            if self.pending_show:
                self.pending_show = False
                self.toggle_(None)
            value = controller.latest()
            if not self.ready or value is None:
                return
            if value != self.previous:
                self.previous = value
                health = value['health']
                troubled = bool(health['state_errors'] or
                                ((health['runtime'] or {}).get('collector') or {}).get('last_error') or
                                ((health['observations'] or {}).get('warnings')) or
                                any(t['health'] != 'ok' or t['projection_errors'] for t in value['tasks'].values()))
                icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                    'exclamationmark.circle' if troubled else 'circle.hexagongrid', 'Task Observer')
                icon.setTemplate_(True)
                self.status.button().setTitle_('')
                self.status.button().setImage_(icon)
                self.web.evaluateJavaScript_completionHandler_('window.Observer.receive(' + json.dumps(wire_value(value), ensure_ascii=True) + ')', None)
            if probe and not self.probed:
                self.probed = True
                # Native-vs-WebKit feasibility evidence, not a substitute for UI interaction.
                native = NSStackView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 600))
                native.addArrangedSubview_(NSTextField.wrappingLabelWithString_('Native multiline 태스크'))
                scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 600))
                scroll.setDocumentView_(native)
                self.native_probe = (native, scroll)
                def done(result, error):
                    Path(probe).write_text(json.dumps({'native_stack': native is not None,
                        'native_scroll': scroll is not None, 'web_result': result,
                        'web_error': str(error) if error else None,
                        'popover_shown': bool(self.popover.isShown()), 'size': [420, 600]}, ensure_ascii=False))
                self.web.evaluateJavaScript_completionHandler_('JSON.stringify({ready:!!window.Observer,body:document.body.innerText})', done)

    delegate = PopoverDelegate.alloc().init()
    delegate.ready, delegate.probed, delegate.previous = False, False, None
    delegate.pending_show = show
    config = WKWebViewConfiguration.alloc().init()
    config.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
    config.userContentController().addScriptMessageHandler_name_(delegate, 'observer')
    delegate.web = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 420, 600), config)
    delegate.web.setNavigationDelegate_(delegate)
    delegate.web.setAccessibilityLabel_('Task Observer')
    view_controller = NSViewController.alloc().init()
    view_controller.setView_(delegate.web)
    delegate.popover = NSPopover.alloc().init()
    delegate.popover.setContentViewController_(view_controller)
    delegate.popover.setContentSize_(NSMakeSize(420, 600))
    delegate.popover.setBehavior_(NSPopoverBehaviorTransient)
    delegate.popover.setAnimates_(False)
    delegate.status = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    delegate.status.button().setTitle_('◉')
    delegate.status.button().setAccessibilityLabel_('Task Observer')
    delegate.status.button().setTarget_(delegate)
    delegate.status.button().setAction_('toggle:')
    delegate.web.loadFileURL_allowingReadAccessToURL_(asset_url, NSURL.fileURLWithPath_(str(ASSETS)))
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.25, delegate, 'tick:', None, True)
    handlers = {s: signal.signal(s, lambda *_: stopped.set()) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        if controller.runtime:
            controller.runtime.start()
        controller.start()
        app.run()
    finally:
        timer.invalidate()
        controller.stop()
        if controller.runtime:
            controller.runtime.stop()
        config.userContentController().removeScriptMessageHandlerForName_('observer')
        NSStatusBar.systemStatusBar().removeStatusItem_(delegate.status)
        for s, handler in handlers.items():
            signal.signal(s, handler)
    return 0


def main(argv=None):
    from .app import DEFAULT_CONFIG, load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--fixture', type=Path, help='synthetic input only; no collector or state writes')
    parser.add_argument('--show', action='store_true', help='open the popover at launch')
    parser.add_argument('--probe', type=Path, help='write optional local native feasibility evidence')
    parser.add_argument('--snapshot', action='store_true', help='project data without the native UI')
    args = parser.parse_args(argv)
    try:
        if args.fixture:
            controller = ProjectionController(fixture=args.fixture)
        else:
            config = load_config(args.config)
            root = args.root.expanduser().absolute()
            if root.resolve().is_relative_to(Path(config['source']).resolve().parent):
                raise ValueError('보고 상태 폴더는 Devin 원본 폴더 밖에 있어야 합니다.')
            runtime = None if args.snapshot or service_running(config) else CollectorRuntime(config)
            controller = ProjectionController(root, config, args.config, runtime)
        if args.snapshot:
            print(json.dumps(wire_value(controller.collect()), ensure_ascii=False))
            return 0
        return run_native(controller, show=args.show, probe=args.probe)
    except (ValueError, OSError, StateError, ImportError) as error:
        print('Observer 안내: ' + str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
