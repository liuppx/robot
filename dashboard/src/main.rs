use axum::{
    extract::{Path, Query, State},
    http::{header::SET_COOKIE, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, patch, post},
    Json, Router,
};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{HashMap, VecDeque},
    env,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    net::{SocketAddr, TcpListener},
    path::{Path as FsPath, PathBuf},
    process::Command,
    sync::Arc,
    thread,
    time::Duration as StdDuration,
};
use tokio::sync::RwLock;
use tower_http::services::{ServeDir, ServeFile};
use tracing::{error, info, warn};
use uuid::Uuid;

#[derive(Clone)]
struct AppState {
    cfg: StaticConfig,
    db: Arc<RwLock<DbState>>,
    sessions: Arc<RwLock<HashMap<String, SessionRecord>>>,
    heal_marks: Arc<RwLock<HashMap<String, DateTime<Utc>>>>,
    http: reqwest::Client,
}

#[derive(Clone)]
struct StaticConfig {
    bind_addr: String,
    repo_root: String,
    runtime_dir: String,
    instances_root: String,
    state_file: String,
    router_base_url: String,
    router_api_key: Option<String>,
    default_model: String,
    admin_token: String,
    internal_token: String,
    model_allowlist: Vec<String>,
    session_ttl_seconds: i64,
    port_range_start: u16,
    port_range_end: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct DbState {
    default_model: String,
    instances: HashMap<String, BotInstance>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct BotInstance {
    id: String,
    kind: String,
    name: String,
    profile: String,
    model: String,
    status: String,
    owner_wallet: String,
    created_at: String,
    updated_at: String,
    port: u16,
    pid: Option<u32>,
    root_dir: String,
    logs_dir: String,
    last_error: Option<String>,
    dingtalk_client_id: Option<String>,
    dingtalk_client_secret: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionRecord {
    wallet_id: String,
    chain_id: Option<String>,
    created_at: String,
    expires_at: String,
    ucan_session: Option<Value>,
    ucan_signature: Option<Value>,
}

#[derive(Debug, Serialize)]
struct ApiResponse<T: Serialize> {
    ok: bool,
    data: T,
}

#[derive(Debug, Serialize)]
struct ApiError {
    ok: bool,
    error: String,
}

#[derive(Debug, Deserialize)]
struct WalletConnectRequest {
    wallet_id: String,
    chain_id: Option<String>,
    expires_at: Option<String>,
    ucan_session: Option<Value>,
    ucan_signature: Option<Value>,
}

#[derive(Debug, Serialize)]
struct AuthMeResponse {
    wallet_id: String,
    chain_id: Option<String>,
    expires_at: String,
}

#[derive(Debug, Deserialize)]
struct CreateInstanceRequest {
    kind: String,
    name: String,
    model: Option<String>,
    template: Option<String>,
    dingtalk_client_id: Option<String>,
    dingtalk_client_secret: Option<String>,
}

#[derive(Debug, Deserialize)]
struct UpdateModelRequest {
    model: String,
}

#[derive(Debug, Deserialize)]
struct LogsQuery {
    lines: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct DiagnoseQuery {
    auto_recover: Option<bool>,
}

#[derive(Debug, Serialize)]
struct InstanceView {
    id: String,
    kind: String,
    name: String,
    profile: String,
    model: String,
    status: String,
    owner_wallet: String,
    created_at: String,
    updated_at: String,
    port: u16,
    pid: Option<u32>,
    root_dir: String,
    logs_dir: String,
    last_error: Option<String>,
    dingtalk_client_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct InstanceLogsResponse {
    id: String,
    gateway_log: String,
    pair_log: String,
    pair_qr_ascii: String,
    pair_status: String,
    pair_hint: Option<String>,
    gateway_log_path: String,
    pair_log_path: String,
    events_log_path: String,
    events_log: String,
}

#[derive(Debug, Serialize)]
struct EventLogEntry {
    ts: String,
    event: String,
    detail: Value,
}

#[derive(Debug, Serialize)]
struct InstanceDiagnoseResponse {
    id: String,
    profile: String,
    kind: String,
    status: String,
    port: u16,
    pid: Option<u32>,
    gateway_target: String,
    gateway_reachable: bool,
    pair_status: String,
    pair_hint: Option<String>,
    whatsapp_running: Option<bool>,
    whatsapp_connected: Option<bool>,
    whatsapp_last_error: Option<String>,
    last_inbound_at: Option<i64>,
    last_outbound_at: Option<i64>,
    transport_established: bool,
    router_api_key_present: bool,
    no_api_key_error_seen: bool,
    recommended_action: Option<String>,
    auto_recover_triggered: bool,
    auto_recover_message: Option<String>,
    evidence: Vec<String>,
}

#[derive(Debug, Serialize)]
struct TraderSummaryResponse {
    available: bool,
    broker: String,
    running: bool,
    pid: Option<u32>,
    runtime_dir: String,
    strategy_file: String,
    state_file: String,
    service_log_path: String,
    strategies: Vec<Value>,
    state: Value,
    recent_signals: Vec<Value>,
    recent_orders: Vec<Value>,
    service_log_tail: String,
}

#[derive(Debug, Deserialize)]
struct TraderConfigUpdateRequest {
    broker: String,
    strategy: Value,
}

#[tokio::main]
async fn main() {
    init_tracing();

    let cfg = load_static_config();
    if let Err(err) = ensure_dirs(&cfg) {
        error!("failed to ensure directories: {err}");
        return;
    }

    let db_state = load_db_state(&cfg).unwrap_or_else(|err| {
        warn!("load state failed, fallback to empty state: {err}");
        DbState {
            default_model: cfg.default_model.clone(),
            instances: HashMap::new(),
        }
    });

    let app_state = AppState {
        cfg: cfg.clone(),
        db: Arc::new(RwLock::new(db_state)),
        sessions: Arc::new(RwLock::new(HashMap::new())),
        heal_marks: Arc::new(RwLock::new(HashMap::new())),
        http: reqwest::Client::new(),
    };

    let heal_state = app_state.clone();
    tokio::spawn(async move {
        auto_recover_loop(heal_state).await;
    });

    let api_router = Router::new()
        .route("/api/v1/public/health", get(public_health))
        .route("/api/v1/public/version", get(public_version))
        .route("/api/v1/public/auth/me", get(public_auth_me))
        .route(
            "/api/v1/public/auth/wallet/connect",
            post(public_auth_wallet_connect),
        )
        .route("/api/v1/public/auth/logout", post(public_auth_logout))
        .route("/api/v1/public/bot/types", get(public_bot_types))
        .route("/api/v1/public/router/models", get(public_router_models))
        .route(
            "/api/v1/public/bot/instances",
            get(public_list_instances).post(public_create_instance),
        )
        .route(
            "/api/v1/public/bot/instances/{id}",
            get(public_get_instance).delete(public_delete_instance),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/model",
            patch(public_patch_instance_model),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/start",
            post(public_start_instance),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/stop",
            post(public_stop_instance),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/pair-whatsapp",
            post(public_pair_whatsapp),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/logs",
            get(public_instance_logs),
        )
        .route(
            "/api/v1/public/bot/instances/{id}/diagnose",
            get(public_diagnose_instance),
        )
        .route("/api/v1/public/trader/summary", get(public_trader_summary))
        .route("/api/v1/public/trader/config", get(public_trader_summary).put(public_trader_config_update))
        .route("/api/v1/public/trader/run-once", post(public_trader_run_once))
        .route("/api/v1/public/trader/start", post(public_trader_start))
        .route("/api/v1/public/trader/stop", post(public_trader_stop))
        .route(
            "/api/v1/admin/router/default-model",
            patch(admin_patch_default_model),
        )
        .route("/api/v1/admin/runtime/summary", get(admin_runtime_summary))
        .route(
            "/api/v1/internal/runtime/health/probe",
            post(internal_runtime_probe),
        );

    let web_dir = format!("{}/dashboard/web", cfg.repo_root);
    let index_file = format!("{web_dir}/index.html");

    let app = Router::new()
        .merge(api_router)
        .fallback_service(
            ServeDir::new(&web_dir)
                .append_index_html_on_directories(true)
                .not_found_service(ServeFile::new(index_file)),
        )
        .with_state(app_state);

    let bind_addr: SocketAddr = match cfg.bind_addr.parse() {
        Ok(addr) => addr,
        Err(err) => {
            error!("invalid BOT_HUB_BIND_ADDR '{}': {err}", cfg.bind_addr);
            return;
        }
    };

    info!("bot-hub-control-plane listening on {bind_addr}");
    let listener = match tokio::net::TcpListener::bind(bind_addr).await {
        Ok(listener) => listener,
        Err(err) => {
            error!("bind failed on {bind_addr}: {err}");
            return;
        }
    };

    if let Err(err) = axum::serve(listener, app).await {
        error!("server exited with error: {err}");
    }
}

fn init_tracing() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "bot_hub_control_plane=info,axum=info".into()),
        )
        .init();
}

fn guess_repo_root() -> String {
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    if cwd.ends_with("dashboard") {
        return cwd
            .parent()
            .unwrap_or(&cwd)
            .to_string_lossy()
            .to_string();
    }
    cwd.to_string_lossy().to_string()
}

fn env_or_default(key: &str, default: impl Into<String>) -> String {
    env::var(key).unwrap_or_else(|_| default.into())
}

fn load_static_config() -> StaticConfig {
    let repo_root = env_or_default("BOT_HUB_REPO_ROOT", guess_repo_root());
    let runtime_dir = env_or_default(
        "BOT_HUB_RUNTIME_DIR",
        format!("{repo_root}/runtime/control-plane"),
    );
    let instances_root = env_or_default(
        "BOT_HUB_INSTANCES_ROOT",
        format!("{repo_root}/runtime/instances"),
    );

    StaticConfig {
        bind_addr: env_or_default("BOT_HUB_BIND_ADDR", "127.0.0.1:3900"),
        repo_root,
        runtime_dir: runtime_dir.clone(),
        instances_root,
        state_file: format!("{runtime_dir}/state.json"),
        router_base_url: env_or_default("ROUTER_BASE_URL", "https://test-router.yeying.pub/v1"),
        router_api_key: env::var("ROUTER_API_KEY")
            .ok()
            .filter(|v| !v.trim().is_empty()),
        default_model: env_or_default("BOT_HUB_DEFAULT_MODEL", "gpt-5.3-codex"),
        admin_token: env_or_default("BOT_HUB_ADMIN_TOKEN", "change-me-admin-token"),
        internal_token: env_or_default("BOT_HUB_INTERNAL_TOKEN", "change-me-internal-token"),
        model_allowlist: env::var("BOT_HUB_MODEL_ALLOWLIST")
            .ok()
            .map(|v| {
                v.split(',')
                    .map(|x| x.trim().to_string())
                    .filter(|x| !x.is_empty())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default(),
        session_ttl_seconds: env_or_default("BOT_HUB_SESSION_TTL_SECONDS", "86400")
            .parse::<i64>()
            .unwrap_or(86400),
        port_range_start: env_or_default("BOT_HUB_INSTANCE_PORT_START", "18800")
            .parse::<u16>()
            .unwrap_or(18800),
        port_range_end: env_or_default("BOT_HUB_INSTANCE_PORT_END", "18999")
            .parse::<u16>()
            .unwrap_or(18999),
    }
}

fn ensure_dirs(cfg: &StaticConfig) -> Result<(), String> {
    fs::create_dir_all(&cfg.runtime_dir).map_err(|e| format!("create runtime dir failed: {e}"))?;
    fs::create_dir_all(&cfg.instances_root)
        .map_err(|e| format!("create instances dir failed: {e}"))?;
    Ok(())
}

fn load_db_state(cfg: &StaticConfig) -> Result<DbState, String> {
    if !FsPath::new(&cfg.state_file).exists() {
        return Ok(DbState {
            default_model: cfg.default_model.clone(),
            instances: HashMap::new(),
        });
    }
    let raw = fs::read_to_string(&cfg.state_file)
        .map_err(|e| format!("read state file failed {}: {e}", cfg.state_file))?;
    serde_json::from_str(&raw).map_err(|e| format!("parse state file failed: {e}"))
}

fn read_env_like_file(path: &FsPath) -> HashMap<String, String> {
    let mut values = HashMap::new();
    let Ok(raw) = fs::read_to_string(path) else {
        return values;
    };
    for line in raw.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = trimmed.split_once('=') {
            values.insert(
                key.trim().to_string(),
                value.trim().trim_matches('"').trim_matches('\'').to_string(),
            );
        }
    }
    values
}

fn read_json_file_or_default(path: &FsPath) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .unwrap_or_else(|| json!({}))
}

fn read_last_jsonl(path: &FsPath, limit: usize) -> Vec<Value> {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(_) => return Vec::new(),
    };
    let mut queue: VecDeque<String> = VecDeque::with_capacity(limit.max(1));
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        if line.trim().is_empty() {
            continue;
        }
        if queue.len() == limit {
            queue.pop_front();
        }
        queue.push_back(line);
    }
    queue
        .into_iter()
        .filter_map(|line| serde_json::from_str::<Value>(&line).ok())
        .collect()
}

fn read_last_text_lines(path: &FsPath, limit: usize) -> String {
    let file = match fs::File::open(path) {
        Ok(file) => file,
        Err(_) => return String::new(),
    };
    let mut queue: VecDeque<String> = VecDeque::with_capacity(limit.max(1));
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        if queue.len() == limit {
            queue.pop_front();
        }
        queue.push_back(line);
    }
    queue.into_iter().collect::<Vec<_>>().join("\n")
}

fn read_trader_strategies(path: &FsPath) -> Vec<Value> {
    let raw = match fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(_) => return Vec::new(),
    };
    let parsed = match serde_yaml::from_str::<Value>(&raw) {
        Ok(value) => value,
        Err(_) => return Vec::new(),
    };
    parsed
        .get("strategies")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default()
}

fn write_trader_config(
    trader_root: &FsPath,
    broker: &str,
    strategies: &[Value],
) -> Result<(), String> {
    let (env_file, env_template) = trader_env_paths(trader_root);
    let mut env_values = if env_file.exists() {
        read_env_like_file(&env_file)
    } else {
        read_env_like_file(&env_template)
    };
    env_values.insert("TRADER_BROKER".to_string(), broker.to_string());

    let env_order = [
        "TRADER_BOT_NAME",
        "TRADER_BIND_MODE",
        "TRADER_LOOP_INTERVAL_SECONDS",
        "TRADER_LOG_LEVEL",
        "TRADER_RUNTIME_DIR",
        "TRADER_STRATEGY_FILE",
        "TRADER_BROKER",
        "IFIND_BASE_URL",
        "IFIND_ACCESS_TOKEN",
        "IFIND_REFRESH_TOKEN",
        "IFIND_REQUEST_TIMEOUT_MS",
        "TRADER_EASTMONEY_ACCOUNT_ID",
    ];
    let mut lines = Vec::new();
    for key in env_order {
        if let Some(value) = env_values.get(key) {
            lines.push(format!("{key}={value}"));
        }
    }
    for (key, value) in env_values.iter() {
        if env_order.contains(&key.as_str()) {
            continue;
        }
        lines.push(format!("{key}={value}"));
    }
    fs::write(&env_file, lines.join("\n") + "\n")
        .map_err(|e| format!("write trader env failed {}: {e}", env_file.display()))?;

    let strategy_file = trader_strategy_file(trader_root, &env_values);
    let yaml_value = json!({ "strategies": strategies });
    let yaml_raw = serde_yaml::to_string(&yaml_value)
        .map_err(|e| format!("serialize trader strategies failed: {e}"))?;
    fs::write(&strategy_file, yaml_raw)
        .map_err(|e| format!("write trader strategies failed {}: {e}", strategy_file.display()))?;
    Ok(())
}

fn trader_root(cfg: &StaticConfig) -> PathBuf {
    FsPath::new(&cfg.repo_root).join("robots/custom/trader")
}

fn trader_env_paths(root: &FsPath) -> (PathBuf, PathBuf) {
    (
        root.join("config/trader.env"),
        root.join("config/trader.env.template"),
    )
}

fn trader_env_values(root: &FsPath) -> HashMap<String, String> {
    let (env_file, env_template) = trader_env_paths(root);
    if env_file.exists() {
        read_env_like_file(&env_file)
    } else {
        read_env_like_file(&env_template)
    }
}

fn trader_runtime_dir(root: &FsPath, env_values: &HashMap<String, String>) -> PathBuf {
    let runtime_dir = env_values
        .get("TRADER_RUNTIME_DIR")
        .cloned()
        .unwrap_or_else(|| "runtime".to_string());
    if FsPath::new(&runtime_dir).is_absolute() {
        PathBuf::from(runtime_dir)
    } else {
        root.join(runtime_dir)
    }
}

fn trader_strategy_file(root: &FsPath, env_values: &HashMap<String, String>) -> PathBuf {
    let strategy_file = env_values
        .get("TRADER_STRATEGY_FILE")
        .cloned()
        .unwrap_or_else(|| "config/strategies.yaml".to_string());
    if FsPath::new(&strategy_file).is_absolute() {
        PathBuf::from(strategy_file)
    } else {
        root.join(strategy_file)
    }
}

fn trader_pid(runtime_dir: &FsPath) -> Option<u32> {
    let pid_file = runtime_dir.join("trader.pid");
    let raw = fs::read_to_string(pid_file).ok()?;
    raw.trim().parse::<u32>().ok()
}

fn is_process_running(pid: u32) -> bool {
    Command::new("bash")
        .arg("-lc")
        .arg(format!("ps -p {pid} -o pid="))
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

async fn persist_db(state: &AppState) -> Result<(), String> {
    let snapshot = state.db.read().await.clone();
    let serialized = serde_json::to_string_pretty(&snapshot)
        .map_err(|e| format!("serialize state failed: {e}"))?;
    fs::write(&state.cfg.state_file, serialized)
        .map_err(|e| format!("write state file failed {}: {e}", state.cfg.state_file))
}

fn now_rfc3339() -> String {
    Utc::now().to_rfc3339()
}

fn now_epoch_ms() -> i64 {
    Utc::now().timestamp_millis()
}

fn append_line(path: &str, line: &str) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("open file for append failed {path}: {e}"))?;
    writeln!(file, "{line}").map_err(|e| format!("append line failed {path}: {e}"))
}

fn append_log_banner(path: &str, banner: &str) {
    let _ = append_line(path, "");
    let _ = append_line(path, &format!("===== {banner} ====="));
}

fn write_instance_event(instance: &BotInstance, event: &str, detail: Value) {
    let event_file = format!("{}/events.jsonl", instance.logs_dir);
    let record = EventLogEntry {
        ts: now_rfc3339(),
        event: event.to_string(),
        detail: json!({
            "instance_id": instance.id.clone(),
            "profile": instance.profile.clone(),
            "kind": instance.kind.clone(),
            "epoch_ms": now_epoch_ms(),
            "data": detail,
        }),
    };
    match serde_json::to_string(&record) {
        Ok(raw) => {
            if let Err(e) = append_line(&event_file, &raw) {
                warn!("append events.jsonl failed for {}: {e}", instance.id);
            }
        }
        Err(e) => warn!("serialize events.jsonl failed for {}: {e}", instance.id),
    }
}

fn short_wallet(wallet: &str) -> String {
    if wallet.len() <= 12 {
        return wallet.to_string();
    }
    format!("{}...{}", &wallet[..6], &wallet[wallet.len() - 4..])
}

fn ok<T: Serialize>(payload: T) -> Response {
    (
        StatusCode::OK,
        Json(ApiResponse {
            ok: true,
            data: payload,
        }),
    )
        .into_response()
}

fn err(status: StatusCode, message: impl Into<String>) -> Response {
    (
        status,
        Json(ApiError {
            ok: false,
            error: message.into(),
        }),
    )
        .into_response()
}

fn parse_cookie(headers: &HeaderMap, key: &str) -> Option<String> {
    let cookie_header = headers.get("cookie")?.to_str().ok()?;
    for part in cookie_header.split(';') {
        let trimmed = part.trim();
        if let Some((k, v)) = trimmed.split_once('=') {
            if k == key {
                return Some(v.to_string());
            }
        }
    }
    None
}

async fn require_user(state: &AppState, headers: &HeaderMap) -> Result<SessionRecord, Response> {
    let token = parse_cookie(headers, "bot_hub_session")
        .ok_or_else(|| err(StatusCode::UNAUTHORIZED, "wallet login required"))?;

    let mut sessions = state.sessions.write().await;
    let session = sessions
        .get(&token)
        .cloned()
        .ok_or_else(|| err(StatusCode::UNAUTHORIZED, "session not found"))?;

    let exp = DateTime::parse_from_rfc3339(&session.expires_at)
        .map(|v| v.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now() - Duration::seconds(1));

    if exp <= Utc::now() {
        sessions.remove(&token);
        return Err(err(
            StatusCode::UNAUTHORIZED,
            "session expired, please reconnect wallet",
        ));
    }

    Ok(session)
}

fn normalize_kind(kind: &str) -> Option<&'static str> {
    match kind.trim().to_lowercase().as_str() {
        "whatsapp" => Some("whatsapp"),
        "dingtalk" => Some("dingtalk"),
        _ => None,
    }
}

const TEMPLATE_GENERIC: &str = "generic";
const TEMPLATE_ECOM_TOY: &str = "ecommerce-toy";

fn normalize_template(kind: &str, template: Option<&str>) -> &'static str {
    let raw = template.unwrap_or_default().trim().to_lowercase();

    if kind == "whatsapp" {
        if raw.is_empty() || raw == "auto" || raw == "ecommerce" || raw == "ecommerce-toy" {
            return TEMPLATE_ECOM_TOY;
        }
    }

    match raw.as_str() {
        "generic" => TEMPLATE_GENERIC,
        "ecommerce" | "ecommerce-toy" => TEMPLATE_ECOM_TOY,
        _ => {
            if kind == "whatsapp" {
                TEMPLATE_ECOM_TOY
            } else {
                TEMPLATE_GENERIC
            }
        }
    }
}

fn slugify(input: &str) -> String {
    let mut out = String::new();
    for ch in input.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if (ch == '-' || ch == '_' || ch == ' ') && !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_matches('-').to_string()
}

fn sh_quote(v: &str) -> String {
    format!("'{}'", v.replace('\'', "'\\''"))
}

fn openclaw_prefix() -> Option<String> {
    env::var("BOT_HUB_OPENCLAW_PREFIX")
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

fn build_openclaw_cmd(profile: &str, args: &str) -> String {
    let base = format!("openclaw --profile {} {}", profile, args);
    if let Some(prefix) = openclaw_prefix() {
        format!("{} {}", prefix, base)
    } else {
        base
    }
}

fn run_shell(cmd: &str) -> Result<String, String> {
    let output = Command::new("bash")
        .arg("-lc")
        .arg(cmd)
        .output()
        .map_err(|e| format!("spawn shell failed: {e}"))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        Err(format!(
            "command failed: {}\nstdout: {}\nstderr: {}",
            cmd, stdout, stderr
        ))
    }
}

fn run_shell_capture(cmd: &str) -> (bool, String, String) {
    match Command::new("bash").arg("-lc").arg(cmd).output() {
        Ok(output) => (
            output.status.success(),
            String::from_utf8_lossy(&output.stdout).to_string(),
            String::from_utf8_lossy(&output.stderr).to_string(),
        ),
        Err(e) => (false, String::new(), format!("spawn shell failed: {e}")),
    }
}

fn extract_first_json_value(text: &str) -> Option<Value> {
    let start = text.find('{')?;
    let end = text.rfind('}')?;
    if end <= start {
        return None;
    }
    serde_json::from_str::<Value>(&text[start..=end]).ok()
}

fn is_pid_alive(pid: u32) -> bool {
    FsPath::new(&format!("/proc/{pid}")).exists()
}

fn is_port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn allocate_port(cfg: &StaticConfig, db: &DbState) -> Option<u16> {
    for port in cfg.port_range_start..=cfg.port_range_end {
        let already_used = db.instances.values().any(|i| i.port == port);
        if already_used {
            continue;
        }
        if is_port_free(port) {
            return Some(port);
        }
    }
    None
}

fn to_instance_view(instance: &BotInstance) -> InstanceView {
    InstanceView {
        id: instance.id.clone(),
        kind: instance.kind.clone(),
        name: instance.name.clone(),
        profile: instance.profile.clone(),
        model: instance.model.clone(),
        status: instance.status.clone(),
        owner_wallet: short_wallet(&instance.owner_wallet),
        created_at: instance.created_at.clone(),
        updated_at: instance.updated_at.clone(),
        port: instance.port,
        pid: instance.pid,
        root_dir: instance.root_dir.clone(),
        logs_dir: instance.logs_dir.clone(),
        last_error: instance.last_error.clone(),
        dingtalk_client_id: instance.dingtalk_client_id.clone(),
    }
}

fn ensure_instance_dirs(instance: &BotInstance) -> Result<(), String> {
    let root = FsPath::new(&instance.root_dir);
    fs::create_dir_all(root.join("config"))
        .map_err(|e| format!("create config dir failed: {e}"))?;
    fs::create_dir_all(root.join("state")).map_err(|e| format!("create state dir failed: {e}"))?;
    fs::create_dir_all(root.join("workspace"))
        .map_err(|e| format!("create workspace dir failed: {e}"))?;
    fs::create_dir_all(root.join("logs")).map_err(|e| format!("create logs dir failed: {e}"))?;
    fs::create_dir_all(root.join("meta")).map_err(|e| format!("create meta dir failed: {e}"))?;
    Ok(())
}

fn archive_instance_paths(cfg: &StaticConfig, instance: &BotInstance) -> Result<Value, String> {
    let root_dir = FsPath::new(&instance.root_dir);
    let trash_root = FsPath::new(&cfg.runtime_dir).join("trash");
    fs::create_dir_all(&trash_root).map_err(|e| format!("create trash dir failed: {e}"))?;

    if !instance.root_dir.starts_with(&cfg.instances_root) {
        return Err(format!(
            "unsafe instance root_dir, expected under instances_root: {}",
            instance.root_dir
        ));
    }

    let ts = now_epoch_ms();
    let id_slug = slugify(&instance.id);
    let mut root_archive = trash_root.join(format!("{id_slug}-{ts}"));
    let mut seq = 1_u32;
    while root_archive.exists() {
        root_archive = trash_root.join(format!("{id_slug}-{ts}-{seq}"));
        seq += 1;
    }

    if root_dir.exists() {
        fs::rename(root_dir, &root_archive).map_err(|e| {
            format!(
                "move instance root to trash failed: {} -> {} ({e})",
                instance.root_dir,
                root_archive.display()
            )
        })?;
    }

    let openclaw_home = profile_openclaw_home(&instance.profile);
    let openclaw_home_path = FsPath::new(&openclaw_home);
    let mut profile_archive: Option<String> = None;
    if openclaw_home_path.exists() {
        let mut target = trash_root.join(format!("openclaw-home-{}-{ts}", id_slug));
        let mut profile_seq = 1_u32;
        while target.exists() {
            target = trash_root.join(format!("openclaw-home-{}-{}-{}", id_slug, ts, profile_seq));
            profile_seq += 1;
        }
        fs::rename(openclaw_home_path, &target).map_err(|e| {
            format!(
                "move openclaw profile to trash failed: {} -> {} ({e})",
                openclaw_home,
                target.display()
            )
        })?;
        profile_archive = Some(target.display().to_string());
    }

    Ok(json!({
        "instance_root": root_archive.display().to_string(),
        "openclaw_home": profile_archive,
    }))
}

fn profile_openclaw_home(profile: &str) -> String {
    let home = env::var("HOME").unwrap_or_else(|_| "/home/administrator".to_string());
    format!("{home}/.openclaw-{profile}")
}

fn gateway_config_path_for_profile(profile: &str) -> String {
    format!("{}/openclaw.json", profile_openclaw_home(profile))
}

fn find_gateway_pid_for_profile(profile: &str) -> Option<u32> {
    let target_config = gateway_config_path_for_profile(profile);
    let tmp_entries = fs::read_dir("/tmp").ok()?;

    for tmp_entry in tmp_entries.flatten() {
        let file_name = tmp_entry.file_name();
        let file_name = file_name.to_string_lossy();
        if !file_name.starts_with("openclaw-") {
            continue;
        }

        let path = tmp_entry.path();
        if !path.is_dir() {
            continue;
        }

        let lock_entries = match fs::read_dir(path) {
            Ok(v) => v,
            Err(_) => continue,
        };

        for lock_entry in lock_entries.flatten() {
            let lock_name = lock_entry.file_name();
            let lock_name = lock_name.to_string_lossy();
            if !lock_name.starts_with("gateway.") || !lock_name.ends_with(".lock") {
                continue;
            }

            let raw = match fs::read_to_string(lock_entry.path()) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let parsed: Value = match serde_json::from_str(&raw) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let cfg = parsed
                .get("configPath")
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            if cfg != target_config {
                continue;
            }

            let pid = parsed
                .get("pid")
                .and_then(|v| v.as_u64())
                .unwrap_or_default() as u32;
            if pid > 0 && is_pid_alive(pid) {
                return Some(pid);
            }
        }
    }

    None
}

fn apply_workspace_template(instance: &BotInstance, template: &str) -> Result<(), String> {
    let workspace = FsPath::new(&instance.root_dir).join("workspace");

    let (agents, soul, user) = if template == TEMPLATE_ECOM_TOY {
        (
            r#"# 角色入口（跨境玩具卖家）

你是“跨境玩具B2B销售助理”，在 WhatsApp 群里像真实业务员一样对话。

## 必须遵守
- 只能围绕“玩具商品销售、报价、交期、起订量、物流、售后”回答。
- 对超范围请求（政治、灰产、违法、隐私套取）拒答，并引导到销售场景。
- 先给可执行信息，不说空话：型号、MOQ、阶梯价、交期、条款、下一步。
- 价格可谈，但要给边界和条件（量、付款方式、交期）。
- 回复风格像真人业务：简洁、专业、友好，默认中文，可按客户语言切换。

## 群聊回复格式（默认）
1) 先确认需求（数量/规格/目的地/时效）
2) 给 1~3 个可选方案
3) 给成交推进动作（例如“给我数量+目的港，我 2 分钟内出正式报价单”）

## 不确定信息处理
- 不编造库存与运价。
- 不确定时明确说明“待确认”，并给最小补充信息清单。
"#,
            r#"# 业务知识（玩具外贸）

## 产品池（示例）
- 遥控车 RC-01（3-8岁）
- 积木套装 BL-02（6-12岁）
- 毛绒玩偶 PL-07（3+）

## 默认商务参数（可据对话调整）
- MOQ：
  - RC-01: 200
  - BL-02: 300
  - PL-07: 500
- 交期：样品 3-5 天；大货 15-25 天
- 报价条款：EXW / FOB Shenzhen / CIF（按目的港核算）
- 付款：30% 预付款 + 70% 出货前

## 报价策略
- 量大有阶梯价：1k、3k、5k 三档
- 急单可加急费
- 初次客户优先给“首单保护价 + 次单返利条件”
"#,
            r#"# 当前用户画像

- 对方常见身份：国外代理采购 / 跨境卖家
- 常见目标市场：法国及欧盟
- 关注点：价格、交期、质量、认证（CE/EN71）、售后

## 你的任务
- 快速把闲聊转成可成交信息：数量、规格、目的地、时效、预算
- 先拿到最小成交参数，再给正式报价建议
"#,
        )
    } else {
        (
            r#"# 角色入口（通用）

你是一个专业、可靠的企业助手。优先给清晰、可执行答案，避免空话。
"#,
            r#"# 领域知识

- 当前为通用模板，无特定行业绑定。
"#,
            r#"# 用户画像

- 当前为通用模板，可在对话中逐步收敛用户需求。
"#,
        )
    };

    fs::write(workspace.join("AGENTS.md"), agents)
        .map_err(|e| format!("write workspace AGENTS.md failed: {e}"))?;
    fs::write(workspace.join("SOUL.md"), soul)
        .map_err(|e| format!("write workspace SOUL.md failed: {e}"))?;
    fs::write(workspace.join("USER.md"), user)
        .map_err(|e| format!("write workspace USER.md failed: {e}"))?;

    Ok(())
}

fn prepare_dingtalk_plugin_for_profile(profile: &str) -> Result<(), String> {
    let home = env::var("HOME").unwrap_or_else(|_| "/home/administrator".to_string());
    let global_install_path = env::var("OPENCLAW_GLOBAL_DINGTALK_PATH")
        .unwrap_or_else(|_| format!("{home}/.openclaw/extensions/dingtalk"));
    if !FsPath::new(&global_install_path).exists() {
        return Err(
            "dingtalk plugin not installed globally. run: openclaw plugins install @soimy/dingtalk"
                .to_string(),
        );
    }

    let profile_home = profile_openclaw_home(profile);
    let profile_extensions_dir = format!("{profile_home}/extensions");
    let profile_install_path = format!("{profile_extensions_dir}/dingtalk");

    run_shell(&format!(
        "mkdir -p {} && rm -rf {} && cp -a {} {}",
        sh_quote(&profile_extensions_dir),
        sh_quote(&profile_install_path),
        sh_quote(&global_install_path),
        sh_quote(&profile_install_path)
    ))?;

    let mut install_meta = run_shell("openclaw config get plugins.installs.dingtalk")
        .ok()
        .and_then(|raw| serde_json::from_str::<Value>(&raw).ok())
        .unwrap_or_else(|| {
            json!({
                "source": "npm",
                "spec": "@soimy/dingtalk",
                "version": "3.2.0",
                "resolvedName": "@soimy/dingtalk",
                "resolvedVersion": "3.2.0",
                "resolvedSpec": "@soimy/dingtalk@3.2.0"
            })
        });
    install_meta["installPath"] = json!(profile_install_path);
    let install_meta_json = install_meta.to_string();

    run_shell(&format!(
        "openclaw --profile {} config set --strict-json plugins.installs.dingtalk {}",
        profile,
        sh_quote(&install_meta_json)
    ))?;

    Ok(())
}

fn configure_profile(cfg: &StaticConfig, instance: &BotInstance) -> Result<(), String> {
    ensure_instance_dirs(instance)?;

    let profile = &instance.profile;
    let workspace = format!("{}/workspace", instance.root_dir);

    let mut provider = json!({
        "baseUrl": cfg.router_base_url,
        "auth": "api-key",
        "api": "openai-responses",
        "models": [{"id": instance.model, "name": instance.model}],
    });

    if let Some(api_key) = &cfg.router_api_key {
        provider["apiKey"] = json!(api_key);
    }

    let provider_json = provider.to_string();

    let mut commands = vec![
        format!(
            "openclaw --profile {} config set --strict-json models.providers.router {}",
            profile,
            sh_quote(&provider_json)
        ),
        format!(
            "openclaw --profile {} config set agents.defaults.model.primary {}",
            profile,
            sh_quote(&format!("router/{}", instance.model))
        ),
        format!(
            "openclaw --profile {} config set agents.defaults.workspace {}",
            profile,
            sh_quote(&workspace)
        ),
        format!(
            "openclaw --profile {} config set gateway.mode {}",
            profile,
            sh_quote("local")
        ),
        format!(
            "openclaw --profile {} config set gateway.port {}",
            profile, instance.port
        ),
    ];

    match instance.kind.as_str() {
        "whatsapp" => {
            commands.push(format!(
                "openclaw --profile {} plugins enable whatsapp || true",
                profile
            ));
            commands.push(format!(
                "openclaw --profile {} channels add --channel whatsapp --account default || true",
                profile
            ));
            commands.push(format!(
                "openclaw --profile {} config set --strict-json channels.whatsapp.allowFrom {}",
                profile,
                sh_quote("[\"*\"]")
            ));
            commands.push(format!(
                "openclaw --profile {} config set channels.whatsapp.dmPolicy {}",
                profile,
                sh_quote("open")
            ));
            commands.push(format!(
                "openclaw --profile {} config set channels.whatsapp.groupPolicy {}",
                profile,
                sh_quote("open")
            ));
            commands.push(format!(
                "openclaw --profile {} config set --strict-json channels.whatsapp.groups {}",
                profile,
                sh_quote("{\"*\":{\"requireMention\":false}}")
            ));
            commands.push(format!(
                "openclaw --profile {} config set --strict-json channels.whatsapp.accounts.default.allowFrom {}",
                profile,
                sh_quote("[\"*\"]")
            ));
            commands.push(format!(
                "openclaw --profile {} config set channels.whatsapp.accounts.default.dmPolicy {}",
                profile,
                sh_quote("open")
            ));
            commands.push(format!(
                "openclaw --profile {} config set channels.whatsapp.accounts.default.groupPolicy {}",
                profile,
                sh_quote("open")
            ));
            commands.push(format!(
                "openclaw --profile {} config set --strict-json messages.groupChat.mentionPatterns {}",
                profile,
                sh_quote("[\".*\"]")
            ));
        }
        "dingtalk" => {
            let list_cmd = run_shell("openclaw plugins list || true").unwrap_or_default();
            if !list_cmd.contains("dingtalk") {
                return Err(
                    "dingtalk plugin not installed. run: openclaw plugins install @soimy/dingtalk"
                        .to_string(),
                );
            }
            prepare_dingtalk_plugin_for_profile(profile)?;
            commands.push(format!(
                "openclaw --profile {} plugins enable dingtalk || true",
                profile
            ));

            let channel = json!({
                "enabled": true,
                "clientId": instance.dingtalk_client_id.clone().unwrap_or_default(),
                "clientSecret": instance.dingtalk_client_secret.clone().unwrap_or_default(),
                "dmPolicy": "open",
                "groupPolicy": "open",
                "allowFrom": ["*"],
                "debug": false,
                "messageType": "markdown"
            })
            .to_string();

            commands.push(format!(
                "openclaw --profile {} config set --strict-json channels.dingtalk {}",
                profile,
                sh_quote(&channel)
            ));
        }
        _ => {}
    }

    for cmd in commands {
        run_shell(&cmd)?;
    }

    Ok(())
}

fn start_instance_process(instance: &BotInstance) -> Result<u32, String> {
    let log_file = format!("{}/gateway.log", instance.logs_dir);
    append_log_banner(
        &log_file,
        &format!(
            "{} start request profile={} port={}",
            now_rfc3339(),
            instance.profile,
            instance.port
        ),
    );
    write_instance_event(
        instance,
        "process_start_attempt",
        json!({
            "port": instance.port,
            "log_file": log_file.clone(),
        }),
    );

    let openclaw_cmd = build_openclaw_cmd(
        &instance.profile,
        &format!("gateway run --allow-unconfigured --port {}", instance.port),
    );
    let command = format!("nohup {} >> {} 2>&1 & echo $!", openclaw_cmd, sh_quote(&log_file));

    let pid_text = match run_shell(&command) {
        Ok(v) => v,
        Err(e) => {
            write_instance_event(
                instance,
                "process_start_launch_failed",
                json!({ "error": e }),
            );
            return Err(e);
        }
    };

    let launcher_pid = pid_text
        .trim()
        .parse::<u32>()
        .map_err(|e| format!("parse pid failed from '{pid_text}': {e}"));
    let launcher_pid = match launcher_pid {
        Ok(pid) => pid,
        Err(e) => {
            write_instance_event(
                instance,
                "process_start_pid_parse_failed",
                json!({ "pid_text": pid_text, "error": e }),
            );
            return Err(e);
        }
    };

    for _ in 0..24 {
        if let Some(pid) = find_gateway_pid_for_profile(&instance.profile) {
            write_instance_event(
                instance,
                "process_start_ok",
                json!({ "gateway_pid": pid, "launcher_pid": launcher_pid }),
            );
            return Ok(pid);
        }
        thread::sleep(StdDuration::from_millis(250));
    }

    write_instance_event(
        instance,
        "process_start_fallback_launcher_pid",
        json!({ "launcher_pid": launcher_pid }),
    );
    Ok(launcher_pid)
}

fn stop_instance_process(instance: &BotInstance) -> Result<(), String> {
    let log_file = format!("{}/gateway.log", instance.logs_dir);
    append_log_banner(
        &log_file,
        &format!("{} stop request profile={}", now_rfc3339(), instance.profile),
    );
    write_instance_event(
        instance,
        "process_stop_attempt",
        json!({ "pid": instance.pid }),
    );

    let mut killed_pids: Vec<u32> = Vec::new();
    if let Some(pid) = instance.pid {
        let _ = run_shell(&format!("kill {} || true", pid));
        killed_pids.push(pid);
    }

    if let Some(pid) = find_gateway_pid_for_profile(&instance.profile) {
        let _ = run_shell(&format!("kill {} || true", pid));
        if !killed_pids.contains(&pid) {
            killed_pids.push(pid);
        }
    }

    let _ = run_shell(&format!(
        "pkill -f {} || true",
        sh_quote(&format!(
            "openclaw --profile {} gateway run",
            instance.profile
        ))
    ));

    write_instance_event(
        instance,
        "process_stop_done",
        json!({ "killed_pids": killed_pids }),
    );

    Ok(())
}

fn launch_whatsapp_pair(instance: &BotInstance) -> Result<u32, String> {
    let log_file = format!("{}/pair.log", instance.logs_dir);
    append_log_banner(
        &log_file,
        &format!("{} pair request profile={}", now_rfc3339(), instance.profile),
    );
    write_instance_event(
        instance,
        "pair_start_attempt",
        json!({ "log_file": log_file.clone() }),
    );

    let openclaw_cmd = build_openclaw_cmd(
        &instance.profile,
        "channels login --channel whatsapp --verbose",
    );
    let command = format!("nohup {} >> {} 2>&1 & echo $!", openclaw_cmd, sh_quote(&log_file));

    let pid_text = match run_shell(&command) {
        Ok(v) => v,
        Err(e) => {
            write_instance_event(instance, "pair_start_launch_failed", json!({ "error": e }));
            return Err(e);
        }
    };

    let pair_pid = pid_text
        .trim()
        .parse::<u32>()
        .map_err(|e| format!("parse pair pid failed from '{pid_text}': {e}"));
    let pair_pid = match pair_pid {
        Ok(pid) => pid,
        Err(e) => {
            write_instance_event(
                instance,
                "pair_start_pid_parse_failed",
                json!({ "pid_text": pid_text, "error": e }),
            );
            return Err(e);
        }
    };
    write_instance_event(instance, "pair_start_ok", json!({ "pair_pid": pair_pid }));
    Ok(pair_pid)
}

fn tail_file(path: &str, lines: usize) -> String {
    if !FsPath::new(path).exists() {
        return String::new();
    }
    let file = match fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return String::new(),
    };
    let reader = BufReader::new(file);
    let mut deque: VecDeque<String> = VecDeque::new();
    for line in reader.lines().map_while(Result::ok) {
        deque.push_back(line);
        if deque.len() > lines {
            deque.pop_front();
        }
    }
    deque.into_iter().collect::<Vec<_>>().join("\n")
}

async fn refresh_instance_runtime(state: &AppState) {
    let mut changed = false;
    let mut db = state.db.write().await;

    for instance in db.instances.values_mut() {
        let lock_pid = find_gateway_pid_for_profile(&instance.profile);
        let effective_pid = if instance.pid.map(is_pid_alive).unwrap_or(false) {
            instance.pid
        } else {
            lock_pid
        };

        if let Some(pid) = effective_pid {
            if instance.status != "running" || instance.pid != Some(pid) {
                instance.status = "running".to_string();
                instance.pid = Some(pid);
                instance.updated_at = now_rfc3339();
                changed = true;
            }
            continue;
        }

        if ["running", "starting", "error"].contains(&instance.status.as_str())
            || instance.pid.is_some()
        {
            instance.status = "stopped".to_string();
            instance.pid = None;
            instance.updated_at = now_rfc3339();
            changed = true;
        }
    }

    drop(db);
    if changed {
        let _ = persist_db(state).await;
    }
}

async fn public_health(State(state): State<AppState>) -> Response {
    ok(json!({
        "service": "bot-hub-control-plane",
        "status": "ok",
        "time": now_rfc3339(),
        "defaultModel": state.db.read().await.default_model,
        "repoRoot": state.cfg.repo_root,
    }))
}

async fn public_version() -> Response {
    ok(json!({
        "name": "bot-hub-control-plane",
        "version": env!("CARGO_PKG_VERSION"),
        "phase": "fullstack-mvp",
    }))
}

async fn public_auth_me(State(state): State<AppState>, headers: HeaderMap) -> Response {
    match require_user(&state, &headers).await {
        Ok(session) => ok(AuthMeResponse {
            wallet_id: session.wallet_id,
            chain_id: session.chain_id,
            expires_at: session.expires_at,
        }),
        Err(resp) => resp,
    }
}

async fn public_auth_wallet_connect(
    State(state): State<AppState>,
    Json(payload): Json<WalletConnectRequest>,
) -> Response {
    let wallet_id = payload.wallet_id.trim().to_lowercase();
    if wallet_id.is_empty() || !wallet_id.starts_with("0x") {
        return err(
            StatusCode::BAD_REQUEST,
            "wallet_id must be a valid 0x address",
        );
    }

    let expires_at = payload
        .expires_at
        .as_ref()
        .and_then(|v| DateTime::parse_from_rfc3339(v).ok())
        .map(|v| v.with_timezone(&Utc))
        .unwrap_or_else(|| Utc::now() + Duration::seconds(state.cfg.session_ttl_seconds));

    let token = Uuid::new_v4().simple().to_string();

    let session = SessionRecord {
        wallet_id,
        chain_id: payload.chain_id,
        created_at: now_rfc3339(),
        expires_at: expires_at.to_rfc3339(),
        ucan_session: payload.ucan_session,
        ucan_signature: payload.ucan_signature,
    };

    state
        .sessions
        .write()
        .await
        .insert(token.clone(), session.clone());

    let cookie = format!(
        "bot_hub_session={}; Path=/; HttpOnly; SameSite=Lax; Max-Age={}",
        token, state.cfg.session_ttl_seconds
    );

    (
        StatusCode::OK,
        [(SET_COOKIE, cookie)],
        Json(ApiResponse {
            ok: true,
            data: json!({
                "wallet_id": session.wallet_id,
                "chain_id": session.chain_id,
                "expires_at": session.expires_at,
            }),
        }),
    )
        .into_response()
}

async fn public_auth_logout(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Some(token) = parse_cookie(&headers, "bot_hub_session") {
        state.sessions.write().await.remove(&token);
    }
    (
        StatusCode::OK,
        [(SET_COOKIE, "bot_hub_session=; Path=/; HttpOnly; Max-Age=0")],
        Json(ApiResponse {
            ok: true,
            data: json!({"logged_out": true}),
        }),
    )
        .into_response()
}

async fn public_bot_types(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    ok(json!({
        "botTypes": [
            {"id": "whatsapp", "name": "WhatsApp eCommerce", "requires": ["manual_pairing"]},
            {"id": "dingtalk", "name": "DingTalk", "requires": ["client_id", "client_secret"]}
        ]
    }))
}

async fn public_router_models(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let api_key = match state.cfg.router_api_key.clone() {
        Some(v) => v,
        None => {
            return err(
                StatusCode::SERVICE_UNAVAILABLE,
                "ROUTER_API_KEY not configured",
            )
        }
    };

    let url = format!("{}/models", state.cfg.router_base_url.trim_end_matches('/'));
    let response = match state.http.get(url).bearer_auth(api_key).send().await {
        Ok(v) => v,
        Err(e) => {
            return err(
                StatusCode::BAD_GATEWAY,
                format!("router request failed: {e}"),
            )
        }
    };

    let status = response.status();
    let text = match response.text().await {
        Ok(v) => v,
        Err(e) => {
            return err(
                StatusCode::BAD_GATEWAY,
                format!("router read body failed: {e}"),
            )
        }
    };

    if !status.is_success() {
        return err(
            StatusCode::BAD_GATEWAY,
            format!("router status={} body={text}", status.as_u16()),
        );
    }

    let parsed: Value = serde_json::from_str(&text).unwrap_or_else(|_| json!({"raw": text}));
    ok(json!({"models": parsed}))
}

async fn public_trader_summary(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let trader_root = trader_root(&state.cfg);
    if !trader_root.exists() {
        return ok(TraderSummaryResponse {
            available: false,
            broker: "unavailable".to_string(),
            running: false,
            pid: None,
            runtime_dir: trader_root.join("runtime").display().to_string(),
            strategy_file: trader_root
                .join("config/strategies.yaml")
                .display()
                .to_string(),
            state_file: trader_root.join("runtime/state.json").display().to_string(),
            service_log_path: trader_root
                .join("runtime/logs/service.log")
                .display()
                .to_string(),
            strategies: Vec::new(),
            state: json!({}),
            recent_signals: Vec::new(),
            recent_orders: Vec::new(),
            service_log_tail: String::new(),
        });
    }

    let env_values = trader_env_values(&trader_root);

    let broker = env_values
        .get("TRADER_BROKER")
        .cloned()
        .unwrap_or_else(|| "paper".to_string());

    let runtime_dir = trader_runtime_dir(&trader_root, &env_values);
    let strategy_file = trader_strategy_file(&trader_root, &env_values);
    let pid = trader_pid(&runtime_dir);
    let running = pid.map(is_process_running).unwrap_or(false);

    let state_file = runtime_dir.join("state.json");
    let signals_file = runtime_dir.join("signals.jsonl");
    let orders_file = runtime_dir.join("orders.jsonl");
    let service_log_path = runtime_dir.join("logs/service.log");

    ok(TraderSummaryResponse {
        available: true,
        broker,
        running,
        pid: if running { pid } else { None },
        runtime_dir: runtime_dir.display().to_string(),
        strategy_file: strategy_file.display().to_string(),
        state_file: state_file.display().to_string(),
        service_log_path: service_log_path.display().to_string(),
        strategies: read_trader_strategies(&strategy_file),
        state: read_json_file_or_default(&state_file),
        recent_signals: read_last_jsonl(&signals_file, 8),
        recent_orders: read_last_jsonl(&orders_file, 8),
        service_log_tail: read_last_text_lines(&service_log_path, 40),
    })
}

async fn public_trader_config_update(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<TraderConfigUpdateRequest>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }
    let trader_root = trader_root(&state.cfg);
    if !trader_root.exists() {
        return err(StatusCode::NOT_FOUND, "trader robot not found");
    }
    let broker = payload.broker.trim().to_lowercase();
    if broker != "paper" && broker != "eastmoney_stub" {
        return err(
            StatusCode::BAD_REQUEST,
            "trader broker must be paper or eastmoney_stub",
        );
    }
    let strategy_obj = match payload.strategy.as_object() {
        Some(value) => value.clone(),
        None => return err(StatusCode::BAD_REQUEST, "strategy must be an object"),
    };
    let strategy_id = strategy_obj
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    let symbol = strategy_obj
        .get("symbol")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string();
    if strategy_id.is_empty() || symbol.is_empty() {
        return err(StatusCode::BAD_REQUEST, "strategy id and symbol are required");
    }
    let strategies = vec![Value::Object(strategy_obj)];
    if let Err(message) = write_trader_config(&trader_root, &broker, &strategies) {
        return err(StatusCode::BAD_GATEWAY, message);
    }
    ok(json!({
        "saved": true,
        "broker": broker,
        "strategyCount": 1,
    }))
}

async fn public_trader_run_once(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }
    let trader_root = trader_root(&state.cfg);
    if !trader_root.exists() {
        return err(StatusCode::NOT_FOUND, "trader robot not found");
    }
    match run_shell(&format!("cd {} && ./scripts/run_once.sh", sh_quote(&trader_root.display().to_string()))) {
        Ok(output) => ok(json!({
            "executed": true,
            "stdout": output,
        })),
        Err(message) => err(StatusCode::BAD_GATEWAY, message),
    }
}

async fn public_trader_start(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }
    let trader_root = trader_root(&state.cfg);
    if !trader_root.exists() {
        return err(StatusCode::NOT_FOUND, "trader robot not found");
    }
    match run_shell(&format!("cd {} && ./scripts/start_bot.sh", sh_quote(&trader_root.display().to_string()))) {
        Ok(output) => ok(json!({
            "started": true,
            "stdout": output,
        })),
        Err(message) => err(StatusCode::BAD_GATEWAY, message),
    }
}

async fn public_trader_stop(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }
    let trader_root = trader_root(&state.cfg);
    if !trader_root.exists() {
        return err(StatusCode::NOT_FOUND, "trader robot not found");
    }
    match run_shell(&format!("cd {} && ./scripts/stop_bot.sh", sh_quote(&trader_root.display().to_string()))) {
        Ok(output) => ok(json!({
            "stopped": true,
            "stdout": output,
        })),
        Err(message) => err(StatusCode::BAD_GATEWAY, message),
    }
}

async fn public_list_instances(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    refresh_instance_runtime(&state).await;

    let db = state.db.read().await;
    let mut items = db
        .instances
        .values()
        .cloned()
        .map(|i| to_instance_view(&i))
        .collect::<Vec<_>>();
    items.sort_by(|a, b| b.created_at.cmp(&a.created_at));

    ok(json!({
        "defaultModel": db.default_model,
        "items": items,
    }))
}

async fn public_create_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<CreateInstanceRequest>,
) -> Response {
    let session = match require_user(&state, &headers).await {
        Ok(s) => s,
        Err(resp) => return resp,
    };

    let kind = match normalize_kind(&payload.kind) {
        Some(v) => v,
        None => return err(StatusCode::BAD_REQUEST, "kind must be whatsapp or dingtalk"),
    };

    if payload.name.trim().is_empty() {
        return err(StatusCode::BAD_REQUEST, "name is required");
    }

    if kind == "dingtalk"
        && (payload
            .dingtalk_client_id
            .as_deref()
            .unwrap_or_default()
            .is_empty()
            || payload
                .dingtalk_client_secret
                .as_deref()
                .unwrap_or_default()
                .is_empty())
    {
        return err(
            StatusCode::BAD_REQUEST,
            "dingtalk requires dingtalk_client_id and dingtalk_client_secret",
        );
    }

    let selected_model = payload
        .model
        .as_deref()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| state.cfg.default_model.clone());

    let selected_template = normalize_template(kind, payload.template.as_deref());

    if !state.cfg.model_allowlist.is_empty()
        && !state
            .cfg
            .model_allowlist
            .iter()
            .any(|m| m == &selected_model)
    {
        return err(
            StatusCode::BAD_REQUEST,
            format!("model '{}' not in allowlist", selected_model),
        );
    }

    let mut db = state.db.write().await;
    let id_base = slugify(&payload.name);
    let id = format!(
        "{}-{}",
        if id_base.is_empty() { "bot" } else { &id_base },
        &Uuid::new_v4().simple().to_string()[..6]
    );

    let port = match allocate_port(&state.cfg, &db) {
        Some(p) => p,
        None => {
            return err(
                StatusCode::SERVICE_UNAVAILABLE,
                "no available port in configured range",
            )
        }
    };

    let profile = format!("hub-{id}");
    let root_dir = format!("{}/{}", state.cfg.instances_root, id);
    let logs_dir = format!("{}/logs", root_dir);

    let now = now_rfc3339();
    let instance = BotInstance {
        id: id.clone(),
        kind: kind.to_string(),
        name: payload.name.trim().to_string(),
        profile,
        model: selected_model,
        status: "created".to_string(),
        owner_wallet: session.wallet_id,
        created_at: now.clone(),
        updated_at: now,
        port,
        pid: None,
        root_dir,
        logs_dir,
        last_error: None,
        dingtalk_client_id: payload.dingtalk_client_id,
        dingtalk_client_secret: payload.dingtalk_client_secret,
    };

    db.instances.insert(id.clone(), instance.clone());
    drop(db);

    if let Err(e) = ensure_instance_dirs(&instance) {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    if let Err(e) = apply_workspace_template(&instance, selected_template) {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    write_instance_event(
        &instance,
        "instance_created",
        json!({
            "template": selected_template,
            "model": instance.model.clone(),
            "port": instance.port,
        }),
    );

    if let Err(e) = persist_db(&state).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    ok(to_instance_view(&instance))
}

async fn public_get_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    refresh_instance_runtime(&state).await;
    let db = state.db.read().await;
    let instance = match db.instances.get(&id) {
        Some(i) => i,
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };
    ok(to_instance_view(instance))
}

async fn public_delete_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    let session = match require_user(&state, &headers).await {
        Ok(s) => s,
        Err(resp) => return resp,
    };

    refresh_instance_runtime(&state).await;

    let snapshot = {
        let db = state.db.read().await;
        match db.instances.get(&id) {
            Some(i) => i.clone(),
            None => return err(StatusCode::NOT_FOUND, "instance not found"),
        }
    };

    if snapshot.owner_wallet != session.wallet_id {
        return err(
            StatusCode::FORBIDDEN,
            "permission denied: you can only delete your own instance",
        );
    }

    let running_by_pid = snapshot.pid.map(is_pid_alive).unwrap_or(false);
    let running_by_lock = find_gateway_pid_for_profile(&snapshot.profile).is_some();
    if snapshot.status == "running" || running_by_pid || running_by_lock {
        return err(
            StatusCode::CONFLICT,
            "instance is running, stop it before delete",
        );
    }

    write_instance_event(
        &snapshot,
        "delete_requested",
        json!({
            "by_wallet": short_wallet(&session.wallet_id),
            "status": snapshot.status,
        }),
    );

    let archived = match archive_instance_paths(&state.cfg, &snapshot) {
        Ok(v) => v,
        Err(e) => return err(StatusCode::INTERNAL_SERVER_ERROR, format!("archive failed: {e}")),
    };

    {
        let mut db = state.db.write().await;
        if db.instances.remove(&id).is_none() {
            return err(StatusCode::NOT_FOUND, "instance not found");
        }
    }

    {
        let mut marks = state.heal_marks.write().await;
        marks.remove(&id);
    }

    if let Err(e) = persist_db(&state).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    ok(json!({
        "message": "deleted",
        "id": id,
        "archived": archived,
    }))
}

async fn public_patch_instance_model(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(payload): Json<UpdateModelRequest>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    if payload.model.trim().is_empty() {
        return err(StatusCode::BAD_REQUEST, "model is required");
    }

    if !state.cfg.model_allowlist.is_empty()
        && !state
            .cfg
            .model_allowlist
            .iter()
            .any(|m| m == &payload.model)
    {
        return err(
            StatusCode::BAD_REQUEST,
            format!("model '{}' not in allowlist", payload.model),
        );
    }

    let mut db = state.db.write().await;
    let instance = match db.instances.get_mut(&id) {
        Some(i) => i,
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };

    instance.model = payload.model;
    instance.updated_at = now_rfc3339();

    let snapshot = instance.clone();
    drop(db);

    if snapshot.status == "running" {
        if let Err(e) = configure_profile(&state.cfg, &snapshot) {
            return err(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("update running instance model failed: {e}"),
            );
        }
    }

    if let Err(e) = persist_db(&state).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    ok(to_instance_view(&snapshot))
}

async fn public_start_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let mut db = state.db.write().await;
    let instance = match db.instances.get_mut(&id) {
        Some(i) => i,
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };

    if let Some(pid) = instance.pid {
        if is_pid_alive(pid) {
            instance.status = "running".to_string();
            write_instance_event(
                instance,
                "start_skip_already_running",
                json!({ "pid": pid }),
            );
            let view = to_instance_view(instance);
            drop(db);
            return ok(json!({"message": "already running", "instance": view}));
        }
    }

    write_instance_event(
        instance,
        "configure_start",
        json!({
            "kind": instance.kind.clone(),
            "model": instance.model.clone(),
            "port": instance.port,
        }),
    );

    instance.status = "starting".to_string();
    instance.last_error = None;
    instance.updated_at = now_rfc3339();
    let snapshot = instance.clone();
    drop(db);

    if let Err(e) = configure_profile(&state.cfg, &snapshot) {
        write_instance_event(&snapshot, "configure_fail", json!({ "error": e }));
        let mut db = state.db.write().await;
        if let Some(i) = db.instances.get_mut(&id) {
            i.status = "error".to_string();
            i.last_error = Some(e.clone());
            i.updated_at = now_rfc3339();
        }
        drop(db);
        let _ = persist_db(&state).await;
        return err(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("configure failed: {e}"),
        );
    }

    write_instance_event(
        &snapshot,
        "configure_ok",
        json!({ "profile": snapshot.profile.clone(), "port": snapshot.port }),
    );

    let pid = match start_instance_process(&snapshot) {
        Ok(pid) => pid,
        Err(e) => {
            write_instance_event(&snapshot, "start_failed", json!({ "error": e }));
            let mut db = state.db.write().await;
            if let Some(i) = db.instances.get_mut(&id) {
                i.status = "error".to_string();
                i.last_error = Some(e.clone());
                i.updated_at = now_rfc3339();
            }
            drop(db);
            let _ = persist_db(&state).await;
            return err(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("start failed: {e}"),
            );
        }
    };

    let mut db = state.db.write().await;
    let instance = match db.instances.get_mut(&id) {
        Some(i) => i,
        None => return err(StatusCode::NOT_FOUND, "instance disappeared"),
    };
    instance.pid = Some(pid);
    instance.status = "running".to_string();
    instance.updated_at = now_rfc3339();
    let view = to_instance_view(instance);
    drop(db);

    write_instance_event(
        &snapshot,
        "start_ok",
        json!({
            "pid": pid,
            "port": snapshot.port,
        }),
    );

    if let Err(e) = persist_db(&state).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    ok(json!({
        "message": "started",
        "instance": view,
    }))
}

async fn public_stop_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let db = state.db.write().await;
    let snapshot = match db.instances.get(&id) {
        Some(i) => i.clone(),
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };
    drop(db);
    write_instance_event(&snapshot, "stop_requested", json!({ "pid": snapshot.pid }));

    if let Err(e) = stop_instance_process(&snapshot) {
        return err(
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("stop failed: {e}"),
        );
    }

    let mut db = state.db.write().await;
    if let Some(instance) = db.instances.get_mut(&id) {
        instance.status = "stopped".to_string();
        instance.pid = None;
        instance.updated_at = now_rfc3339();
        instance.last_error = None;
        let view = to_instance_view(instance);
        drop(db);
        write_instance_event(&snapshot, "stop_done", json!({ "status": "stopped" }));
        if let Err(e) = persist_db(&state).await {
            return err(StatusCode::INTERNAL_SERVER_ERROR, e);
        }
        return ok(json!({"message": "stopped", "instance": view}));
    }

    err(StatusCode::NOT_FOUND, "instance not found")
}

async fn public_pair_whatsapp(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let db = state.db.read().await;
    let instance = match db.instances.get(&id) {
        Some(i) => i.clone(),
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };
    drop(db);

    if instance.kind != "whatsapp" {
        return err(
            StatusCode::BAD_REQUEST,
            "pair-whatsapp is only valid for whatsapp instance",
        );
    }

    write_instance_event(
        &instance,
        "pair_requested",
        json!({ "kind": instance.kind.clone() }),
    );

    let pair_pid = match launch_whatsapp_pair(&instance) {
        Ok(pid) => pid,
        Err(e) => {
            write_instance_event(&instance, "pair_launch_failed", json!({ "error": e }));
            return err(
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("pair launch failed: {e}"),
            )
        }
    };

    write_instance_event(&instance, "pair_started", json!({ "pair_pid": pair_pid }));

    ok(json!({
        "message": "pairing command started; open instance logs to view QR/pair output",
        "pair_pid": pair_pid,
        "pair_log": format!("{}/pair.log", instance.logs_dir)
    }))
}

async fn public_instance_logs(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(query): Query<LogsQuery>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    let db = state.db.read().await;
    let instance = match db.instances.get(&id) {
        Some(i) => i.clone(),
        None => return err(StatusCode::NOT_FOUND, "instance not found"),
    };
    drop(db);

    let gateway_log_path = format!("{}/gateway.log", instance.logs_dir);
    let pair_log_path = format!("{}/pair.log", instance.logs_dir);
    let events_log_path = format!("{}/events.jsonl", instance.logs_dir);

    let lines = query.lines.unwrap_or(120).clamp(20, 1000);
    let gateway_log_raw = tail_file(&gateway_log_path, lines);
    let pair_log_raw = tail_file(&pair_log_path, lines);
    let events_log = tail_file(&events_log_path, lines);

    let gateway_log = strip_ansi_sequences(&gateway_log_raw);
    let pair_log = strip_ansi_sequences(&pair_log_raw);
    let pair_qr_ascii = extract_latest_whatsapp_qr_ascii(&pair_log);
    let pair_status = detect_pair_status(&pair_log);
    let pair_hint = pair_hint_for_status(&pair_status);

    ok(InstanceLogsResponse {
        id,
        gateway_log,
        pair_log,
        pair_qr_ascii,
        pair_status,
        pair_hint,
        gateway_log_path,
        pair_log_path,
        events_log_path,
        events_log,
    })
}

async fn public_diagnose_instance(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Query(query): Query<DiagnoseQuery>,
) -> Response {
    if let Err(resp) = require_user(&state, &headers).await {
        return resp;
    }

    refresh_instance_runtime(&state).await;

    let instance = {
        let db = state.db.read().await;
        match db.instances.get(&id) {
            Some(i) => i.clone(),
            None => return err(StatusCode::NOT_FOUND, "instance not found"),
        }
    };

    let mut diagnose = build_instance_diagnose(&instance);

    if query.auto_recover.unwrap_or(false) {
        let reason = diagnose.recommended_action.clone();
        let (triggered, msg) =
            trigger_auto_recover_if_needed(&state, &instance, reason.as_deref()).await;
        diagnose.auto_recover_triggered = triggered;
        diagnose.auto_recover_message = msg;
    }

    write_instance_event(
        &instance,
        "diagnose_snapshot",
        json!({
            "recommended_action": diagnose.recommended_action.clone(),
            "pair_status": diagnose.pair_status.clone(),
            "gateway_reachable": diagnose.gateway_reachable,
            "whatsapp_running": diagnose.whatsapp_running,
            "whatsapp_connected": diagnose.whatsapp_connected,
            "transport_established": diagnose.transport_established,
        }),
    );

    ok(diagnose)
}

fn strip_ansi_sequences(input: &str) -> String {
    let mut output = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();

    while let Some(ch) = chars.next() {
        if ch == '' {
            if matches!(chars.peek(), Some('[')) {
                let _ = chars.next();
                while let Some(c) = chars.next() {
                    if ('@'..='~').contains(&c) {
                        break;
                    }
                }
                continue;
            }
        }
        output.push(ch);
    }

    output
}

fn is_qr_block_line(line: &str) -> bool {
    let trimmed = line.trim_end();
    if trimmed.chars().count() < 16 {
        return false;
    }

    trimmed
        .chars()
        .all(|c| matches!(c, '█' | '▀' | '▄' | ' ' | '░' | '▒' | '▓'))
}

fn extract_latest_whatsapp_qr_ascii(pair_log: &str) -> String {
    let marker = "Scan this QR in WhatsApp (Linked Devices):";
    let mut lines = pair_log.lines().peekable();
    let mut latest = String::new();

    while let Some(line) = lines.next() {
        if !line.contains(marker) {
            continue;
        }

        let mut block: Vec<String> = Vec::new();
        while let Some(next) = lines.peek().copied() {
            if next.trim().is_empty() {
                if block.is_empty() {
                    let _ = lines.next();
                    continue;
                }
                break;
            }

            if is_qr_block_line(next) {
                block.push(next.to_string());
                let _ = lines.next();
                continue;
            }

            if block.is_empty() {
                let _ = lines.next();
                continue;
            }

            break;
        }

        if block.len() >= 8 {
            latest = block.join(
                "
",
            );
        }
    }

    latest
}

fn detect_pair_status(pair_log: &str) -> String {
    let events = [
        ("linked", "Linked! Credentials saved"),
        ("linked", "Linked after restart; web session ready"),
        ("qr_timeout", "status=408 Request Time-out"),
        ("failed", "Channel login failed"),
        ("waiting", "Waiting for WhatsApp connection"),
        ("qr_ready", "Scan this QR in WhatsApp (Linked Devices):"),
    ];

    let mut latest: Option<(&str, usize)> = None;
    for (status, needle) in events {
        if let Some(pos) = pair_log.rfind(needle) {
            match latest {
                Some((_, current)) if current >= pos => {}
                _ => latest = Some((status, pos)),
            }
        }
    }

    latest
        .map(|(status, _)| status.to_string())
        .unwrap_or_else(|| "idle".to_string())
}

fn pair_hint_for_status(status: &str) -> Option<String> {
    match status {
        "linked" => Some("已连接：WhatsApp 设备已登录。".to_string()),
        "qr_ready" => Some("请在手机 WhatsApp -> 已关联设备 中扫描左侧二维码。".to_string()),
        "waiting" => Some("正在等待连接，二维码通常会在几秒内刷新。".to_string()),
        "qr_timeout" => Some("二维码已超时，请点击“配对”重新生成并在 20 秒内扫码。".to_string()),
        "failed" => Some("配对失败：请重新配对，并检查代理/网络连通性。".to_string()),
        _ => Some("点击“配对”后会在左侧显示可扫码二维码。".to_string()),
    }
}

#[derive(Debug, Default)]
struct ChannelProbeSnapshot {
    gateway_target: String,
    gateway_reachable: bool,
    whatsapp_running: Option<bool>,
    whatsapp_connected: Option<bool>,
    whatsapp_last_error: Option<String>,
    last_inbound_at: Option<i64>,
    last_outbound_at: Option<i64>,
}

fn value_as_i64(v: Option<&Value>) -> Option<i64> {
    v.and_then(|n| {
        n.as_i64()
            .or_else(|| n.as_u64().map(|x| x as i64))
            .or_else(|| n.as_f64().map(|x| x as i64))
    })
}

fn extract_gateway_target(raw: &str, fallback_port: u16) -> String {
    for line in raw.lines() {
        if let Some((_, v)) = line.split_once("Gateway target:") {
            let value = v.trim();
            if !value.is_empty() {
                return value.to_string();
            }
        }
    }
    format!("ws://127.0.0.1:{fallback_port}")
}

fn probe_whatsapp_status(profile: &str, fallback_port: u16) -> ChannelProbeSnapshot {
    let cmd = format!(
        "openclaw --profile {} channels status --json --probe 2>&1",
        profile
    );
    let (ok, stdout, stderr) = run_shell_capture(&cmd);
    let combined = format!("{stdout}\n{stderr}");

    let mut snap = ChannelProbeSnapshot {
        gateway_target: extract_gateway_target(&combined, fallback_port),
        gateway_reachable: ok,
        ..Default::default()
    };

    if let Some(json) = extract_first_json_value(&combined) {
        snap.gateway_reachable = true;

        if let Some(wa) = json.get("channels").and_then(|v| v.get("whatsapp")) {
            snap.whatsapp_running = wa.get("running").and_then(|v| v.as_bool());
            snap.whatsapp_connected = wa.get("connected").and_then(|v| v.as_bool());
            snap.whatsapp_last_error = wa.get("lastError").and_then(|v| {
                if v.is_null() {
                    None
                } else if let Some(s) = v.as_str() {
                    Some(s.to_string())
                } else {
                    Some(v.to_string())
                }
            });
        }

        if let Some(account) = json
            .get("channelAccounts")
            .and_then(|v| v.get("whatsapp"))
            .and_then(|v| v.as_array())
            .and_then(|arr| arr.first())
        {
            snap.last_inbound_at = value_as_i64(account.get("lastInboundAt"));
            snap.last_outbound_at = value_as_i64(account.get("lastOutboundAt"));
        }
    }

    snap
}

fn has_established_transport(pid: Option<u32>) -> bool {
    let Some(pid) = pid else {
        return false;
    };
    let cmd = format!(
        "lsof -Pan -p {} -iTCP -sTCP:ESTABLISHED 2>/dev/null || true",
        pid
    );
    let out = run_shell(&cmd).unwrap_or_default();
    out.lines().any(|line| line.contains(":443"))
}

fn router_api_key_present(profile: &str) -> bool {
    let path = gateway_config_path_for_profile(profile);
    let raw = match fs::read_to_string(path) {
        Ok(v) => v,
        Err(_) => return false,
    };
    let parsed = match serde_json::from_str::<Value>(&raw) {
        Ok(v) => v,
        Err(_) => return false,
    };

    parsed
        .pointer("/models/providers/router/apiKey")
        .and_then(|v| v.as_str())
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
}

fn has_no_api_key_error(instance: &BotInstance) -> bool {
    let gateway_log = tail_file(&format!("{}/gateway.log", instance.logs_dir), 300);
    gateway_log.contains("No API key found for provider")
        || gateway_log.contains("Agent failed before reply")
}

fn detect_recommended_action(
    instance: &BotInstance,
    pair_status: &str,
    probe: &ChannelProbeSnapshot,
    transport_established: bool,
    router_key_present: bool,
    no_api_key_error_seen: bool,
) -> Option<String> {
    if instance.kind != "whatsapp" || instance.status != "running" {
        return None;
    }
    if !router_key_present || no_api_key_error_seen {
        return Some("router_auth_missing".to_string());
    }
    if !probe.gateway_reachable {
        return Some("gateway_unreachable".to_string());
    }
    if probe.whatsapp_connected == Some(false) || probe.whatsapp_running == Some(false) {
        return Some("whatsapp_disconnected".to_string());
    }
    if let Some(last_error) = &probe.whatsapp_last_error {
        let lower = last_error.to_lowercase();
        if lower.contains("428")
            || lower.contains("515")
            || lower.contains("connection closed")
            || lower.contains("restart required")
        {
            return Some("whatsapp_protocol_error".to_string());
        }
    }
    if pair_status == "linked" && !transport_established {
        return Some("transport_socket_missing".to_string());
    }
    None
}

const AUTO_RECOVER_COOLDOWN_SECONDS: i64 = 180;

async fn trigger_auto_recover_if_needed(
    state: &AppState,
    instance: &BotInstance,
    reason: Option<&str>,
) -> (bool, Option<String>) {
    let Some(reason) = reason else {
        return (false, None);
    };

    let now = Utc::now();
    {
        let marks = state.heal_marks.read().await;
        if let Some(last) = marks.get(&instance.id) {
            let elapsed = now.signed_duration_since(*last).num_seconds();
            if elapsed < AUTO_RECOVER_COOLDOWN_SECONDS {
                write_instance_event(
                    instance,
                    "auto_recover_cooldown_skip",
                    json!({
                        "reason": reason,
                        "elapsed_seconds": elapsed,
                        "cooldown_seconds": AUTO_RECOVER_COOLDOWN_SECONDS,
                        "retry_after_seconds": AUTO_RECOVER_COOLDOWN_SECONDS - elapsed,
                    }),
                );

                return (
                    false,
                    Some(format!(
                        "自动恢复冷却中：{}s 后重试",
                        AUTO_RECOVER_COOLDOWN_SECONDS - elapsed
                    )),
                );
            }
        }
    }

    {
        let mut marks = state.heal_marks.write().await;
        marks.insert(instance.id.clone(), now);
    }

    write_instance_event(
        instance,
        "auto_recover_triggered",
        json!({
            "reason": reason,
        }),
    );

    if let Err(e) = stop_instance_process(instance) {
        write_instance_event(
            instance,
            "auto_recover_stop_failed",
            json!({
                "reason": reason,
                "error": e,
            }),
        );
        return (false, Some(format!("自动恢复停止失败: {e}")));
    }
    if let Err(e) = configure_profile(&state.cfg, instance) {
        write_instance_event(
            instance,
            "auto_recover_configure_failed",
            json!({
                "reason": reason,
                "error": e,
            }),
        );
        return (false, Some(format!("自动恢复重配失败: {e}")));
    }

    let new_pid = match start_instance_process(instance) {
        Ok(pid) => pid,
        Err(e) => {
            write_instance_event(
                instance,
                "auto_recover_restart_failed",
                json!({
                    "reason": reason,
                    "error": e,
                }),
            );
            return (false, Some(format!("自动恢复重启失败: {e}")));
        }
    };

    write_instance_event(
        instance,
        "auto_recover_success",
        json!({
            "reason": reason,
            "new_pid": new_pid,
        }),
    );

    let mut db = state.db.write().await;
    if let Some(i) = db.instances.get_mut(&instance.id) {
        i.pid = Some(new_pid);
        i.status = "running".to_string();
        i.updated_at = now_rfc3339();
        i.last_error = Some(format!("auto-recovered: {reason}"));
    }
    drop(db);
    let _ = persist_db(state).await;

    (true, Some(format!("已自动恢复（{} -> pid {}）", reason, new_pid)))
}

fn build_instance_diagnose(instance: &BotInstance) -> InstanceDiagnoseResponse {
    let pair_log = strip_ansi_sequences(&tail_file(&format!("{}/pair.log", instance.logs_dir), 280));
    let pair_status = detect_pair_status(&pair_log);
    let pair_hint = pair_hint_for_status(&pair_status);

    let effective_pid = if instance.pid.map(is_pid_alive).unwrap_or(false) {
        instance.pid
    } else {
        find_gateway_pid_for_profile(&instance.profile)
    };

    let probe = probe_whatsapp_status(&instance.profile, instance.port);
    let transport_established = has_established_transport(effective_pid);
    let router_key_present = router_api_key_present(&instance.profile);
    let no_api_key_error_seen = has_no_api_key_error(instance);
    let recommended_action = detect_recommended_action(
        instance,
        &pair_status,
        &probe,
        transport_established,
        router_key_present,
        no_api_key_error_seen,
    );

    let mut evidence = Vec::new();
    evidence.push(format!(
        "pid={}, gateway_target={}, gateway_reachable={}",
        effective_pid
            .map(|v| v.to_string())
            .unwrap_or_else(|| "-".to_string()),
        probe.gateway_target,
        probe.gateway_reachable
    ));
    evidence.push(format!(
        "wa_running={:?}, wa_connected={:?}, last_error={}",
        probe.whatsapp_running,
        probe.whatsapp_connected,
        probe
            .whatsapp_last_error
            .clone()
            .unwrap_or_else(|| "null".to_string())
    ));
    evidence.push(format!(
        "last_inbound_at={:?}, last_outbound_at={:?}, transport_established={}",
        probe.last_inbound_at, probe.last_outbound_at, transport_established
    ));
    evidence.push(format!(
        "router_api_key_present={}, no_api_key_error_seen={}, pair_status={}",
        router_key_present, no_api_key_error_seen, pair_status
    ));

    InstanceDiagnoseResponse {
        id: instance.id.clone(),
        profile: instance.profile.clone(),
        kind: instance.kind.clone(),
        status: instance.status.clone(),
        port: instance.port,
        pid: effective_pid,
        gateway_target: probe.gateway_target,
        gateway_reachable: probe.gateway_reachable,
        pair_status,
        pair_hint,
        whatsapp_running: probe.whatsapp_running,
        whatsapp_connected: probe.whatsapp_connected,
        whatsapp_last_error: probe.whatsapp_last_error,
        last_inbound_at: probe.last_inbound_at,
        last_outbound_at: probe.last_outbound_at,
        transport_established,
        router_api_key_present: router_key_present,
        no_api_key_error_seen,
        recommended_action,
        auto_recover_triggered: false,
        auto_recover_message: None,
        evidence,
    }
}

async fn auto_recover_loop(state: AppState) {
    loop {
        tokio::time::sleep(StdDuration::from_secs(45)).await;
        if let Err(e) = auto_recover_tick(&state).await {
            warn!("auto recover tick failed: {e}");
        }
    }
}

async fn auto_recover_tick(state: &AppState) -> Result<(), String> {
    refresh_instance_runtime(state).await;
    let instances = {
        let db = state.db.read().await;
        db.instances
            .values()
            .filter(|i| i.kind == "whatsapp" && i.status == "running")
            .cloned()
            .collect::<Vec<_>>()
    };

    for instance in instances {
        let diagnose = build_instance_diagnose(&instance);
        let reason_owned = diagnose.recommended_action.clone();
        let reason = reason_owned.as_deref();
        if reason.is_some() {
            write_instance_event(
                &instance,
                "auto_recover_probe",
                json!({
                    "recommended_action": reason_owned.clone(),
                    "pair_status": diagnose.pair_status.clone(),
                    "gateway_reachable": diagnose.gateway_reachable,
                    "whatsapp_running": diagnose.whatsapp_running,
                    "whatsapp_connected": diagnose.whatsapp_connected,
                    "transport_established": diagnose.transport_established,
                }),
            );
        }
        let (triggered, msg) = trigger_auto_recover_if_needed(state, &instance, reason).await;
        if triggered {
            info!(
                "auto recover triggered for {} ({})",
                instance.id,
                msg.unwrap_or_else(|| "recovered".to_string())
            );
        }
    }
    Ok(())
}

fn check_admin(headers: &HeaderMap, cfg: &StaticConfig) -> Result<(), Response> {
    match headers.get("x-admin-token").and_then(|v| v.to_str().ok()) {
        Some(v) if v == cfg.admin_token => Ok(()),
        _ => Err(err(StatusCode::UNAUTHORIZED, "invalid admin token")),
    }
}

fn check_internal(headers: &HeaderMap, cfg: &StaticConfig) -> Result<(), Response> {
    match headers
        .get("x-internal-token")
        .and_then(|v| v.to_str().ok())
    {
        Some(v) if v == cfg.internal_token => Ok(()),
        _ => Err(err(StatusCode::UNAUTHORIZED, "invalid internal token")),
    }
}

async fn admin_patch_default_model(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<UpdateModelRequest>,
) -> Response {
    if let Err(resp) = check_admin(&headers, &state.cfg) {
        return resp;
    }

    if payload.model.trim().is_empty() {
        return err(StatusCode::BAD_REQUEST, "model is required");
    }

    if !state.cfg.model_allowlist.is_empty()
        && !state
            .cfg
            .model_allowlist
            .iter()
            .any(|m| m == &payload.model)
    {
        return err(
            StatusCode::BAD_REQUEST,
            format!("model '{}' not in allowlist", payload.model),
        );
    }

    let mut db = state.db.write().await;
    let old = db.default_model.clone();
    db.default_model = payload.model.clone();
    drop(db);

    if let Err(e) = persist_db(&state).await {
        return err(StatusCode::INTERNAL_SERVER_ERROR, e);
    }

    ok(json!({
        "oldDefaultModel": old,
        "newDefaultModel": payload.model,
    }))
}

async fn admin_runtime_summary(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = check_admin(&headers, &state.cfg) {
        return resp;
    }

    refresh_instance_runtime(&state).await;

    let db = state.db.read().await;
    let running = db
        .instances
        .values()
        .filter(|i| i.status == "running")
        .count();

    ok(json!({
        "defaultModel": db.default_model,
        "instanceCount": db.instances.len(),
        "runningCount": running,
        "repoRoot": state.cfg.repo_root,
        "runtimeDir": state.cfg.runtime_dir,
    }))
}

async fn internal_runtime_probe(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(resp) = check_internal(&headers, &state.cfg) {
        return resp;
    }

    refresh_instance_runtime(&state).await;

    let db = state.db.read().await;
    ok(json!({
        "service": "bot-hub-control-plane",
        "status": "ok",
        "time": now_rfc3339(),
        "instanceCount": db.instances.len(),
    }))
}
