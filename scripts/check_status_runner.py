"""Run the patch's worker tests without compiling the upstream TUI test suite.

The real TUI is separately compiled and exercised by verify_embedded_codex.py.
Only the event channel/ThreadId are replaced here; the worker source and its
own tests are imported verbatim from the patched upstream source tree.
"""
import argparse
from pathlib import Path
import re
import subprocess


def run(source, target):
    source = Path(source).resolve()
    target = Path(target).resolve()
    workspace = source / 'codex-rs'
    harness = target.parent / 'status-runner-tests'
    (harness / 'src').mkdir(parents=True, exist_ok=True)
    lock = (workspace / 'Cargo.lock').read_text()
    def version(name):
        match = re.search(r'\[\[package\]\]\nname = "' + re.escape(name) + r'"\nversion = "([^"]+)"', lock)
        if not match:
            raise ValueError('missing locked test dependency: ' + name)
        return match.group(1)
    (harness / 'Cargo.toml').write_text('''[package]
name = "codex-status-runner-tests"
version = "0.0.0"
edition = "2024"
[workspace]
[dependencies]
tokio = { version = "=''' + version('tokio') + '''", features = ["full"] }
tempfile = "=''' + version('tempfile') + '''"
[profile.release]
lto = false
debug = 0
codegen-units = 4
''')
    worker = workspace / 'tui/src/external_status_line.rs'
    # include-by-path preserves the module docs and cfg(test) blocks unchanged.
    import json
    (harness / 'src/lib.rs').write_text('''extern crate self as codex_protocol;
pub type ThreadId = u128;
mod app_event {
    #[derive(Debug)]
    pub enum AppEvent { ExternalStatusLineUpdated { thread_id: Option<crate::ThreadId>, line: Option<String> } }
}
mod app_event_sender {
    #[derive(Clone, Debug)]
    pub struct AppEventSender(tokio::sync::mpsc::UnboundedSender<crate::app_event::AppEvent>);
    impl AppEventSender {
        pub fn new(tx: tokio::sync::mpsc::UnboundedSender<crate::app_event::AppEvent>) -> Self { Self(tx) }
        pub fn send(&self, event: crate::app_event::AppEvent) { let _ = self.0.send(event); }
    }
}
#[path = ''' + json.dumps(str(worker)) + ''']
mod external_status_line;
''')
    subprocess.run(['cargo', 'test', '--release', '--manifest-path', str(harness / 'Cargo.toml'),
                    '--target-dir', str(target), 'external_status_'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    run(args.source, args.target)
