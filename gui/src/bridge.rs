//! Bridge child process and NDJSON codec.
//!
//! The Python bridge (bridge/main.py) speaks one JSON object per line on
//! stdin/stdout; stderr is appended to ~/.cache/migaki/gui.log so a crash
//! there is diagnosable. Requests are correlated by id, everything else is an
//! event pushed to the shared channel.
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{Value, json};

pub type Reply = Result<Value, String>;

type Pending = Arc<Mutex<HashMap<u64, async_channel::Sender<Reply>>>>;

pub struct Bridge {
    child: Child,
    stdin: Mutex<ChildStdin>,
    next_id: AtomicU64,
    pending: Pending,
    pub events: async_channel::Receiver<Value>,
}

impl Bridge {
    /// Spawn `python3 -u bridge/main.py` from the checkout root.
    pub fn spawn(root: &Path) -> anyhow::Result<Bridge> {
        let log_path = log_path();
        if let Some(parent) = log_path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let stderr = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)?;
        let script = root.join("bridge/main.py");
        if !script.is_file() {
            anyhow::bail!("bridge/main.py is missing at {}", script.display());
        }
        let mut child = Command::new("python3")
            .arg("-u")
            .arg(&script)
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr))
            .spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| anyhow::anyhow!("bridge stdin unavailable"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| anyhow::anyhow!("bridge stdout unavailable"))?;

        let (event_tx, event_rx) = async_channel::unbounded();
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let reader_pending = pending.clone();
        std::thread::Builder::new()
            .name("bridge-reader".into())
            .spawn(move || {
                let mut lines = BufReader::new(stdout).lines();
                loop {
                    let line = match lines.next() {
                        Some(Ok(line)) => line,
                        Some(Err(err)) => {
                            let _ = event_tx.send_blocking(json!({
                                "event": "bridge_down",
                                "detail": err.to_string(),
                            }));
                            break;
                        }
                        None => {
                            let _ = event_tx.send_blocking(json!({
                                "event": "bridge_down",
                                "detail": "bridge exited",
                            }));
                            break;
                        }
                    };
                    if line.trim().is_empty() {
                        continue;
                    }
                    let Ok(value) = serde_json::from_str::<Value>(&line) else {
                        continue;
                    };
                    let id = value.get("id").and_then(Value::as_u64);
                    match id {
                        Some(id) => {
                            let waiter = reader_pending.lock().ok().and_then(|mut p| p.remove(&id));
                            if let Some(waiter) = waiter {
                                let reply = if value.get("ok").and_then(Value::as_bool) == Some(true) {
                                    Ok(value.get("result").cloned().unwrap_or(Value::Null))
                                } else {
                                    Err(value
                                        .get("error")
                                        .and_then(Value::as_str)
                                        .unwrap_or("bridge error")
                                        .to_string())
                                };
                                let _ = waiter.send_blocking(reply);
                            }
                        }
                        None => {
                            let _ = event_tx.send_blocking(value);
                        }
                    }
                }
            })?;

        Ok(Bridge {
            child,
            stdin: Mutex::new(stdin),
            next_id: AtomicU64::new(1),
            pending,
            events: event_rx,
        })
    }

    /// Send one request; await the reply with `.recv().await`.
    pub fn request(&self, method: &str, params: Value) -> async_channel::Receiver<Reply> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = async_channel::bounded(1);
        if let Ok(mut pending) = self.pending.lock() {
            pending.insert(id, tx);
        }
        let payload = json!({"id": id, "method": method, "params": params});
        let wrote = (|| -> std::io::Result<()> {
            let mut stdin = self
                .stdin
                .lock()
                .map_err(|_| std::io::Error::other("bridge stdin poisoned"))?;
            writeln!(stdin, "{payload}")?;
            stdin.flush()
        })();
        if let Err(err) = wrote {
            if let Ok(mut pending) = self.pending.lock() {
                pending.remove(&id);
            }
            let (tx, rx2) = async_channel::bounded(1);
            let _ = tx.send_blocking(Err(format!("bridge write failed: {err}")));
            return rx2;
        }
        rx
    }

}

impl Drop for Bridge {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub fn log_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/".into());
    PathBuf::from(home).join(".cache/migaki/gui.log")
}
