//! migaki-gui: the omarchy-styled Rust frontend for migaki.
//!
//! The binary is a thin shell around `app::MainView`: the window, the CLI
//! flags (`--version`, `--self-test`, `--diagnose`) and nothing else. Every
//! backend call goes through bridge/main.py over NDJSON.
mod app;
mod bridge;
mod root;
mod theme;
mod ui;
mod views;

use std::path::PathBuf;
use std::process::Command;

use gpui::{App, AppContext, Application, Bounds, WindowBounds, WindowOptions, px, size};
use serde_json::json;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|arg| arg == "--help" || arg == "-h") {
        usage();
        return;
    }
    if args.iter().any(|arg| arg == "--version" || arg == "-V") {
        println!("migaki-gui {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if args.iter().any(|arg| arg == "--self-test") {
        std::process::exit(self_test());
    }
    if args.iter().any(|arg| arg == "--diagnose") {
        std::process::exit(diagnose());
    }
    run_gui();
}

fn usage() {
    println!("migaki-gui {}", env!("CARGO_PKG_VERSION"));
    println!();
    println!("USAGE");
    println!("  migaki-gui [--version] [--self-test] [--diagnose]");
    println!();
    println!("  (no arguments)   open the window");
    println!("  --version        print the version and exit");
    println!("  --self-test      spawn the bridge, run `init`, print the snapshot");
    println!("  --diagnose       print root/python/theme facts, then --self-test");
}

fn checkout() -> Option<PathBuf> {
    match root::find() {
        Ok(path) => Some(path),
        Err(err) => {
            eprintln!("migaki-gui: {err}");
            None
        }
    }
}

fn self_test() -> i32 {
    let Some(root) = checkout() else {
        return 1;
    };
    let bridge = match bridge::Bridge::spawn(&root) {
        Ok(bridge) => bridge,
        Err(err) => {
            eprintln!("self-test FAILED: {err}");
            return 1;
        }
    };
    let reply = bridge
        .request("init", json!({ "ui_pid": std::process::id() }))
        .recv_blocking();
    match reply {
        Ok(Ok(value)) => {
            let text =
                serde_json::to_string_pretty(&value).unwrap_or_else(|_| value.to_string());
            println!("{text}");
            println!("self-test: OK");
            0
        }
        Ok(Err(err)) => {
            eprintln!("self-test FAILED: {err}");
            1
        }
        Err(_) => {
            eprintln!("self-test FAILED: the bridge channel closed");
            1
        }
    }
}

fn diagnose() -> i32 {
    println!("migaki-gui {}", env!("CARGO_PKG_VERSION"));
    let Some(root) = checkout() else {
        return 1;
    };
    println!("root: {}", root.display());
    let script = root.join("bridge/main.py");
    println!(
        "bridge: {} ({})",
        script.display(),
        if script.is_file() { "present" } else { "MISSING" }
    );
    match Command::new("python3").arg("--version").output() {
        Ok(out) => println!("python3: {}", String::from_utf8_lossy(&out.stdout).trim()),
        Err(err) => println!("python3: unavailable ({err})"),
    }
    println!("theme dir: {}", theme::theme_dir().display());
    println!("bridge log: {}", bridge::log_path().display());
    self_test()
}

fn run_gui() {
    if let Err(err) = root::find() {
        eprintln!("migaki-gui: {err}");
    }
    Application::new().run(|cx: &mut App| {
        let bounds = Bounds::centered(None, size(px(1100.), px(700.)), cx);
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            app_id: Some("migaki".to_string()),
            ..Default::default()
        };
        let opened = cx.open_window(options, |window, cx| {
            window.set_window_title("Migaki");
            cx.new(|cx| app::MainView::new(window, cx))
        });
        if let Err(err) = opened {
            eprintln!("migaki-gui: could not open the window: {err}");
            cx.quit();
            return;
        }
        cx.activate(true);
    });
}
