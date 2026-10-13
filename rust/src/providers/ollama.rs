use crate::*;
use serde_json::Value;

pub(crate) fn encode_ollama_request(
    model: &str,
    req: &GenerationRequest,
    streaming: bool,
) -> Result<Value, ConduitError> {
    if let Some(po) = &req.provider_options {
        if po.contains_key("model")
            || po.contains_key("messages")
            || po.contains_key("stream")
            || po.contains_key("tools")
            || po.contains_key("format")
            || po.contains_key("tool_choice")
        {
            return Err(ConduitError::new(
                "InvalidRequestError",
                "Invalid Ollama options or conflict with a Conduit-owned field.",
            ));
        }
        if let Some(Value::Object(opts)) = po.get("options") {
            if opts.contains_key("num_predict")
                || opts.contains_key("temperature")
                || opts.contains_key("top_p")
                || opts.contains_key("stop")
            {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "Invalid Ollama options or conflict with a Conduit-owned field.",
                ));
            }
            for v in opts.values() {
                let mut seen = HashSet::new();
                json_validate(v, &mut seen)?;
            }
        } else if po.get("options").is_some() {
            return Err(ConduitError::new(
                "InvalidRequestError",
                "Invalid Ollama options or conflict with a Conduit-owned field.",
            ));
        }
        for v in po.values() {
            let mut seen = HashSet::new();
            json_validate(v, &mut seen)?;
        }
    }
    if req.tool_choice.is_some() {
        return Err(ConduitError::new(
            "UnsupportedCapabilityError",
            "toolChoice is not supported for Ollama; omit toolChoice or use providerOptions for native fields.",
        ));
    }
    // tools mapping already validated via validate_tools; wire tools
    let wire_tools = req.tools.as_ref().map(|ts| {
        Value::Array(
            ts.iter()
                .map(|td| {
                    let mut f = serde_json::Map::new();
                    f.insert("name".to_string(), Value::String(td.name.clone()));
                    if let Some(d) = &td.description {
                        f.insert("description".to_string(), Value::String(d.clone()));
                    }
                    f.insert("parameters".to_string(), td.input_schema.clone());
                    let mut o = serde_json::Map::new();
                    o.insert("type".to_string(), Value::String("function".to_string()));
                    o.insert("function".to_string(), Value::Object(f));
                    Value::Object(o)
                })
                .collect(),
        )
    });
    let wire_format = match &req.response_format {
        None => None,
        Some(ResponseFormat::Text) => None,
        Some(ResponseFormat::Json) => Some(Value::String("json".to_string())),
        Some(ResponseFormat::JsonSchema { schema }) => Some(schema.clone()),
    };
    // messages
    let mut wire_messages: Vec<Value> = Vec::new();
    for m in &req.messages {
        if m.role == "tool" {
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
            if let Some(n) = &tr.name {
                obj.insert("tool_name".to_string(), Value::String(n.clone()));
            } else if let Some(id) = &tr.call_id {
                obj.insert("tool_name".to_string(), Value::String(id.clone()));
            }
            wire_messages.push(Value::Object(obj));
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
            if !tool_calls.is_empty() {
                let tcs: Vec<Value> = tool_calls
                    .iter()
                    .map(|tc| {
                        let mut f = serde_json::Map::new();
                        f.insert("name".to_string(), Value::String(tc.name.clone()));
                        f.insert("arguments".to_string(), tc.arguments.clone());
                        let mut o = serde_json::Map::new();
                        o.insert("function".to_string(), Value::Object(f));
                        Value::Object(o)
                    })
                    .collect();
                let mut obj = serde_json::Map::new();
                obj.insert("role".to_string(), Value::String(m.role.clone()));
                obj.insert("content".to_string(), Value::String(texts));
                obj.insert("tool_calls".to_string(), Value::Array(tcs));
                wire_messages.push(Value::Object(obj));
            } else {
                let mut obj = serde_json::Map::new();
                obj.insert("role".to_string(), Value::String(m.role.clone()));
                obj.insert("content".to_string(), Value::String(texts));
                wire_messages.push(Value::Object(obj));
            }
        }
    }
    let mut body = serde_json::Map::new();
    if let Some(po) = &req.provider_options {
        for (k, v) in po {
            if k != "options" {
                body.insert(k.clone(), v.clone());
            }
        }
    }
    body.insert("model".to_string(), Value::String(model.to_string()));
    body.insert("messages".to_string(), Value::Array(wire_messages));
    body.insert("stream".to_string(), Value::Bool(streaming));
    if let Some(wt) = wire_tools {
        body.insert("tools".to_string(), wt);
    }
    if let Some(wf) = wire_format {
        body.insert("format".to_string(), wf);
    }
    let mut options = serde_json::Map::new();
    if let Some(n) = req.max_output_tokens {
        options.insert("num_predict".to_string(), Value::Number(n.into()));
    }
    if let Some(v) = req.temperature {
        options.insert(
            "temperature".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(v) = req.top_p {
        options.insert(
            "top_p".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(stop) = &req.stop {
        options.insert(
            "stop".to_string(),
            Value::Array(stop.iter().map(|s| Value::String(s.clone())).collect()),
        );
    }
    // merge native options
    if let Some(po) = &req.provider_options {
        if let Some(Value::Object(native_opts)) = po.get("options") {
            for (k, v) in native_opts {
                options.entry(k.clone()).or_insert(v.clone());
            }
        }
    }
    if !options.is_empty() {
        body.insert("options".to_string(), Value::Object(options));
    }
    Ok(Value::Object(body))
}

pub(crate) fn ollama_error(
    status: u16,
    body: &str,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
    model: Option<&str>,
) -> ConduitError {
    let mut msg: Option<String> = None;
    if let Ok(v) = serde_json::from_str::<Value>(body) {
        if let Some(s) = v.get("error").and_then(|x| x.as_str()) {
            msg = Some(s.to_string());
        }
    }
    let model_not_found = if let (Some(m), Some(e)) = (model, &msg) {
        let candidates = [
            format!("model '{}' not found", m),
            format!("model \"{}\" not found", m),
            format!("model \"{}\" not found, try pulling it first", m),
        ];
        candidates.iter().any(|c| c == e)
    } else {
        false
    };
    let mut details = HashMap::new();
    if let Some(m) = msg {
        details.insert("message".to_string(), redact(&m));
    }
    http_failure(status, details, request_id, model_not_found)
}

pub(crate) fn decode_ollama(
    value: Value,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> Result<GenerationResponse, ConduitError> {
    if let Some(obj) = value.as_object() {
        if let Some(Value::String(e)) = obj.get("error") {
            return Err(ollama_error(
                200,
                &serde_json::to_string(&value).unwrap_or_default(),
                request_id.clone(),
                redact,
                None,
            ));
        }
    }
    let obj = value.as_object().ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        )
    })?;
    let done = obj.get("done").and_then(|v| v.as_bool()).ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        )
    })?;
    if !done {
        return Err(ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        ));
    }
    let msg_val = obj.get("message").ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        )
    })?;
    let msg = msg_val.as_object().ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        )
    })?;
    if msg.get("role").and_then(|v| v.as_str()) != Some("assistant") {
        return Err(ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        ));
    }
    let content_str = msg.get("content").and_then(|v| v.as_str()).ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Ollama chat response.",
        )
    })?;
    let mut provider_metadata = HashMap::new();
    if let Some(rid) = request_id {
        provider_metadata.insert("requestId".to_string(), Value::String(rid));
    }
    if let Some(Value::String(s)) = obj.get("done_reason") {
        provider_metadata.insert("finishReason".to_string(), Value::String(redact(s)));
    }
    if let Some(Value::String(s)) = obj.get("created_at") {
        provider_metadata.insert("created_at".to_string(), Value::String(redact(s)));
    }
    for k in [
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    ] {
        if let Some(Value::Number(n)) = obj.get(k) {
            provider_metadata.insert(k.to_string(), Value::Number(n.clone()));
        }
    }
    let mut usage: Option<Usage> = None;
    let mut u = Usage::default();
    let mut has_u = false;
    if let Some(Value::Number(n)) = obj.get("prompt_eval_count") {
        if let Some(v) = n.as_u64() {
            u.input_tokens = Some(v as u32);
            has_u = true;
        }
    }
    if let Some(Value::Number(n)) = obj.get("eval_count") {
        if let Some(v) = n.as_u64() {
            u.output_tokens = Some(v as u32);
            has_u = true;
        }
    }
    if has_u {
        usage = Some(u);
    }
    let mut content: Vec<ContentPart> = Vec::new();
    let has_tool_calls = msg
        .get("tool_calls")
        .and_then(|v| v.as_array())
        .map(|a| !a.is_empty())
        .unwrap_or(false);
    if !content_str.is_empty() || !has_tool_calls {
        content.push(ContentPart::Text(TextPart {
            part_type: "text".to_string(),
            text: content_str.to_string(),
        }));
    }
    if has_tool_calls {
        for tc in msg.get("tool_calls").and_then(|v| v.as_array()).unwrap() {
            let o = tc.as_object().ok_or_else(|| {
                ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Ollama chat response.",
                )
            })?;
            let f = o
                .get("function")
                .and_then(|v| v.as_object())
                .ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Ollama chat response.",
                    )
                })?;
            let name = f
                .get("name")
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Ollama chat response.",
                    )
                })?
                .to_string();
            let args = f
                .get("arguments")
                .cloned()
                .unwrap_or(Value::Object(serde_json::Map::new()));
            content.push(ContentPart::ToolCall(ToolCallPart {
                part_type: "tool_call".to_string(),
                id: None,
                name,
                arguments: args,
            }));
        }
    }
    let finish_reason = if has_tool_calls {
        "tool_call".to_string()
    } else {
        match obj.get("done_reason").and_then(|v| v.as_str()) {
            Some("stop") => "stop".to_string(),
            Some("length") => "length".to_string(),
            _ => "other".to_string(),
        }
    };
    let model = obj.get("model").and_then(|v| v.as_str()).map(|s| redact(s));
    Ok(GenerationResponse {
        id: None,
        model,
        content,
        finish_reason,
        usage,
        provider_metadata,
    })
}

pub(crate) fn ollama_ndjson_stream<R: BufRead + Send + 'static>(
    mut reader: R,
    request_id: Option<String>,
    redact: std::sync::Arc<dyn Fn(&str) -> String + Send + Sync>,
    deadline: Option<std::time::Instant>,
    signal: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
) -> impl Iterator<Item = Result<StreamEvent, ConduitError>> + Send {
    struct State<R> {
        reader: R,
        buf: String,
        line: String,
        started: bool,
        model: Option<String>,
        content: String,
        usage: Option<Usage>,
        tool_calls: Vec<ToolCallPart>,
        tool_emitted: usize,
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
                            "Malformed or unsupported Ollama chat response.",
                        )));
                    }
                    return Some(Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or incomplete Ollama stream.",
                    )));
                }
                let effective = if self.line.ends_with("\r\n") {
                    self.line[..self.line.len() - 2].to_string()
                } else if self.line.ends_with('\n') || self.line.ends_with('\r') {
                    self.line
                        .trim_end_matches(|c| c == '\r' || c == '\n')
                        .to_string()
                } else {
                    self.line.clone()
                };
                if effective.trim().is_empty() {
                    continue;
                }
                let v: Value = match serde_json::from_str(&effective) {
                    Ok(v) => v,
                    Err(_) => {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Ollama chat response.",
                        )))
                    }
                };
                if let Some(obj) = v.as_object() {
                    if let Some(Value::String(e)) = obj.get("error") {
                        return Some(Err(ollama_error(
                            200,
                            &effective,
                            self.request_id.clone(),
                            &*self.redact,
                            None,
                        )));
                    }
                }
                let obj = match v.as_object() {
                    Some(o) => o.clone(),
                    None => {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Ollama chat response.",
                        )))
                    }
                };
                let done = obj.get("done").and_then(|v| v.as_bool()).unwrap_or(false);
                let model = obj
                    .get("model")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                if let Some(m) = &model {
                    if let Some(prev) = &self.model {
                        if prev != m {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or unsupported Ollama chat response.",
                            )));
                        }
                    } else {
                        self.model = Some(m.clone());
                    }
                }
                // usage
                let mut rep = Usage::default();
                let mut has_rep = false;
                if let Some(Value::Number(n)) = obj.get("prompt_eval_count") {
                    if let Some(v) = n.as_u64() {
                        rep.input_tokens = Some(v as u32);
                        has_rep = true;
                    }
                }
                if let Some(Value::Number(n)) = obj.get("eval_count") {
                    if let Some(v) = n.as_u64() {
                        rep.output_tokens = Some(v as u32);
                        has_rep = true;
                    }
                }
                if has_rep {
                    self.usage = Some(match &self.usage {
                        Some(prev) => {
                            let mut n = prev.clone();
                            if rep.input_tokens.is_some() {
                                n.input_tokens = rep.input_tokens;
                            }
                            if rep.output_tokens.is_some() {
                                n.output_tokens = rep.output_tokens;
                            }
                            n
                        }
                        None => rep,
                    });
                }
                let msg = obj.get("message").and_then(|v| v.as_object());
                let text = msg
                    .and_then(|m| m.get("content"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let incoming_tcs: Vec<ToolCallPart> = if let Some(tc_arr) = msg
                    .and_then(|m| m.get("tool_calls"))
                    .and_then(|v| v.as_array())
                {
                    let mut out = Vec::new();
                    for tc in tc_arr {
                        let o = match tc.as_object() {
                            Some(o) => o,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or unsupported Ollama chat response.",
                                )))
                            }
                        };
                        let f = match o.get("function").and_then(|v| v.as_object()) {
                            Some(f) => f,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or unsupported Ollama chat response.",
                                )))
                            }
                        };
                        let name = match f.get("name").and_then(|v| v.as_str()) {
                            Some(n) => n.to_string(),
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or unsupported Ollama chat response.",
                                )))
                            }
                        };
                        let args = f
                            .get("arguments")
                            .cloned()
                            .unwrap_or(Value::Object(serde_json::Map::new()));
                        out.push(ToolCallPart {
                            part_type: "tool_call".to_string(),
                            id: None,
                            name,
                            arguments: args,
                        });
                    }
                    out
                } else {
                    Vec::new()
                };
                for tc in incoming_tcs {
                    self.tool_calls.push(tc);
                }
                self.content.push_str(&text);
                let is_terminal = done;
                let mut response: Option<GenerationResponse> = None;
                if is_terminal {
                    let mut fin_msg = serde_json::Map::new();
                    fin_msg.insert("role".to_string(), Value::String("assistant".to_string()));
                    fin_msg.insert("content".to_string(), Value::String(self.content.clone()));
                    if !self.tool_calls.is_empty() {
                        let tcs: Vec<Value> = self
                            .tool_calls
                            .iter()
                            .map(|tc| {
                                let mut f = serde_json::Map::new();
                                f.insert("name".to_string(), Value::String(tc.name.clone()));
                                f.insert("arguments".to_string(), tc.arguments.clone());
                                let mut o = serde_json::Map::new();
                                o.insert("function".to_string(), Value::Object(f));
                                Value::Object(o)
                            })
                            .collect();
                        fin_msg.insert("tool_calls".to_string(), Value::Array(tcs));
                    }
                    let mut body = serde_json::Map::new();
                    body.insert("message".to_string(), Value::Object(fin_msg));
                    body.insert("done".to_string(), Value::Bool(true));
                    if let Some(m) = &self.model {
                        body.insert("model".to_string(), Value::String(m.clone()));
                    }
                    if let Some(d) = obj.get("done_reason") {
                        body.insert("done_reason".to_string(), d.clone());
                    }
                    if let Some(u) = &self.usage {
                        if let Some(v) = u.input_tokens {
                            body.insert("prompt_eval_count".to_string(), Value::Number(v.into()));
                        }
                        if let Some(v) = u.output_tokens {
                            body.insert("eval_count".to_string(), Value::Number(v.into()));
                        }
                    }
                    let v2 = Value::Object(body);
                    match decode_ollama(v2, self.request_id.clone(), &*self.redact) {
                        Ok(r) => response = Some(r),
                        Err(e) => return Some(Err(e)),
                    };
                }
                if !self.started {
                    self.started = true;
                    let m = self.model.clone().map(|s| (self.redact)(&s));
                    self.pending.push(StreamEvent::Start { id: None, model: m });
                }
                if !text.is_empty() {
                    self.pending.push(StreamEvent::TextDelta {
                        index: 0,
                        text: text.clone(),
                    });
                }
                for i in self.tool_emitted..self.tool_calls.len() {
                    let tc = &self.tool_calls[i];
                    let args_str = if tc.arguments.is_object()
                        && tc.arguments.as_object().unwrap().is_empty()
                    {
                        "".to_string()
                    } else {
                        serde_json::to_string(&tc.arguments).unwrap_or_default()
                    };
                    self.pending.push(StreamEvent::ToolCallDelta {
                        index: i as u32,
                        id: None,
                        name: Some(tc.name.clone()),
                        arguments_delta: if args_str.is_empty() || args_str == "{}" {
                            None
                        } else {
                            Some(args_str)
                        },
                    });
                }
                self.tool_emitted = self.tool_calls.len();
                if has_rep {
                    if let Some(u) = &self.usage {
                        self.pending.push(StreamEvent::Usage { usage: u.clone() });
                    }
                }
                if let Some(r) = response {
                    self.pending.push(StreamEvent::Done { response: r });
                    self.done = true;
                }
                if !self.pending.is_empty() {
                    return Some(Ok(self.pending.remove(0)));
                }
                if is_terminal {
                    return Some(Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or incomplete Ollama stream.",
                    )));
                }
            }
        }
    }
    let state = State {
        reader: std::io::BufReader::new(reader),
        buf: String::new(),
        line: String::new(),
        started: false,
        model: None,
        content: String::new(),
        usage: None,
        tool_calls: Vec::new(),
        tool_emitted: 0,
        deadline,
        signal,
        redact,
        request_id,
        done: false,
        pending: Vec::new(),
    };
    state
}
