//! Conduit Rust v0 — synchronous parity port of TypeScript/Python.
//! Drivers: openai-compatible, ollama, anthropic, gemini
//! HTTP: ureq (sync), JSON: serde_json
//! Timeout: integer milliseconds 1..2147483647 covering whole operation

#![allow(
    dead_code,
    clippy::result_large_err,
    clippy::type_complexity,
    clippy::ifs_same_cond,
    clippy::redundant_closure,
    clippy::let_and_return,
    clippy::manual_pattern_char_comparison,
    clippy::double_comparisons,
    clippy::manual_strip
)]
#![allow(unused_mut, unused_variables)]
use std::collections::{HashMap, HashSet};
use std::io::{BufRead, BufReader, Read};
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};

use serde_json::Value;

mod errors;
mod types;
pub use errors::*;
pub use types::*;

mod providers;
use providers::anthropic::{
    anthropic_error, anthropic_sse_stream, decode_anthropic, encode_anthropic_request,
};
use providers::gemini::{decode_gemini, encode_gemini_request, gemini_error, gemini_sse_stream};
use providers::ollama::{decode_ollama, encode_ollama_request, ollama_error, ollama_ndjson_stream};
use providers::openai::{
    decode_openai, encode_openai_request, http_error_openai, normalize_openai_models,
    openai_sse_stream,
};

pub fn ollama(model: impl Into<String>) -> Result<Model, ConduitError> {
    let m = model.into();
    let c = connect(ClientConfig::ollama(m.clone()))?;
    Ok(c.model(&m).unwrap())
}
pub fn anthropic(model: impl Into<String>) -> Result<Model, ConduitError> {
    let m = model.into();
    let c = connect(ClientConfig::anthropic(m.clone()))?;
    Ok(c.model(&m).unwrap())
}
pub fn gemini(model: impl Into<String>) -> Result<Model, ConduitError> {
    let m = model.into();
    let c = connect(ClientConfig::gemini(m.clone()))?;
    Ok(c.model(&m).unwrap())
}
// Minimal helpers for compilation
// ---------------------------------------------------------------------------
fn make_redactor(secrets: &[String]) -> Arc<dyn Fn(&str) -> String + Send + Sync> {
    let mut uniq: Vec<String> = secrets.iter().filter(|s| !s.is_empty()).cloned().collect();
    uniq.sort_by_key(|b| std::cmp::Reverse(b.len()));
    let mut expanded = Vec::new();
    let mut seen = HashSet::new();
    for s in uniq {
        let stripped = s.trim().to_string();
        let variants = vec![
            s.clone(),
            urlencoding(&s),
            stripped.clone(),
            urlencoding(&stripped),
        ];
        for v in variants {
            if !v.is_empty() && seen.insert(v.clone()) {
                expanded.push(v);
            }
        }
    }
    expanded.sort_by_key(|b| std::cmp::Reverse(b.len()));
    let expanded = Arc::new(expanded);
    Arc::new(move |text: &str| {
        let mut out = text.to_string();
        for sec in expanded.iter() {
            out = out.replace(sec, "[REDACTED]");
        }
        out
    })
}
fn urlencoding(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if (b as char).is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{:02X}", b));
        }
    }
    out
}
#[allow(clippy::result_large_err)]
fn timeout_value(v: Option<u32>) -> Result<(), ConduitError> {
    if let Some(n) = v {
        if !(1..=2147483647).contains(&n) {
            return Err(ConduitError::new(
                "InvalidRequestError",
                "timeout must be an integer from 1 through 2147483647 milliseconds.",
            ));
        }
    }
    Ok(())
}
fn is_aborted(sig: &Option<Arc<AtomicBool>>) -> bool {
    sig.as_ref()
        .map(|a| a.load(std::sync::atomic::Ordering::SeqCst))
        .unwrap_or(false)
}
#[allow(clippy::result_large_err)]
fn json_validate(v: &Value, seen: &mut HashSet<usize>) -> Result<(), ConduitError> {
    match v {
        Value::Null | Value::Bool(_) | Value::String(_) => Ok(()),
        Value::Number(n) => {
            if !n.is_i64() && !n.is_u64() && !n.as_f64().map(|f| f.is_finite()).unwrap_or(false) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions must contain acyclic JSON data.",
                ));
            }
            Ok(())
        }
        Value::Array(arr) => {
            let id = v as *const _ as usize;
            if !seen.insert(id) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions must contain acyclic JSON data.",
                ));
            }
            for item in arr {
                json_validate(item, seen)?;
            }
            seen.remove(&id);
            Ok(())
        }
        Value::Object(map) => {
            let id = v as *const _ as usize;
            if !seen.insert(id) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions must contain acyclic JSON data.",
                ));
            }
            for (k, vv) in map {
                if k.is_empty() {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "providerOptions must contain plain JSON data.",
                    ));
                }
                json_validate(vv, seen)?;
            }
            seen.remove(&id);
            Ok(())
        }
    }
}
#[allow(clippy::result_large_err)]
fn validate_tools(tools: &Option<Vec<ToolDefinition>>) -> Result<(), ConduitError> {
    if let Some(ts) = tools {
        if ts.is_empty() {
            return Err(ConduitError::new(
                "InvalidRequestError",
                "tools must be a nonempty array.",
            ));
        }
        let mut names = HashSet::new();
        for t in ts {
            if t.name.trim().is_empty() {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "Tool name must be a nonempty string.",
                ));
            }
            if !names.insert(t.name.clone()) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "Tool names must be unique.",
                ));
            }
            let mut seen = HashSet::new();
            json_validate(&t.input_schema, &mut seen)?;
        }
    }
    Ok(())
}
#[allow(clippy::result_large_err)]
fn validate_tool_choice(
    choice: &Option<ToolChoice>,
    tools: &Option<Vec<ToolDefinition>>,
) -> Result<(), ConduitError> {
    match choice {
        None => Ok(()),
        Some(ToolChoice::Auto) | Some(ToolChoice::None_) | Some(ToolChoice::Required) => Ok(()),
        Some(ToolChoice::Named(n)) => {
            if n.trim().is_empty() {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "toolChoice name must be a nonempty string.",
                ));
            }
            if let Some(ts) = tools {
                if ts.iter().any(|t| &t.name == n) {
                    return Ok(());
                }
            }
            Err(ConduitError::new(
                "InvalidRequestError",
                "toolChoice name must match a supplied tool.",
            ))
        }
    }
}
#[allow(clippy::result_large_err)]
fn validate_response_format(v: &Option<ResponseFormat>) -> Result<(), ConduitError> {
    if let Some(rf) = v {
        match rf {
            ResponseFormat::Text | ResponseFormat::Json => Ok(()),
            ResponseFormat::JsonSchema { schema } => {
                let mut seen = HashSet::new();
                json_validate(schema, &mut seen)?;
                Ok(())
            }
        }
    } else {
        Ok(())
    }
}
#[allow(dead_code)]
fn text_response(_fields: serde_json::Map<String, Value>) -> GenerationResponse {
    GenerationResponse {
        id: None,
        model: None,
        content: vec![],
        finish_reason: "stop".to_string(),
        usage: None,
        provider_metadata: HashMap::new(),
    }
}
// ---------------------------------------------------------------------------
// HTTP — whole-operation deadline
// ---------------------------------------------------------------------------
fn ureq_transport_error(error: ureq::Error, deadline: Option<Instant>) -> ConduitError {
    let timed_out = match &error {
        ureq::Error::Timeout(_) => true,
        ureq::Error::Io(error) => error.kind() == std::io::ErrorKind::TimedOut,
        _ => false,
    };
    if timed_out && deadline.is_some() {
        return ConduitError::new("TimeoutError", "Request deadline exceeded.");
    }
    let mut cause = HashMap::new();
    cause.insert("name".to_string(), "TransportError".to_string());
    cause.insert("message".to_string(), "[REDACTED]".to_string());
    ConduitError::new("ConnectionError", "Provider connection failed.").with_cause(cause)
}

fn stream_read_error(error: std::io::Error, deadline: Option<Instant>) -> ConduitError {
    if error.kind() == std::io::ErrorKind::TimedOut || deadline.is_some_and(|d| Instant::now() >= d)
    {
        ConduitError::new("TimeoutError", "Request deadline exceeded.")
    } else {
        ConduitError::new("ConnectionError", "Provider connection failed.")
    }
}
#[allow(
    unused_variables,
    clippy::type_complexity,
    clippy::collapsible_if,
    clippy::result_large_err
)]
fn do_http(
    method: &str,
    url: &str,
    headers: &HashMap<String, String>,
    body: Option<&str>,
    timeout: Option<u32>,
) -> Result<(u16, HashMap<String, String>, Vec<u8>), ConduitError> {
    let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
    if let Some(d) = deadline {
        if Instant::now() >= d {
            return Err(ConduitError::new(
                "TimeoutError",
                "Request deadline exceeded.",
            ));
        }
    }
    let remaining = deadline
        .map(|d| d.saturating_duration_since(Instant::now()))
        .unwrap_or(Duration::from_secs(30));
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(remaining))
        .http_status_as_error(false)
        .build()
        .into();
    let mut builder = ureq::http::Request::builder().method(method).uri(url);
    for (k, v) in headers {
        builder = builder.header(k, v);
    }
    let resp = if let Some(b) = body {
        let req = builder
            .body(b.to_string())
            .map_err(|e| ureq_transport_error(ureq::Error::Http(e), deadline))?;
        agent.run(req)
    } else {
        let req = builder
            .body(())
            .map_err(|e| ureq_transport_error(ureq::Error::Http(e), deadline))?;
        agent.run(req)
    }
    .map_err(|e| ureq_transport_error(e, deadline))?;
    let status = resp.status().as_u16();
    let mut hmap = HashMap::new();
    for (k, v) in resp.headers() {
        if let Ok(v) = v.to_str() {
            hmap.insert(k.as_str().to_lowercase(), v.to_string());
        }
    }
    let mut buf = Vec::new();
    let mut reader = resp.into_body().into_reader();
    let mut tmp = [0u8; 8192];
    loop {
        if let Some(d) = deadline {
            if Instant::now() >= d {
                return Err(ConduitError::new(
                    "TimeoutError",
                    "Request deadline exceeded.",
                ));
            }
        }
        let n = reader.read(&mut tmp).unwrap_or(0);
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
    }
    Ok((status, hmap, buf))
}

// ---------------------------------------------------------------------------
// Client / Model — real four-driver implementation (minimal, no fabricated IDs)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Anthropic driver
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Gemini driver
// ---------------------------------------------------------------------------

pub struct Client {
    driver: Driver,
    endpoint: String,
    url: String,
    list_url: String,
    headers: HashMap<String, String>,
    redact: Arc<dyn Fn(&str) -> String + Send + Sync>,
    default_timeout: Option<u32>,
}
pub struct Model {
    client: Arc<Client>,
    model_id: String,
}

#[allow(clippy::result_large_err)]
pub fn connect(mut config: ClientConfig) -> Result<Arc<Client>, ConduitError> {
    let driver = Driver::from_str(&config.driver)
        .ok_or_else(|| ConduitError::new("InvalidRequestError", "Unknown driver."))?;
    if config.endpoint.trim().is_empty() {
        match driver {
            Driver::Ollama => config.endpoint = "http://localhost:11434".into(),
            Driver::Anthropic => config.endpoint = "https://api.anthropic.com".into(),
            Driver::Gemini => config.endpoint = "https://generativelanguage.googleapis.com".into(),
            Driver::OpenAICompatible => {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "endpoint must be an HTTP(S) API base URL.",
                ));
            }
        }
    }
    timeout_value(config.timeout)?;
    // Basic endpoint validation: must be http/https, no userinfo, no query/fragment
    if !(config.endpoint.starts_with("http://") || config.endpoint.starts_with("https://")) {
        return Err(ConduitError::new(
            "InvalidRequestError",
            "endpoint must be an HTTP(S) API base URL.",
        ));
    }
    if config.endpoint.contains('@')
        || config.endpoint.contains('?')
        || config.endpoint.contains('#')
    {
        return Err(ConduitError::new(
            "InvalidRequestError",
            "endpoint must be an HTTP(S) API base URL.",
        ));
    }
    let secrets: Vec<String> = config.credentials.clone().into_iter().collect();
    let redact = make_redactor(&secrets);
    let mut headers = HashMap::new();
    headers.insert("content-type".to_string(), "application/json".to_string());
    if let Some(hs) = config.headers {
        for (k, v) in hs {
            let lk = k.to_lowercase();
            if [
                "authorization",
                "x-api-key",
                "x-goog-api-key",
                "anthropic-version",
            ]
            .contains(&lk.as_str())
            {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions conflicts with a Conduit-owned field.",
                ));
            }
            headers.insert(k, v);
        }
    }
    match driver {
        Driver::Anthropic => {
            headers.insert("anthropic-version".to_string(), "2023-06-01".to_string());
            if let Some(c) = &config.credentials {
                headers.insert("x-api-key".to_string(), c.clone());
            }
        }
        Driver::Gemini => {
            if let Some(c) = &config.credentials {
                headers.insert("x-goog-api-key".to_string(), c.clone());
            }
        }
        _ => {
            if let Some(c) = &config.credentials {
                headers.insert("authorization".to_string(), format!("Bearer {}", c));
            }
        }
    }
    let base = config.endpoint.trim_end_matches('/');
    let (url, list_url) = match driver {
        Driver::OpenAICompatible => (
            format!("{}/chat/completions", base),
            format!("{}/models", base),
        ),
        Driver::Ollama => (format!("{}/api/chat", base), format!("{}/api/tags", base)),
        Driver::Anthropic => (
            format!("{}/v1/messages", base),
            format!("{}/v1/models", base),
        ),
        Driver::Gemini => (
            format!("{}/v1beta/models", base),
            format!("{}/v1beta/models", base),
        ),
    };
    Ok(Arc::new(Client {
        driver,
        endpoint: config.endpoint,
        url,
        list_url,
        headers,
        redact,
        default_timeout: config.timeout,
    }))
}

impl Client {
    #[allow(clippy::result_large_err)]
    pub fn model(self: &Arc<Self>, id: &str) -> Result<Model, ConduitError> {
        if id.trim().is_empty() {
            return Err(ConduitError::new(
                "InvalidRequestError",
                "model must be a nonempty string.",
            ));
        }
        Ok(Model {
            client: Arc::clone(self),
            model_id: id.to_string(),
        })
    }
    #[allow(
        clippy::result_large_err,
        clippy::manual_strip,
        clippy::collapsible_if,
        unused_variables
    )]
    pub fn list_models(
        &self,
        opts: Option<ListModelsOptions>,
    ) -> Result<Vec<ModelInfo>, ConduitError> {
        let _ = &self.endpoint;
        let _ = &self.url;
        if let Some(sig) = opts.as_ref().and_then(|o| o.signal.clone()) {
            if sig.load(std::sync::atomic::Ordering::SeqCst) {
                return Err(ConduitError::new(
                    "CancelledError",
                    "Request cancelled by caller.",
                ));
            }
        }
        let timeout = opts
            .as_ref()
            .and_then(|o| o.timeout)
            .or(self.default_timeout);
        timeout_value(timeout)?;
        let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
        match self.driver {
            Driver::OpenAICompatible => {
                let (status, headers, body) =
                    do_http("GET", &self.list_url, &self.headers, None, timeout)?;
                let rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.redact)(&v));
                if !(200..300).contains(&status) {
                    let text = String::from_utf8_lossy(&body).to_string();
                    let mut details = HashMap::new();
                    details.insert("message".to_string(), text);
                    return Err(http_failure(status, details, rid, false));
                }
                let v: Value = serde_json::from_slice(&body).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                let data = v.get("data").and_then(|d| d.as_array()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported model listing response.",
                    )
                })?;
                normalize_openai_models(data, &*self.redact)
            }
            Driver::Ollama => {
                let (status, headers, body) =
                    do_http("GET", &self.list_url, &self.headers, None, timeout)?;
                let _rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.redact)(&v));
                if !(200..300).contains(&status) {
                    let _text = String::from_utf8_lossy(&body).to_string();
                    return Err(ConduitError::new(
                        "ProviderError",
                        &format!("Provider returned HTTP {}.", status),
                    )
                    .with_status(status));
                }
                let v: Value = serde_json::from_slice(&body).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                let models = v.get("models").and_then(|m| m.as_array()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported model listing response.",
                    )
                })?;
                let mut out = Vec::new();
                for entry in models {
                    let id = entry
                        .get("name")
                        .and_then(|v| v.as_str())
                        .or(entry.get("model").and_then(|v| v.as_str()))
                        .ok_or_else(|| {
                            ConduitError::new(
                                "ProtocolError",
                                "Malformed or unsupported model listing response.",
                            )
                        })?
                        .to_string();
                    out.push(ModelInfo {
                        id: (self.redact)(&id),
                        name: None,
                        provider_metadata: None,
                    });
                }
                Ok(out)
            }
            Driver::Anthropic => {
                let mut all = Vec::new();
                let mut cursor: Option<String> = None;
                let mut seen = HashSet::new();
                for _ in 0..100 {
                    if let Some(d) = deadline {
                        if Instant::now() >= d {
                            return Err(ConduitError::new(
                                "TimeoutError",
                                "Request deadline exceeded.",
                            ));
                        }
                    }
                    let url = if let Some(c) = &cursor {
                        format!("{}?after_id={}", self.list_url, urlencoding(c))
                    } else {
                        self.list_url.clone()
                    };
                    if let Some(c) = &cursor {
                        if !seen.insert(c.clone()) {
                            return Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed pagination cursor.",
                            ));
                        }
                    }
                    let rem = deadline
                        .map(|d| d.saturating_duration_since(Instant::now()).as_millis() as u32);
                    let (status, headers, body) = do_http("GET", &url, &self.headers, None, rem)?;
                    let rid = headers
                        .get("x-request-id")
                        .or(headers.get("request-id"))
                        .cloned()
                        .map(|v| (self.redact)(&v));
                    if !(200..300).contains(&status) {
                        let text = String::from_utf8_lossy(&body).to_string();
                        let mut details = HashMap::new();
                        details.insert("message".to_string(), text);
                        return Err(http_failure(status, details, rid, false));
                    }
                    let v: Value = serde_json::from_slice(&body).map_err(|_| {
                        ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                    })?;
                    let data = v.get("data").and_then(|d| d.as_array()).ok_or_else(|| {
                        ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported model listing response.",
                        )
                    })?;
                    let has_more = v.get("has_more").and_then(|h| h.as_bool()).unwrap_or(false);
                    let last_id = v
                        .get("last_id")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string());
                    for entry in data {
                        let id = entry
                            .get("id")
                            .and_then(|v| v.as_str())
                            .ok_or_else(|| {
                                ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or unsupported model listing response.",
                                )
                            })?
                            .to_string();
                        all.push(ModelInfo {
                            id: (self.redact)(&id),
                            name: None,
                            provider_metadata: None,
                        });
                    }
                    if !has_more {
                        return Ok(all);
                    }
                    let nxt = last_id.ok_or_else(|| {
                        ConduitError::new(
                            "ProtocolError",
                            "Malformed pagination: last_id required when has_more true.",
                        )
                    })?;
                    if seen.contains(&nxt) {
                        return Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed pagination: cursor cycle.",
                        ));
                    }
                    cursor = Some(nxt);
                }
                Err(ConduitError::new(
                    "ProtocolError",
                    "Too many pagination pages.",
                ))
            }
            Driver::Gemini => {
                let mut all = Vec::new();
                let mut token: Option<String> = None;
                let mut seen = HashSet::new();
                for _ in 0..100 {
                    if let Some(d) = deadline {
                        if Instant::now() >= d {
                            return Err(ConduitError::new(
                                "TimeoutError",
                                "Request deadline exceeded.",
                            ));
                        }
                    }
                    let url = if let Some(t) = &token {
                        format!("{}?pageToken={}", self.list_url, urlencoding(t))
                    } else {
                        self.list_url.clone()
                    };
                    if let Some(t) = &token {
                        if !seen.insert(t.clone()) {
                            return Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed pagination token.",
                            ));
                        }
                    }
                    let rem = deadline
                        .map(|d| d.saturating_duration_since(Instant::now()).as_millis() as u32);
                    let (status, headers, body) = do_http("GET", &url, &self.headers, None, rem)?;
                    let rid = headers
                        .get("x-request-id")
                        .or(headers.get("request-id"))
                        .cloned()
                        .map(|v| (self.redact)(&v));
                    if !(200..300).contains(&status) {
                        let text = String::from_utf8_lossy(&body).to_string();
                        let mut details = HashMap::new();
                        details.insert("message".to_string(), text);
                        return Err(http_failure(status, details, rid, false));
                    }
                    let v: Value = serde_json::from_slice(&body).map_err(|_| {
                        ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                    })?;
                    let models = v.get("models").and_then(|m| m.as_array()).ok_or_else(|| {
                        ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported model listing response.",
                        )
                    })?;
                    for entry in models {
                        let name = entry
                            .get("name")
                            .and_then(|v| v.as_str())
                            .ok_or_else(|| {
                                ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or unsupported model listing response.",
                                )
                            })?
                            .to_string();
                        let id = if name.starts_with("models/") {
                            name["models/".len()..].to_string()
                        } else {
                            name.clone()
                        };
                        all.push(ModelInfo {
                            id: (self.redact)(&id),
                            name: None,
                            provider_metadata: None,
                        });
                    }
                    let nxt = v
                        .get("nextPageToken")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string());
                    if nxt.is_none() || nxt.as_ref().map(|s| s.is_empty()).unwrap_or(false) {
                        return Ok(all);
                    }
                    let nt = nxt.unwrap();
                    if seen.contains(&nt) {
                        return Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed pagination: token cycle.",
                        ));
                    }
                    token = Some(nt);
                }
                Err(ConduitError::new(
                    "ProtocolError",
                    "Too many pagination pages.",
                ))
            }
        }
    }
}

impl Model {
    #[allow(clippy::result_large_err)]
    #[allow(clippy::result_large_err)]
    pub fn generate(
        &self,
        req: impl Into<GenerationRequest>,
    ) -> Result<GenerationResponse, ConduitError> {
        let req = req.into();
        if is_aborted(&req.signal) {
            return Err(ConduitError::new(
                "CancelledError",
                "Request cancelled by caller.",
            ));
        }
        timeout_value(req.timeout)?;
        validate_tools(&req.tools)?;
        validate_tool_choice(&req.tool_choice, &req.tools)?;
        validate_response_format(&req.response_format)?;
        if let Some(po) = &req.provider_options {
            let owned: &[&str] = match self.client.driver {
                Driver::OpenAICompatible => &[
                    "model",
                    "messages",
                    "stream",
                    "max_tokens",
                    "temperature",
                    "top_p",
                    "stop",
                    "tools",
                    "tool_choice",
                    "response_format",
                ],
                Driver::Ollama => &["model", "messages", "stream", "tools", "format"],
                Driver::Anthropic => &[
                    "model",
                    "messages",
                    "system",
                    "stream",
                    "max_tokens",
                    "temperature",
                    "top_p",
                    "stop_sequences",
                    "tools",
                    "tool_choice",
                ],
                Driver::Gemini => &[
                    "contents",
                    "systemInstruction",
                    "generationConfig",
                    "tools",
                    "toolConfig",
                ],
            };
            for k in po.keys() {
                if owned.contains(&k.as_str()) {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "providerOptions conflicts with a Conduit-owned field.",
                    ));
                }
            }
        }
        match self.client.driver {
            Driver::OpenAICompatible => {
                let body_val = encode_openai_request(&self.model_id, &req, false)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let (status, headers, body_bytes) = do_http(
                    "POST",
                    &self.client.url,
                    &self.client.headers,
                    Some(&body_str),
                    timeout,
                )?;
                let rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(&v));
                if !(200..300).contains(&status) {
                    let text = String::from_utf8_lossy(&body_bytes).to_string();
                    return Err(http_error_openai(status, &text, rid, &*self.client.redact));
                }
                let v: Value = serde_json::from_slice(&body_bytes).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                decode_openai(v, rid, &*self.client.redact)
            }
            Driver::Anthropic => {
                // Anthropic requires max_output_tokens; 0 is valid
                let body_val = encode_anthropic_request(&self.model_id, &req, false)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let (status, headers, body_bytes) = do_http(
                    "POST",
                    &self.client.url,
                    &self.client.headers,
                    Some(&body_str),
                    timeout,
                )?;
                let rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(&v));
                if !(200..300).contains(&status) {
                    let text = String::from_utf8_lossy(&body_bytes).to_string();
                    return Err(anthropic_error(status, &text, rid, &*self.client.redact));
                }
                let v: Value = serde_json::from_slice(&body_bytes).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                decode_anthropic(v, rid, &*self.client.redact)
            }
            Driver::Gemini => {
                let body_val = encode_gemini_request(&self.model_id, &req, false)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let fetch_url = format!(
                    "{}/{}:generateContent",
                    self.client.url.trim_end_matches('/'),
                    urlencoding(&self.model_id)
                );
                let (status, headers, body_bytes) = do_http(
                    "POST",
                    &fetch_url,
                    &self.client.headers,
                    Some(&body_str),
                    timeout,
                )?;
                let rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(&v));
                if !(200..300).contains(&status) {
                    let text = String::from_utf8_lossy(&body_bytes).to_string();
                    return Err(gemini_error(status, &text, rid, &*self.client.redact));
                }
                let v: Value = serde_json::from_slice(&body_bytes).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                decode_gemini(v, rid, &*self.client.redact)
            }
            Driver::Ollama => {
                let body_val = encode_ollama_request(&self.model_id, &req, false)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let (status, headers, body_bytes) = do_http(
                    "POST",
                    &self.client.url,
                    &self.client.headers,
                    Some(&body_str),
                    timeout,
                )?;
                let rid = headers
                    .get("x-request-id")
                    .or(headers.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(&v));
                if !(200..300).contains(&status) {
                    let text = String::from_utf8_lossy(&body_bytes).to_string();
                    return Err(ollama_error(
                        status,
                        &text,
                        rid,
                        &*self.client.redact,
                        Some(&self.model_id),
                    ));
                }
                let v: Value = serde_json::from_slice(&body_bytes).map_err(|_| {
                    ConduitError::new("ProtocolError", "Provider returned invalid JSON.")
                })?;
                decode_ollama(v, rid, &*self.client.redact)
            }
        }
    }

    #[allow(clippy::result_large_err)]
    pub fn stream(
        &self,
        req: impl Into<GenerationRequest>,
    ) -> Result<Box<dyn Iterator<Item = Result<StreamEvent, ConduitError>> + Send>, ConduitError>
    {
        let req = req.into();
        if is_aborted(&req.signal) {
            return Err(ConduitError::new(
                "CancelledError",
                "Request cancelled by caller.",
            ));
        }
        timeout_value(req.timeout)?;
        validate_tools(&req.tools)?;
        validate_tool_choice(&req.tool_choice, &req.tools)?;
        validate_response_format(&req.response_format)?;
        if let Some(po) = &req.provider_options {
            let owned: &[&str] = match self.client.driver {
                Driver::OpenAICompatible => &[
                    "model",
                    "messages",
                    "stream",
                    "max_tokens",
                    "temperature",
                    "top_p",
                    "stop",
                    "tools",
                    "tool_choice",
                    "response_format",
                ],
                Driver::Ollama => &["model", "messages", "stream", "tools", "format"],
                Driver::Anthropic => &[
                    "model",
                    "messages",
                    "system",
                    "stream",
                    "max_tokens",
                    "temperature",
                    "top_p",
                    "stop_sequences",
                    "tools",
                    "tool_choice",
                ],
                Driver::Gemini => &[
                    "contents",
                    "systemInstruction",
                    "generationConfig",
                    "tools",
                    "toolConfig",
                ],
            };
            for k in po.keys() {
                if owned.contains(&k.as_str()) {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "providerOptions conflicts with a Conduit-owned field.",
                    ));
                }
            }
        }
        match self.client.driver {
            Driver::OpenAICompatible => {
                let body_val = encode_openai_request(&self.model_id, &req, true)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
                if is_aborted(&req.signal) {
                    return Err(ConduitError::new(
                        "CancelledError",
                        "Request cancelled by caller.",
                    ));
                }
                let remaining = deadline
                    .map(|d| d.saturating_duration_since(Instant::now()))
                    .unwrap_or(Duration::from_secs(30));
                let agent: ureq::Agent = ureq::Agent::config_builder()
                    .timeout_global(Some(remaining))
                    .http_status_as_error(false)
                    .build()
                    .into();
                let mut ureq_req = agent.post(&self.client.url);
                for (k, v) in &self.client.headers {
                    ureq_req = ureq_req.header(k, v);
                }
                let resp = ureq_req
                    .send(&body_str)
                    .map_err(|e| ureq_transport_error(e, deadline))?;
                let status = resp.status().as_u16();
                let mut hmap = HashMap::new();
                for (k, v) in resp.headers() {
                    if let Ok(v) = v.to_str() {
                        hmap.insert(k.as_str().to_lowercase(), v.to_string());
                    }
                }
                let rid = hmap
                    .get("x-request-id")
                    .or(hmap.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(v.as_str()));
                if !(200..300).contains(&status) {
                    let mut buf = Vec::new();
                    let mut r = resp.into_body().into_reader();
                    let _ = r.read_to_end(&mut buf);
                    let text = String::from_utf8_lossy(&buf).to_string();
                    return Err(http_error_openai(status, &text, rid, &*self.client.redact));
                }
                let ctype = hmap
                    .get("content-type")
                    .map(|s| s.split(';').next().unwrap_or("").trim().to_lowercase())
                    .unwrap_or_default();
                if ctype != "text/event-stream" {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Expected a text/event-stream response.",
                    ));
                }
                let reader = resp.into_body().into_reader();
                let red = self.client.redact.clone();
                let rid2 = rid.clone();
                let buf_reader = Box::new(BufReader::new(reader)) as Box<dyn BufRead + Send>;
                let stream = openai_sse_stream(buf_reader, rid2, red, deadline, req.signal.clone());
                Ok(Box::new(stream))
            }
            Driver::Anthropic => {
                let body_val = encode_anthropic_request(&self.model_id, &req, true)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
                if is_aborted(&req.signal) {
                    return Err(ConduitError::new(
                        "CancelledError",
                        "Request cancelled by caller.",
                    ));
                }
                let remaining = deadline
                    .map(|d| d.saturating_duration_since(Instant::now()))
                    .unwrap_or(Duration::from_secs(30));
                let agent: ureq::Agent = ureq::Agent::config_builder()
                    .timeout_global(Some(remaining))
                    .http_status_as_error(false)
                    .build()
                    .into();
                let mut ureq_req = agent.post(&self.client.url);
                for (k, v) in &self.client.headers {
                    ureq_req = ureq_req.header(k, v);
                }
                let resp = ureq_req
                    .send(&body_str)
                    .map_err(|e| ureq_transport_error(e, deadline))?;
                let status = resp.status().as_u16();
                let mut hmap = HashMap::new();
                for (k, v) in resp.headers() {
                    if let Ok(v) = v.to_str() {
                        hmap.insert(k.as_str().to_lowercase(), v.to_string());
                    }
                }
                let rid = hmap
                    .get("x-request-id")
                    .or(hmap.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(v.as_str()));
                if !(200..300).contains(&status) {
                    let mut buf = Vec::new();
                    let mut r = resp.into_body().into_reader();
                    let _ = r.read_to_end(&mut buf);
                    let text = String::from_utf8_lossy(&buf).to_string();
                    return Err(anthropic_error(status, &text, rid, &*self.client.redact));
                }
                let ctype = hmap
                    .get("content-type")
                    .map(|s| s.split(';').next().unwrap_or("").trim().to_lowercase())
                    .unwrap_or_default();
                if ctype != "text/event-stream" {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Expected a text/event-stream response.",
                    ));
                }
                let reader = resp.into_body().into_reader();
                let red = self.client.redact.clone();
                let rid2 = rid.clone();
                let buf_reader = Box::new(BufReader::new(reader)) as Box<dyn BufRead + Send>;
                let stream =
                    anthropic_sse_stream(buf_reader, rid2, red, deadline, req.signal.clone());
                Ok(Box::new(stream))
            }
            Driver::Gemini => {
                let body_val = encode_gemini_request(&self.model_id, &req, true)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
                if is_aborted(&req.signal) {
                    return Err(ConduitError::new(
                        "CancelledError",
                        "Request cancelled by caller.",
                    ));
                }
                let remaining = deadline
                    .map(|d| d.saturating_duration_since(Instant::now()))
                    .unwrap_or(Duration::from_secs(30));
                let agent: ureq::Agent = ureq::Agent::config_builder()
                    .timeout_global(Some(remaining))
                    .http_status_as_error(false)
                    .build()
                    .into();
                let fetch_url = format!(
                    "{}/{}:streamGenerateContent?alt=sse",
                    self.client.url.trim_end_matches('/'),
                    urlencoding(&self.model_id)
                );
                let mut ureq_req = agent.post(&fetch_url);
                for (k, v) in &self.client.headers {
                    ureq_req = ureq_req.header(k, v);
                }
                let resp = ureq_req
                    .send(&body_str)
                    .map_err(|e| ureq_transport_error(e, deadline))?;
                let status = resp.status().as_u16();
                let mut hmap = HashMap::new();
                for (k, v) in resp.headers() {
                    if let Ok(v) = v.to_str() {
                        hmap.insert(k.as_str().to_lowercase(), v.to_string());
                    }
                }
                let rid = hmap
                    .get("x-request-id")
                    .or(hmap.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(v.as_str()));
                if !(200..300).contains(&status) {
                    let mut buf = Vec::new();
                    let mut r = resp.into_body().into_reader();
                    let _ = r.read_to_end(&mut buf);
                    let text = String::from_utf8_lossy(&buf).to_string();
                    return Err(gemini_error(status, &text, rid, &*self.client.redact));
                }
                let ctype = hmap
                    .get("content-type")
                    .map(|s| s.split(';').next().unwrap_or("").trim().to_lowercase())
                    .unwrap_or_default();
                if ctype != "text/event-stream" {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Expected a text/event-stream response.",
                    ));
                }
                let reader = resp.into_body().into_reader();
                let red = self.client.redact.clone();
                let rid2 = rid.clone();
                let buf_reader = Box::new(BufReader::new(reader)) as Box<dyn BufRead + Send>;
                let stream = gemini_sse_stream(buf_reader, rid2, red, deadline, req.signal.clone());
                Ok(Box::new(stream))
            }
            Driver::Ollama => {
                let body_val = encode_ollama_request(&self.model_id, &req, true)?;
                let body_str = serde_json::to_string(&body_val).unwrap();
                let timeout = req.timeout.or(self.client.default_timeout);
                let deadline = timeout.map(|ms| Instant::now() + Duration::from_millis(ms as u64));
                if is_aborted(&req.signal) {
                    return Err(ConduitError::new(
                        "CancelledError",
                        "Request cancelled by caller.",
                    ));
                }
                let remaining = deadline
                    .map(|d| d.saturating_duration_since(Instant::now()))
                    .unwrap_or(Duration::from_secs(30));
                let agent: ureq::Agent = ureq::Agent::config_builder()
                    .timeout_global(Some(remaining))
                    .http_status_as_error(false)
                    .build()
                    .into();
                let mut ureq_req = agent.post(&self.client.url);
                for (k, v) in &self.client.headers {
                    ureq_req = ureq_req.header(k, v);
                }
                let resp = ureq_req
                    .send(&body_str)
                    .map_err(|e| ureq_transport_error(e, deadline))?;
                let status = resp.status().as_u16();
                let mut hmap = HashMap::new();
                for (k, v) in resp.headers() {
                    if let Ok(v) = v.to_str() {
                        hmap.insert(k.as_str().to_lowercase(), v.to_string());
                    }
                }
                let rid = hmap
                    .get("x-request-id")
                    .or(hmap.get("request-id"))
                    .cloned()
                    .map(|v| (self.client.redact)(v.as_str()));
                if !(200..300).contains(&status) {
                    let mut buf = Vec::new();
                    let mut r = resp.into_body().into_reader();
                    let _ = r.read_to_end(&mut buf);
                    let text = String::from_utf8_lossy(&buf).to_string();
                    return Err(ollama_error(
                        status,
                        &text,
                        rid,
                        &*self.client.redact,
                        Some(&self.model_id),
                    ));
                }
                let reader = resp.into_body().into_reader();
                let red = self.client.redact.clone();
                let rid2 = rid.clone();
                let buf_reader = Box::new(BufReader::new(reader)) as Box<dyn BufRead + Send>;
                let stream =
                    ollama_ndjson_stream(buf_reader, rid2, red, deadline, req.signal.clone());
                Ok(Box::new(stream))
            }
        }
    }
}

#[cfg(test)]
mod tests;
