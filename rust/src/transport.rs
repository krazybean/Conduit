use std::collections::HashMap;
use std::io::Read;
use std::time::{Duration, Instant};

use crate::ConduitError;

// HTTP — whole-operation deadline
// ---------------------------------------------------------------------------
pub(crate) fn ureq_transport_error(error: ureq::Error, deadline: Option<Instant>) -> ConduitError {
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

pub(crate) fn stream_read_error(error: std::io::Error, deadline: Option<Instant>) -> ConduitError {
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
pub(crate) fn do_http(
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
