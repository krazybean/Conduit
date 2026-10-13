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
pub(crate) use std::io::{BufRead, BufReader};
use std::sync::{atomic::AtomicBool, Arc};
pub(crate) use std::time::Instant;

use serde_json::Value;

mod errors;
mod types;
pub use errors::*;
pub use types::*;

mod client;
mod providers;
mod transport;

pub use client::{anthropic, connect, gemini, ollama, Client, Model};
#[cfg(test)]
pub(crate) use providers::anthropic::{decode_anthropic, encode_anthropic_request};
#[cfg(test)]
pub(crate) use providers::gemini::{decode_gemini, encode_gemini_request};
#[cfg(test)]
pub(crate) use providers::openai::{
    decode_openai, encode_openai_request, normalize_openai_models, openai_sse_stream,
};
pub(crate) use transport::stream_read_error;

// Minimal helpers for compilation
// ---------------------------------------------------------------------------
pub(crate) fn make_redactor(secrets: &[String]) -> Arc<dyn Fn(&str) -> String + Send + Sync> {
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
pub(crate) fn urlencoding(s: &str) -> String {
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
pub(crate) fn timeout_value(v: Option<u32>) -> Result<(), ConduitError> {
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
pub(crate) fn is_aborted(sig: &Option<Arc<AtomicBool>>) -> bool {
    sig.as_ref()
        .map(|a| a.load(std::sync::atomic::Ordering::SeqCst))
        .unwrap_or(false)
}
#[allow(clippy::result_large_err)]
pub(crate) fn json_validate(v: &Value, seen: &mut HashSet<usize>) -> Result<(), ConduitError> {
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
pub(crate) fn validate_tools(tools: &Option<Vec<ToolDefinition>>) -> Result<(), ConduitError> {
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
pub(crate) fn validate_tool_choice(
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
pub(crate) fn validate_response_format(v: &Option<ResponseFormat>) -> Result<(), ConduitError> {
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

#[cfg(test)]
mod tests;
