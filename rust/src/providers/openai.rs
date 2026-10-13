use crate::*;
use serde_json::Value;

pub(crate) fn normalize_openai_models(
    data: &[Value],
    redact: &dyn Fn(&str) -> String,
) -> Result<Vec<ModelInfo>, ConduitError> {
    let mut out = Vec::new();
    for entry in data {
        let obj = entry.as_object().ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported model listing response.",
            )
        })?;
        let id = obj.get("id").and_then(|v| v.as_str()).ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported model listing response.",
            )
        })?;
        let mut provider_metadata = HashMap::new();
        for (key, value) in obj {
            if key != "id" {
                provider_metadata.insert(key.clone(), value.clone());
            }
        }
        out.push(ModelInfo {
            id: redact(id),
            name: None,
            provider_metadata: (!provider_metadata.is_empty()).then_some(provider_metadata),
        });
    }
    Ok(out)
}

pub(crate) fn encode_openai_request(
    model: &str,
    req: &GenerationRequest,
    streaming: bool,
) -> Result<Value, ConduitError> {
    let mut body = serde_json::Map::new();
    body.insert("model".to_string(), Value::String(model.to_string()));
    // messages — preserve array form for text, handle tool cases minimally for tests
    let mut msgs = Vec::new();
    for m in &req.messages {
        if m.role == "tool" {
            // tool result
            let tr = m
                .content
                .iter()
                .find_map(|p| {
                    if let ContentPart::ToolResult(r) = p {
                        Some(r)
                    } else {
                        None
                    }
                })
                .ok_or_else(|| {
                    ConduitError::new(
                        "InvalidRequestError",
                        "Tool message must contain tool_result.",
                    )
                })?;
            let mut obj = serde_json::Map::new();
            obj.insert("role".to_string(), Value::String("tool".to_string()));
            obj.insert("content".to_string(), Value::String(tr.content.clone()));
            if let Some(id) = &tr.call_id {
                obj.insert("tool_call_id".to_string(), Value::String(id.clone()));
            }
            msgs.push(Value::Object(obj));
        } else {
            let tool_calls: Vec<&ToolCallPart> = m
                .content
                .iter()
                .filter_map(|p| {
                    if let ContentPart::ToolCall(tc) = p {
                        Some(tc)
                    } else {
                        None
                    }
                })
                .collect();
            if !tool_calls.is_empty() {
                let texts: String = m
                    .content
                    .iter()
                    .filter_map(|p| {
                        if let ContentPart::Text(t) = p {
                            Some(t.text.clone())
                        } else {
                            None
                        }
                    })
                    .collect();
                let mut obj = serde_json::Map::new();
                obj.insert("role".to_string(), Value::String("assistant".to_string()));
                if texts.is_empty() {
                    obj.insert("content".to_string(), Value::Null);
                } else {
                    obj.insert("content".to_string(), Value::String(texts));
                }
                let wire_tcs: Vec<Value> = tool_calls
                    .iter()
                    .map(|tc| {
                        let mut o = serde_json::Map::new();
                        if let Some(id) = &tc.id {
                            o.insert("id".to_string(), Value::String(id.clone()));
                        }
                        o.insert("type".to_string(), Value::String("function".to_string()));
                        let mut f = serde_json::Map::new();
                        f.insert("name".to_string(), Value::String(tc.name.clone()));
                        f.insert(
                            "arguments".to_string(),
                            Value::String(serde_json::to_string(&tc.arguments).unwrap_or_default()),
                        );
                        o.insert("function".to_string(), Value::Object(f));
                        Value::Object(o)
                    })
                    .collect();
                obj.insert("tool_calls".to_string(), Value::Array(wire_tcs));
                msgs.push(Value::Object(obj));
            } else {
                let mut obj = serde_json::Map::new();
                obj.insert("role".to_string(), Value::String(m.role.clone()));
                let content_arr: Vec<Value> = m
                    .content
                    .iter()
                    .filter_map(|p| {
                        if let ContentPart::Text(t) = p {
                            Some(serde_json::json!({"type":"text","text":t.text}))
                        } else {
                            None
                        }
                    })
                    .collect();
                // Frozen v0.1.0: simple OpenAI text keeps array form for backward compat
                obj.insert("content".to_string(), Value::Array(content_arr));
                msgs.push(Value::Object(obj));
            }
        }
    }
    body.insert("messages".to_string(), Value::Array(msgs));
    body.insert("stream".to_string(), Value::Bool(streaming));
    if let Some(n) = req.max_output_tokens {
        body.insert("max_tokens".to_string(), Value::Number(n.into()));
    }
    if let Some(v) = req.temperature {
        body.insert(
            "temperature".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(v) = req.top_p {
        body.insert(
            "top_p".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(stop) = &req.stop {
        body.insert(
            "stop".to_string(),
            Value::Array(stop.iter().map(|s| Value::String(s.clone())).collect()),
        );
    }
    if let Some(tools) = &req.tools {
        body.insert("tools".to_string(), Value::Array(tools.iter().map(|t| serde_json::json!({"type":"function","function":{"name":t.name,"parameters":t.input_schema}})).collect()));
    }
    if let Some(tc) = &req.tool_choice {
        let v = match tc {
            ToolChoice::Auto => Value::String("auto".to_string()),
            ToolChoice::None_ => Value::String("none".to_string()),
            ToolChoice::Required => Value::String("required".to_string()),
            ToolChoice::Named(n) => serde_json::json!({"type":"function","function":{"name":n}}),
        };
        body.insert("tool_choice".to_string(), v);
    }
    if let Some(fmt) = &req.response_format {
        match fmt {
            ResponseFormat::Text => {}
            ResponseFormat::Json => {
                body.insert(
                    "response_format".to_string(),
                    serde_json::json!({"type":"json_object"}),
                );
            }
            ResponseFormat::JsonSchema { schema } => {
                body.insert("response_format".to_string(), serde_json::json!({"type":"json_schema","json_schema":{"name":"response","strict":true,"schema":schema}}));
            }
        }
    }
    if let Some(po) = &req.provider_options {
        for (k, v) in po {
            body.insert(k.clone(), v.clone());
        }
    }
    Ok(Value::Object(body))
}

pub(crate) fn decode_openai(
    value: Value,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> Result<GenerationResponse, ConduitError> {
    let obj = value.as_object().ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Chat Completions response.",
        )
    })?;
    let choices = obj
        .get("choices")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Chat Completions response.",
            )
        })?;
    if choices.len() != 1 {
        return Err(ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Chat Completions response.",
        ));
    }
    let choice = choices[0].as_object().ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Chat Completions response.",
        )
    })?;
    let message = choice
        .get("message")
        .and_then(|v| v.as_object())
        .ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Chat Completions response.",
            )
        })?;
    if message.get("role").and_then(|v| v.as_str()) != Some("assistant") {
        return Err(ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Chat Completions response.",
        ));
    }
    let finish = choice
        .get("finish_reason")
        .and_then(|v| v.as_str())
        .unwrap_or("other");
    let has_tool_calls = message
        .get("tool_calls")
        .and_then(|v| v.as_array())
        .map(|calls| !calls.is_empty())
        .unwrap_or(false);
    // content may be string, null, or missing
    let content_val = message.get("content");
    match content_val {
        Some(Value::String(_)) => {}
        Some(Value::Null) => {
            if finish != "content_filter" && !has_tool_calls && content_val.is_some() {
                // content null is allowed for tool-only or content_filter
            }
        }
        None => {}
        _ => {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Chat Completions response.",
            ))
        }
    }
    let mut content = Vec::new();
    if let Some(Value::String(s)) = content_val {
        if !s.is_empty() || !has_tool_calls {
            content.push(ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: s.clone(),
            }));
        } else if s.is_empty() && has_tool_calls {
            // tool-only with empty string: do not push Text
        } else if !s.is_empty() {
            content.push(ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: s.clone(),
            }));
        }
    } else if content_val.is_none() && has_tool_calls {
        // tool-only with null/missing: no Text
    } else if let Some(Value::String(s)) = content_val {
        if !s.is_empty() {
            content.push(ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: s.clone(),
            }));
        }
    }
    if let Some(arr) = message.get("tool_calls").and_then(|v| v.as_array()) {
        for tc in arr {
            let o = tc.as_object().ok_or_else(|| {
                ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Chat Completions response.",
                )
            })?;
            let id = o.get("id").and_then(|v| v.as_str()).map(|s| s.to_string());
            let f = o
                .get("function")
                .and_then(|v| v.as_object())
                .ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Chat Completions response.",
                    )
                })?;
            let name = f
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Chat Completions response.",
                    )
                })?
                .to_string();
            let args = match f.get("arguments") {
                None => Value::Object(Default::default()),
                Some(Value::String(args_s)) if args_s.is_empty() => {
                    Value::Object(Default::default())
                }
                Some(Value::String(args_s)) => serde_json::from_str(args_s).map_err(|_| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Chat Completions response.",
                    )
                })?,
                Some(Value::Object(args)) => Value::Object(args.clone()),
                Some(_) => {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Chat Completions response.",
                    ))
                }
            };
            content.push(ContentPart::ToolCall(ToolCallPart {
                part_type: "tool_call".to_string(),
                id,
                name,
                arguments: args,
            }));
        }
    }
    let finish_reason = if has_tool_calls {
        "tool_call".to_string()
    } else {
        match finish {
            "stop" => "stop",
            "length" => "length",
            "content_filter" => "content_filter",
            "tool_calls" => "tool_call",
            _ => "other",
        }
        .to_string()
    };
    let mut usage = None;
    if let Some(u) = obj.get("usage") {
        if let Some(o) = u.as_object() {
            let mut us = Usage::default();
            let mut has = false;
            if let Some(Value::Number(n)) = o.get("prompt_tokens") {
                if let Some(v) = n.as_u64() {
                    us.input_tokens = Some(v as u32);
                    has = true;
                }
            }
            if let Some(Value::Number(n)) = o.get("completion_tokens") {
                if let Some(v) = n.as_u64() {
                    us.output_tokens = Some(v as u32);
                    has = true;
                }
            }
            if let Some(Value::Number(n)) = o.get("total_tokens") {
                if let Some(v) = n.as_u64() {
                    us.total_tokens = Some(v as u32);
                    has = true;
                }
            }
            if has {
                usage = Some(us);
            }
        }
    }
    let mut meta = HashMap::new();
    if let Some(rid) = request_id {
        meta.insert("requestId".to_string(), Value::String(rid));
    }
    let id = obj.get("id").and_then(|v| v.as_str()).map(|s| redact(s));
    let model = obj.get("model").and_then(|v| v.as_str()).map(|s| redact(s));
    Ok(GenerationResponse {
        id,
        model,
        content,
        finish_reason,
        usage,
        provider_metadata: meta,
    })
}

pub(crate) fn http_error_openai(
    status: u16,
    body: &str,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> ConduitError {
    let mut details = HashMap::new();
    if let Ok(v) = serde_json::from_str::<Value>(body) {
        if let Some(err) = v.get("error").and_then(|e| e.as_object()) {
            for f in &["message", "type", "code"] {
                if let Some(Value::String(s)) = err.get(*f) {
                    details.insert(f.to_string(), redact(s));
                }
            }
        }
    }
    let mut e = ConduitError::new(
        "ProviderError",
        &format!("Provider returned HTTP {}.", status),
    )
    .with_status(status);
    if let Some(rid) = request_id {
        e = e.with_request_id(rid);
    }
    if !details.is_empty() {
        e = e.with_details(details);
    }
    // Map 404 model_not_found etc. would be handled via details code, but for v0 keep ProviderError
    e
}

pub(crate) fn openai_sse_stream<R: BufRead + Send + 'static>(
    mut reader: R,
    request_id: Option<String>,
    redact: std::sync::Arc<dyn Fn(&str) -> String + Send + Sync>,
    deadline: Option<std::time::Instant>,
    signal: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
) -> impl Iterator<Item = Result<StreamEvent, ConduitError>> + Send {
    struct State<R> {
        reader: R,
        line: String,
        data: Vec<String>,
        started: bool,
        id: Option<String>,
        model: Option<String>,
        finish: Option<String>,
        usage: Option<Usage>,
        content: String,
        has_content: bool,
        tool_accum: std::collections::HashMap<u32, std::collections::HashMap<String, String>>,
        deadline: Option<std::time::Instant>,
        signal: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
        redact: std::sync::Arc<dyn Fn(&str) -> String + Send + Sync>,
        request_id: Option<String>,
        done: bool,
        pending: Vec<StreamEvent>,
    }
    impl<R: BufRead> Iterator for State<R> {
        type Item = Result<StreamEvent, ConduitError>;
        fn next(&mut self) -> Option<Self::Item> {
            if !self.pending.is_empty() {
                return Some(Ok(self.pending.remove(0)));
            }
            if self.done {
                return None;
            }
            loop {
                if let Some(sig) = &self.signal {
                    if sig.load(std::sync::atomic::Ordering::SeqCst) {
                        return Some(Err(ConduitError::new(
                            "CancelledError",
                            "Request cancelled by caller.",
                        )));
                    }
                }
                if let Some(d) = self.deadline {
                    if std::time::Instant::now() >= d {
                        return Some(Err(ConduitError::new(
                            "TimeoutError",
                            "Request deadline exceeded.",
                        )));
                    }
                }
                self.line.clear();
                let n = match self.reader.read_line(&mut self.line) {
                    Ok(n) => n,
                    Err(e) => return Some(Err(stream_read_error(e, self.deadline))),
                };
                if n == 0 {
                    if !self.started {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Chat Completions stream.",
                        )));
                    }
                    return None;
                }
                let trimmed = self
                    .line
                    .trim_end_matches(|c| c == '\r' || c == '\n')
                    .to_string();
                if trimmed.is_empty() {
                    if self.data.is_empty() {
                        continue;
                    }
                    let data_str = self.data.join("\n");
                    self.data.clear();
                    if data_str == "[DONE]" {
                        if !self.started || self.finish.is_none() {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Chat Completions stream.",
                            )));
                        }
                        let mut msg = serde_json::Map::new();
                        msg.insert(
                            "role".to_string(),
                            serde_json::Value::String("assistant".to_string()),
                        );
                        if self.has_content {
                            msg.insert(
                                "content".to_string(),
                                serde_json::Value::String(self.content.clone()),
                            );
                        } else {
                            msg.insert("content".to_string(), serde_json::Value::Null);
                        }
                        if !self.tool_accum.is_empty() {
                            let mut tcs = Vec::new();
                            for (idx, m) in &self.tool_accum {
                                let mut o = serde_json::Map::new();
                                if let Some(id) = m.get("id") {
                                    o.insert(
                                        "id".to_string(),
                                        serde_json::Value::String(id.clone()),
                                    );
                                }
                                o.insert(
                                    "type".to_string(),
                                    serde_json::Value::String("function".to_string()),
                                );
                                let mut f = serde_json::Map::new();
                                f.insert(
                                    "name".to_string(),
                                    serde_json::Value::String(
                                        m.get("name").cloned().unwrap_or_default(),
                                    ),
                                );
                                f.insert(
                                    "arguments".to_string(),
                                    serde_json::Value::String(
                                        m.get("arguments").cloned().unwrap_or_default(),
                                    ),
                                );
                                o.insert("function".to_string(), serde_json::Value::Object(f));
                                tcs.push(serde_json::Value::Object(o));
                            }
                            tcs.sort_by(|a, b| {
                                a.get("id")
                                    .and_then(|v| v.as_str())
                                    .cmp(&b.get("id").and_then(|v| v.as_str()))
                            });
                            msg.insert("tool_calls".to_string(), serde_json::Value::Array(tcs));
                        }
                        let mut body = serde_json::Map::new();
                        if let Some(id) = &self.id {
                            body.insert("id".to_string(), serde_json::Value::String(id.clone()));
                        }
                        if let Some(m) = &self.model {
                            body.insert("model".to_string(), serde_json::Value::String(m.clone()));
                        }
                        let choice = serde_json::json!({"message": msg, "finish_reason": self.finish.clone().unwrap()});
                        body.insert(
                            "choices".to_string(),
                            serde_json::Value::Array(vec![choice]),
                        );
                        if let Some(u) = &self.usage {
                            let mut um = serde_json::Map::new();
                            if let Some(v) = u.input_tokens {
                                um.insert(
                                    "prompt_tokens".to_string(),
                                    serde_json::Value::Number(v.into()),
                                );
                            }
                            if let Some(v) = u.output_tokens {
                                um.insert(
                                    "completion_tokens".to_string(),
                                    serde_json::Value::Number(v.into()),
                                );
                            }
                            if let Some(v) = u.total_tokens {
                                um.insert(
                                    "total_tokens".to_string(),
                                    serde_json::Value::Number(v.into()),
                                );
                            }
                            body.insert("usage".to_string(), serde_json::Value::Object(um));
                        }
                        let v = serde_json::Value::Object(body);
                        match decode_openai(v, self.request_id.clone(), &*self.redact) {
                            Ok(resp) => {
                                self.done = true;
                                return Some(Ok(StreamEvent::Done { response: resp }));
                            }
                            Err(e) => return Some(Err(e)),
                        }
                    }
                    let v: Value = match serde_json::from_str(&data_str) {
                        Ok(v) => v,
                        Err(_) => {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Chat Completions stream.",
                            )))
                        }
                    };
                    if v.get("error").is_some() {
                        return Some(Err(http_error_openai(
                            200,
                            &data_str,
                            self.request_id.clone(),
                            &*self.redact,
                        )));
                    }
                    let choices = match v.get("choices").and_then(|c| c.as_array()) {
                        Some(c) => c,
                        None => {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Chat Completions stream.",
                            )))
                        }
                    };
                    if choices.len() > 1 {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Chat Completions stream.",
                        )));
                    }
                    if let Some(id) = v.get("id").and_then(|x| x.as_str()) {
                        self.id = Some(id.to_string());
                    }
                    if let Some(m) = v.get("model").and_then(|x| x.as_str()) {
                        self.model = Some(m.to_string());
                    }
                    let mut reported = None;
                    if let Some(u) = v.get("usage") {
                        if let Some(o) = u.as_object() {
                            let mut us = Usage::default();
                            let mut has = false;
                            if let Some(serde_json::Value::Number(n)) = o.get("prompt_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.input_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if let Some(serde_json::Value::Number(n)) = o.get("completion_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.output_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if let Some(serde_json::Value::Number(n)) = o.get("total_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.total_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if has {
                                reported = Some(us);
                            }
                        }
                    }
                    let mut content_delta = String::new();
                    let mut tool_deltas: Vec<std::collections::HashMap<String, String>> =
                        Vec::new();
                    if choices.is_empty() {
                        if !self.started || reported.is_none() {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Chat Completions stream.",
                            )));
                        }
                    } else {
                        let choice = &choices[0];
                        let finish = choice
                            .get("finish_reason")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        if let Some(fr) = finish {
                            self.finish = Some(fr);
                        }
                        let delta = match choice.get("delta").and_then(|d| d.as_object()) {
                            Some(d) => d,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Chat Completions stream.",
                                )))
                            }
                        };
                        if let Some(c) = delta.get("content").and_then(|v| v.as_str()) {
                            content_delta = c.to_string();
                            self.has_content = true;
                        }
                        if let Some(arr) = delta.get("tool_calls").and_then(|v| v.as_array()) {
                            for tc in arr {
                                let idx =
                                    tc.get("index").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
                                let entry = self.tool_accum.entry(idx).or_insert_with(|| {
                                    let mut m = std::collections::HashMap::new();
                                    m.insert("arguments".to_string(), "".to_string());
                                    m
                                });
                                if let Some(id) = tc.get("id").and_then(|v| v.as_str()) {
                                    entry.insert("id".to_string(), id.to_string());
                                }
                                if let Some(f) = tc.get("function").and_then(|v| v.as_object()) {
                                    if let Some(n) = f.get("name").and_then(|v| v.as_str()) {
                                        entry.insert("name".to_string(), n.to_string());
                                    }
                                    if let Some(a) = f.get("arguments") {
                                        if !a.is_string() {
                                            return Some(Err(ConduitError::new(
                                                "ProtocolError",
                                                "Malformed or incomplete Chat Completions stream.",
                                            )));
                                        }
                                    }
                                    if let Some(a) = f.get("arguments").and_then(|v| v.as_str()) {
                                        let cur =
                                            entry.get("arguments").cloned().unwrap_or_default();
                                        entry.insert("arguments".to_string(), cur + a);
                                    }
                                }
                                let mut d = std::collections::HashMap::new();
                                d.insert("index".to_string(), idx.to_string());
                                if let Some(id) = tc.get("id").and_then(|v| v.as_str()) {
                                    d.insert("id".to_string(), id.to_string());
                                }
                                if let Some(f) = tc
                                    .get("function")
                                    .and_then(|v| v.as_object())
                                    .and_then(|f| f.get("name"))
                                    .and_then(|v| v.as_str())
                                {
                                    d.insert("name".to_string(), f.to_string());
                                }
                                if let Some(a) = tc
                                    .get("function")
                                    .and_then(|v| v.as_object())
                                    .and_then(|f| f.get("arguments"))
                                    .and_then(|v| v.as_str())
                                {
                                    if !a.is_empty() {
                                        d.insert("argumentsDelta".to_string(), a.to_string());
                                    }
                                }
                                if d.contains_key("id")
                                    || d.contains_key("name")
                                    || d.contains_key("argumentsDelta")
                                {
                                    tool_deltas.push(d);
                                }
                            }
                        }
                    }
                    if !self.started {
                        self.started = true;
                        let ev = StreamEvent::Start {
                            id: self.id.clone().map(|s| (self.redact)(&s)),
                            model: self.model.clone().map(|s| (self.redact)(&s)),
                        };
                        self.pending.push(ev);
                    }
                    if !content_delta.is_empty() {
                        self.content.push_str(&content_delta);
                        self.pending.push(StreamEvent::TextDelta {
                            index: 0,
                            text: content_delta.clone(),
                        });
                    }
                    for d in tool_deltas {
                        let idx: u32 = d.get("index").and_then(|s| s.parse().ok()).unwrap_or(0);
                        self.pending.push(StreamEvent::ToolCallDelta {
                            index: idx,
                            id: d.get("id").cloned(),
                            name: d.get("name").cloned(),
                            arguments_delta: d.get("argumentsDelta").cloned(),
                        });
                    }
                    if let Some(u) = reported {
                        self.usage = Some(match &self.usage {
                            Some(prev) => {
                                let mut n = prev.clone();
                                if let Some(v) = u.input_tokens {
                                    n.input_tokens = Some(v);
                                }
                                if let Some(v) = u.output_tokens {
                                    n.output_tokens = Some(v);
                                }
                                if let Some(v) = u.total_tokens {
                                    n.total_tokens = Some(v);
                                }
                                n
                            }
                            None => u.clone(),
                        });
                        self.pending.push(StreamEvent::Usage {
                            usage: self.usage.clone().unwrap(),
                        });
                    }
                    if !self.pending.is_empty() {
                        return Some(Ok(self.pending.remove(0)));
                    }
                } else if let Some(s) = trimmed.strip_prefix("data:") {
                    let v = s.trim_start_matches(' ').to_string();
                    self.data.push(v);
                } else if trimmed.starts_with("data") {
                    self.data.push("".to_string());
                }
            }
        }
    }
    let state = State {
        reader: std::io::BufReader::new(reader),
        line: String::new(),
        data: Vec::new(),
        started: false,
        id: None,
        model: None,
        finish: None,
        usage: None,
        content: String::new(),
        has_content: false,
        tool_accum: std::collections::HashMap::new(),
        deadline,
        signal,
        redact,
        request_id,
        done: false,
        pending: Vec::new(),
    };
    state
}
