use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct ConduitError {
    pub name: String,
    pub message: String,
    pub status_code: Option<u16>,
    pub provider_code: Option<String>,
    pub request_id: Option<String>,
    pub provider_details: Option<HashMap<String, String>>,
    pub cause: Option<HashMap<String, String>>,
}

impl ConduitError {
    pub fn new(name: &str, message: &str) -> Self {
        Self {
            name: name.to_string(),
            message: message.to_string(),
            status_code: None,
            provider_code: None,
            request_id: None,
            provider_details: None,
            cause: None,
        }
    }
    pub fn with_status(mut self, status: u16) -> Self {
        self.status_code = Some(status);
        self
    }
    pub fn with_provider_code(mut self, code: String) -> Self {
        self.provider_code = Some(code);
        self
    }
    pub fn with_request_id(mut self, id: String) -> Self {
        self.request_id = Some(id);
        self
    }
    pub fn with_details(mut self, d: HashMap<String, String>) -> Self {
        self.provider_details = Some(d);
        self
    }
    pub fn with_cause(mut self, c: HashMap<String, String>) -> Self {
        self.cause = Some(c);
        self
    }
}

impl std::fmt::Display for ConduitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.name, self.message)
    }
}
impl std::error::Error for ConduitError {}

#[allow(dead_code)]
pub(crate) fn invalid(msg: &str) -> ConduitError {
    ConduitError::new("InvalidRequestError", msg)
}
#[allow(dead_code)]
pub(crate) fn protocol(msg: &str) -> ConduitError {
    ConduitError::new("ProtocolError", msg)
}
#[allow(dead_code)]
pub(crate) fn http_failure(
    status: u16,
    details: HashMap<String, String>,
    request_id: Option<String>,
    model_not_found: bool,
) -> ConduitError {
    let name = if status == 404 && model_not_found {
        "ModelNotFoundError"
    } else {
        match status {
            400 => "InvalidRequestError",
            401 => "AuthenticationError",
            403 => "AuthorizationError",
            408 => "TimeoutError",
            422 => "InvalidRequestError",
            429 => "RateLimitError",
            _ => "ProviderError",
        }
    };
    let msg = details
        .get("message")
        .cloned()
        .unwrap_or_else(|| format!("Provider returned HTTP {}.", status));
    let mut e = ConduitError::new(name, &msg).with_status(status);
    if let Some(rid) = request_id {
        e = e.with_request_id(rid);
    }
    if let Some(code) = details.get("code").cloned() {
        e = e.with_provider_code(code);
    }
    if !details.is_empty() {
        e = e.with_details(details);
    }
    e
}
