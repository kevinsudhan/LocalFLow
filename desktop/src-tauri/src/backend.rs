//! Supervises the Python backend process and speaks JSON-RPC to it.
//!
//! The backend is a child process bound to `127.0.0.1` on an ephemeral port with
//! a per-launch token; it prints `LOCALFLOW_READY {json}` on stdout once it is
//! listening. Nothing here ever binds or connects to a non-loopback address.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{mpsc, oneshot};
use tokio_tungstenite::tungstenite::Message;

/// Generous by default because a first model load legitimately takes minutes.
const CALL_TIMEOUT: Duration = Duration::from_secs(120);

/// Calls the user is actively waiting on, with the HUD frozen on screen. A
/// two-minute wait here is indistinguishable from a hang, so these fail fast
/// and let the interface recover instead.
const INTERACTIVE_TIMEOUT: Duration = Duration::from_secs(30);
const READY_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Debug, Clone, Deserialize)]
struct ReadyInfo {
    port: u16,
    #[serde(default)]
    host: String,
    token: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct BackendError {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub detail: String,
}

impl std::fmt::Display for BackendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for BackendError {}

/// Emitted by the backend; forwarded to the webview and the orchestrator.
pub type EventHandler = Arc<dyn Fn(String, Value) + Send + Sync>;

pub struct Backend {
    outbound: Mutex<Option<mpsc::UnboundedSender<Message>>>,
    pending: Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, BackendError>>>>>,
    next_id: AtomicU64,
    connected: Arc<AtomicBool>,
    child: Mutex<Option<Child>>,
    #[cfg(windows)]
    job: Mutex<Option<job::JobHandle>>,
    handler: Mutex<Option<EventHandler>>,
    pub last_error: Mutex<Option<String>>,
}

impl Backend {
    pub fn new() -> Self {
        Self {
            outbound: Mutex::new(None),
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            connected: Arc::new(AtomicBool::new(false)),
            child: Mutex::new(None),
            #[cfg(windows)]
            job: Mutex::new(None),
            handler: Mutex::new(None),
            last_error: Mutex::new(None),
        }
    }

    pub fn set_handler(&self, handler: EventHandler) {
        *self.handler.lock() = Some(handler);
    }

    pub fn is_connected(&self) -> bool {
        self.connected.load(Ordering::Relaxed)
    }

    fn emit(&self, event: &str, payload: Value) {
        let handler = self.handler.lock().clone();
        if let Some(handler) = handler {
            handler(event.to_string(), payload);
        }
    }

    /// Launch the Python backend and connect to it.
    pub async fn start(self: &Arc<Self>, python: PathBuf, backend_dir: PathBuf) -> Result<()> {
        let token = random_token();
        log::info!("Starting backend: {} (cwd {})", python.display(), backend_dir.display());

        let mut command = Command::new(&python);
        command
            .arg("-u")
            .arg("-m")
            .arg("localflow")
            .arg("--port")
            .arg("0")
            .arg("--token")
            .arg(&token)
            .current_dir(&backend_dir)
            .env("PYTHONPATH", &backend_dir)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null());

        #[cfg(windows)]
        {
            // CREATE_NO_WINDOW: never flash a console for a background service.
            command.creation_flags(0x0800_0000);
        }

        let mut child = command
            .spawn()
            .with_context(|| format!("could not start {}", python.display()))?;

        // On Windows a venv's `Scripts\python.exe` is a launcher stub that
        // re-executes the base interpreter as a *child* of itself, so the
        // process we spawned is not the process serving RPC. Killing only the
        // handle we hold orphans the real backend, which keeps the microphone
        // open and fights the next launch for it. A job object makes the kill
        // cover the whole tree, and kills it too if we crash outright.
        #[cfg(windows)]
        if let Some(pid) = child.id() {
            match job::assign(pid) {
                Ok(handle) => *self.job.lock() = Some(handle),
                Err(err) => log::warn!("could not put the backend in a job object: {err}"),
            }
        }

        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
        let stderr = child.stderr.take().ok_or_else(|| anyhow!("no stderr"))?;

        // Backend logs go to stderr; mirror them into our own log.
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if line.contains("ERROR") || line.contains("CRITICAL") {
                    log::error!("[backend] {line}");
                } else {
                    log::debug!("[backend] {line}");
                }
            }
        });

        let (ready_tx, ready_rx) = oneshot::channel::<ReadyInfo>();
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            let mut ready_tx = Some(ready_tx);
            while let Ok(Some(line)) = lines.next_line().await {
                if let Some(rest) = line.strip_prefix("LOCALFLOW_READY ") {
                    match serde_json::from_str::<ReadyInfo>(rest) {
                        Ok(info) => {
                            if let Some(tx) = ready_tx.take() {
                                let _ = tx.send(info);
                            }
                        }
                        Err(err) => log::error!("bad ready line: {err}"),
                    }
                } else {
                    log::info!("[backend] {line}");
                }
            }
        });

        *self.child.lock() = Some(child);

        let info = tokio::time::timeout(READY_TIMEOUT, ready_rx)
            .await
            .map_err(|_| anyhow!("the backend did not start within 45 seconds"))?
            .map_err(|_| anyhow!("the backend exited before it was ready"))?;

        self.connect(info).await
    }

    async fn connect(self: &Arc<Self>, info: ReadyInfo) -> Result<()> {
        let host = if info.host.is_empty() { "127.0.0.1".to_string() } else { info.host };
        if host != "127.0.0.1" && host != "localhost" {
            bail!("refusing to connect to a non-loopback backend at {host}");
        }
        let url = format!("ws://{host}:{}", info.port);
        let (stream, _) = tokio_tungstenite::connect_async(&url)
            .await
            .with_context(|| format!("could not connect to {url}"))?;
        let (mut writer, mut reader) = stream.split();

        writer
            .send(Message::Text(json!({ "token": info.token }).to_string()))
            .await
            .context("authentication failed")?;

        let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
        *self.outbound.lock() = Some(tx);
        self.connected.store(true, Ordering::Relaxed);

        tauri::async_runtime::spawn(async move {
            while let Some(message) = rx.recv().await {
                if writer.send(message).await.is_err() {
                    break;
                }
            }
        });

        let this = Arc::clone(self);
        tauri::async_runtime::spawn(async move {
            while let Some(Ok(message)) = reader.next().await {
                if let Message::Text(text) = message {
                    this.on_message(&text);
                }
            }
            this.connected.store(false, Ordering::Relaxed);
            this.fail_all_pending("The backend disconnected.");
            this.emit("backend:disconnected", json!({}));
            log::warn!("Backend connection closed");
        });

        log::info!("Connected to backend on {url}");
        Ok(())
    }

    fn on_message(&self, text: &str) {
        let value: Value = match serde_json::from_str(text) {
            Ok(value) => value,
            Err(err) => {
                log::warn!("bad message from backend: {err}");
                return;
            }
        };

        if let Some(event) = value.get("event").and_then(Value::as_str) {
            let data = value.get("data").cloned().unwrap_or(Value::Null);
            self.emit(event, data);
            return;
        }

        let Some(id) = value.get("id").and_then(Value::as_u64) else {
            return;
        };
        let sender = self.pending.lock().remove(&id);
        let Some(sender) = sender else { return };

        if value.get("ok").and_then(Value::as_bool).unwrap_or(false) {
            let _ = sender.send(Ok(value.get("result").cloned().unwrap_or(Value::Null)));
        } else {
            let err = value.get("error").cloned().unwrap_or(Value::Null);
            let _ = sender.send(Err(BackendError {
                code: err.get("code").and_then(Value::as_str).unwrap_or("error").into(),
                message: err
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("The request failed.")
                    .into(),
                detail: err.get("detail").and_then(Value::as_str).unwrap_or("").into(),
            }));
        }
    }

    fn fail_all_pending(&self, message: &str) {
        let mut pending = self.pending.lock();
        for (_, sender) in pending.drain() {
            let _ = sender.send(Err(BackendError {
                code: "backend_disconnected".into(),
                message: message.into(),
                detail: String::new(),
            }));
        }
    }

    /// Call a backend method and await the response.
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, BackendError> {
        let timeout = if INTERACTIVE_METHODS.contains(&method) {
            INTERACTIVE_TIMEOUT
        } else {
            CALL_TIMEOUT
        };
        self.call_with_timeout(method, params, timeout).await
    }

    async fn call_with_timeout(
        &self,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> Result<Value, BackendError> {
        let sender = {
            let guard = self.outbound.lock();
            guard.clone()
        };
        let Some(sender) = sender else {
            return Err(BackendError {
                code: "backend_unavailable".into(),
                message: "LocalFlow's speech engine isn't running.".into(),
                detail: self.last_error.lock().clone().unwrap_or_default(),
            });
        };

        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().insert(id, tx);

        let payload = json!({ "id": id, "method": method, "params": params });
        if sender.send(Message::Text(payload.to_string())).is_err() {
            self.pending.lock().remove(&id);
            return Err(BackendError {
                code: "backend_unavailable".into(),
                message: "Lost the connection to the speech engine.".into(),
                detail: String::new(),
            });
        }

        match tokio::time::timeout(timeout, rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err(BackendError {
                code: "backend_cancelled".into(),
                message: "The request was cancelled.".into(),
                detail: String::new(),
            }),
            Err(_) => {
                self.pending.lock().remove(&id);
                Err(BackendError {
                    code: "backend_timeout".into(),
                    message: format!(
                        "'{method}' did not respond within {}s.",
                        timeout.as_secs()
                    ),
                    detail: String::new(),
                })
            }
        }
    }

    pub async fn shutdown(&self) {
        *self.outbound.lock() = None;
        self.connected.store(false, Ordering::Relaxed);
        let child = self.child.lock().take();
        if let Some(mut child) = child {
            let _ = child.start_kill();
            let _ = tokio::time::timeout(Duration::from_secs(3), child.wait()).await;
        }
        // Closing the job terminates anything the stub launched and survived.
        #[cfg(windows)]
        {
            let _ = self.job.lock().take();
        }
    }
}

/// Methods that block the dictation flow, and therefore the user.
const INTERACTIVE_METHODS: [&str; 4] =
    ["session.start", "session.stop", "session.cancel", "process.text"];

fn random_token() -> String {
    use rand::Rng;
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let mut rng = rand::thread_rng();
    (0..32).map(|_| CHARS[rng.gen_range(0..CHARS.len())] as char).collect()
}

/// Where the Python interpreter and backend sources live.
pub struct BackendPaths {
    pub python: PathBuf,
    pub backend_dir: PathBuf,
}

/// Resolve the interpreter, preferring a bundled runtime over a dev venv.
pub fn resolve_paths(resource_dir: Option<PathBuf>) -> Result<BackendPaths> {
    let mut candidates: Vec<(PathBuf, PathBuf)> = Vec::new();

    if let Ok(explicit) = std::env::var("LOCALFLOW_PYTHON") {
        let python = PathBuf::from(explicit);
        let backend = std::env::var("LOCALFLOW_BACKEND_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| python.parent().map(Path::to_path_buf).unwrap_or_default());
        candidates.push((python, backend));
    }

    // 1. Runtime shipped next to the installed application.
    if let Some(resources) = resource_dir.clone() {
        let backend = resources.join("backend");
        for rel in ["python-runtime/python.exe", "runtime/python.exe"] {
            candidates.push((resources.join(rel), backend.clone()));
        }
    }

    // 2. Runtime installed into the user's data directory by the setup script.
    if let Some(data) = dirs::data_dir() {
        let root = data.join("LocalFlow").join("runtime");
        let backend = resource_dir
            .clone()
            .map(|r| r.join("backend"))
            .unwrap_or_else(|| root.join("backend"));
        candidates.push((root.join("python.exe"), backend));
    }

    // 3. Development checkout.
    if let Ok(cwd) = std::env::current_dir() {
        for base in [cwd.clone(), cwd.join(".."), cwd.join("..").join("..")] {
            let venv = base.join(".venv").join("Scripts").join("python.exe");
            candidates.push((venv, base.join("backend")));
        }
    }

    for (python, backend_dir) in candidates {
        if python.is_file() && backend_dir.join("localflow").is_dir() {
            return Ok(BackendPaths {
                python: python.canonicalize().unwrap_or(python),
                backend_dir: backend_dir.canonicalize().unwrap_or(backend_dir),
            });
        }
    }

    bail!(
        "Could not find LocalFlow's Python runtime. Run scripts/setup.ps1 to create it, \
         or set LOCALFLOW_PYTHON to a Python 3.11+ interpreter that has the backend \
         requirements installed."
    )
}

#[cfg(windows)]
mod job {
    //! A Windows job object that terminates the whole backend process tree.

    use anyhow::{bail, Result};
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
        JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

    /// Owns the job handle; dropping it kills every process still inside.
    pub struct JobHandle(HANDLE);

    unsafe impl Send for JobHandle {}
    unsafe impl Sync for JobHandle {}

    impl Drop for JobHandle {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    pub fn assign(pid: u32) -> Result<JobHandle> {
        unsafe {
            let job = CreateJobObjectW(None, None)?;
            if job.is_invalid() {
                bail!("CreateJobObjectW returned an invalid handle");
            }
            let handle = JobHandle(job);

            let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )?;

            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, false, pid)?;
            let result = AssignProcessToJobObject(job, process);
            let _ = CloseHandle(process);
            result?;
            Ok(handle)
        }
    }
}
