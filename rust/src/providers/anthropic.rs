use crate::*;
use serde_json::Value;

pub(crate) fn encode_anthropic_request(
    model: &str,
    req: &GenerationRequest,
    streaming: bool,
) -> Result<Value, ConduitError> {
    // providerOptions owned check (Anthropic)
    if let Some(po) = &req.provider_options {
        let owned = [
            "model",
            "messages",
            "system",
            "stream",
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "stop_sequences",
            "tools",
            "tool_choice",
        ];
        for k in po.keys() {
            if owned.contains(&k.as_str()) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions conflicts with a Conduit-owned field.",
                ));
            }
        }
        // validate JSON acyclic via json_validate on each value
        for v in po.values() {
            let mut seen = HashSet::new();
            json_validate(v, &mut seen)?;
        }
    }
    // system extraction
    let mut system_parts: Vec<String> = Vec::new();
    let mut anthropic_messages: Vec<Value> = Vec::new();
    let mut seen_non_system = false;
    for m in &req.messages {
        if m.role == "system" {
            if seen_non_system {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "System messages must be leading.",
                ));
            }
            for p in &m.content {
                match p {
                    ContentPart::Text(t) => system_parts.push(t.text.clone()),
                    _ => {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "System messages must not contain tool content.",
                        ))
                    }
                }
            }
            continue;
        }
        seen_non_system = true;
        if m.role == "tool" {
            let mut tool_results: Vec<Value> = Vec::new();
            for p in &m.content {
                match p {
                    ContentPart::ToolResult(tr) => {
                        let cid = tr
                            .call_id
                            .as_ref()
                            .map(|s| s.trim().to_string())
                            .unwrap_or_default();
                        if tr.call_id.is_none() || cid.is_empty() {
                            return Err(ConduitError::new(
                                "InvalidRequestError",
                                "Tool result callId is required for Anthropic.",
                            ));
                        }
                        let content_value = tr.content.clone();
                        let mut block = serde_json::Map::new();
                        block.insert("type".to_string(), Value::String("tool_result".to_string()));
                        block.insert(
                            "tool_use_id".to_string(),
                            Value::String(tr.call_id.clone().unwrap()),
                        );
                        block.insert("content".to_string(), Value::String(content_value));
                        tool_results.push(Value::Object(block));
                    }
                    _ => {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Tool messages must contain tool_result parts.",
                        ))
                    }
                }
            }
            anthropic_messages.push(serde_json::json!({"role":"user","content":tool_results}));
            continue;
        }
        // user or assistant
        if m.role != "user" && m.role != "assistant" {
            return Err(ConduitError::new(
                "ProtocolError",
                "Invalid message role for Anthropic.",
            ));
        }
        let mut blocks: Vec<Value> = Vec::new();
        for p in &m.content {
            match p {
                ContentPart::Text(t) => {
                    blocks.push(serde_json::json!({"type":"text","text":t.text}))
                }
                ContentPart::ToolCall(tc) => {
                    if m.role != "assistant" {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Only assistant messages may contain tool_call parts.",
                        ));
                    }
                    let id = tc
                        .id
                        .as_ref()
                        .map(|s| s.trim().to_string())
                        .unwrap_or_default();
                    if tc.id.is_none() || id.is_empty() {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Tool call id is required for Anthropic.",
                        ));
                    }
                    blocks.push(serde_json::json!({"type":"tool_use","id":tc.id.clone().unwrap(),"name":tc.name,"input":tc.arguments}));
                }
                ContentPart::ToolResult(_) => {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "Only tool messages may contain tool_result parts.",
                    ))
                }
            }
        }
        let has_tool_use = blocks
            .iter()
            .any(|b| b.get("type").and_then(|v| v.as_str()) == Some("tool_use"));
        let val = if !has_tool_use
            && blocks.len() == 1
            && blocks[0].get("type").and_then(|v| v.as_str()) == Some("text")
        {
            let txt = blocks[0]
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            serde_json::json!({"role":m.role,"content":txt})
        } else {
            serde_json::json!({"role":m.role,"content":blocks})
        };
        anthropic_messages.push(val);
    }
    let system: Option<Value> = if system_parts.len() == 1 {
        Some(Value::String(system_parts[0].clone()))
    } else if system_parts.len() > 1 {
        Some(Value::Array(
            system_parts
                .iter()
                .map(|t| serde_json::json!({"type":"text","text":t}))
                .collect(),
        ))
    } else {
        None
    };
    let max_tokens = req.max_output_tokens.ok_or_else(|| {
        ConduitError::new(
            "InvalidRequestError",
            "maxOutputTokens is required for Anthropic.",
        )
    })?;
    let mut body = serde_json::Map::new();
    if let Some(po) = &req.provider_options {
        for (k, v) in po {
            body.insert(k.clone(), v.clone());
        }
    }
    body.insert("model".to_string(), Value::String(model.to_string()));
    body.insert("messages".to_string(), Value::Array(anthropic_messages));
    body.insert("stream".to_string(), Value::Bool(streaming));
    body.insert("max_tokens".to_string(), Value::Number(max_tokens.into()));
    if let Some(s) = system {
        body.insert("system".to_string(), s);
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
            "stop_sequences".to_string(),
            Value::Array(stop.iter().map(|s| Value::String(s.clone())).collect()),
        );
    }
    if let Some(tools) = &req.tools {
        let wire: Vec<Value> = tools
            .iter()
            .map(|t| {
                let mut o = serde_json::Map::new();
                o.insert("name".to_string(), Value::String(t.name.clone()));
                if let Some(d) = &t.description {
                    o.insert("description".to_string(), Value::String(d.clone()));
                }
                o.insert("input_schema".to_string(), t.input_schema.clone());
                Value::Object(o)
            })
            .collect();
        body.insert("tools".to_string(), Value::Array(wire));
    }
    if let Some(tc) = &req.tool_choice {
        let v = match tc {
            ToolChoice::Auto => serde_json::json!({"type":"auto"}),
            ToolChoice::None_ => serde_json::json!({"type":"none"}),
            ToolChoice::Required => serde_json::json!({"type":"any"}),
            ToolChoice::Named(n) => serde_json::json!({"type":"tool","name":n}),
        };
        body.insert("tool_choice".to_string(), v);
    }
    Ok(Value::Object(body))
}

pub(crate) fn anthropic_error(
    status: u16,
    body: &str,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> ConduitError {
    let mut details = HashMap::new();
    let mut rid = request_id;
    if let Ok(v) = serde_json::from_str::<Value>(body) {
        if let Some(obj) = v.as_object() {
            let err_obj: Option<&serde_json::Map<String, Value>> =
                obj.get("error").and_then(|e| e.as_object()).or(Some(obj));
            if let Some(eo) = err_obj {
                if let Some(Value::String(s)) = eo.get("type") {
                    details.insert("type".to_string(), redact(s));
                }
                if let Some(Value::String(s)) = eo.get("message") {
                    details.insert("message".to_string(), redact(s));
                }
                if let Some(Value::String(s)) = eo.get("code") {
                    details.insert("code".to_string(), redact(s));
                }
            }
            if rid.is_none() {
                if let Some(Value::String(s)) = obj.get("request_id") {
                    rid = Some(redact(s));
                } else if let Some(Value::String(s)) = v.get("request_id") {
                    rid = Some(redact(s));
                }
            }
        }
    }
    if rid.is_none() {
        if let Ok(v) = serde_json::from_str::<Value>(body) {
            if let Some(Value::String(s)) = v.get("request_id") {
                rid = Some(redact(s));
            }
        }
    }
    if !details.is_empty() || rid.is_some() {
        let mut d = details.clone();
        if d.is_empty() {
            d.insert(
                "message".to_string(),
                format!("Provider returned HTTP {}.", status),
            );
        }
        return http_failure(status, d, rid, false);
    }
    let mut e = ConduitError::new(
        "ProviderError",
        &format!("Provider returned HTTP {}.", status),
    )
    .with_status(status);
    if let Some(r) = rid {
        e = e.with_request_id(r);
    }
    if !details.is_empty() {
        e = e.with_details(details.clone());
        if let Some(code) = details
            .get("type")
            .cloned()
            .or(details.get("code").cloned())
        {
            e = e.with_provider_code(code);
        }
    }
    e
}

pub(crate) fn decode_anthropic(
    value: Value,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> Result<GenerationResponse, ConduitError> {
    let obj = value.as_object().ok_or_else(|| {
        ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Anthropic response.",
        )
    })?;
    if obj.get("error").is_some() {
        return Err(anthropic_error(200, &value.to_string(), request_id, redact));
    }
    if obj.get("type").and_then(|v| v.as_str()) != Some("message")
        || obj.get("role").and_then(|v| v.as_str()) != Some("assistant")
    {
        return Err(ConduitError::new(
            "ProtocolError",
            "Malformed or unsupported Anthropic response.",
        ));
    }
    let id = obj
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            )
        })?
        .to_string();
    let model = obj
        .get("model")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            )
        })?
        .to_string();
    let content_arr = obj
        .get("content")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            )
        })?;
    // stop_reason validation
    let stop_reason_val = obj.get("stop_reason");
    match stop_reason_val {
        Some(Value::String(_)) | Some(Value::Null) | None => {}
        _ => {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            ))
        }
    }
    if let Some(v) = obj.get("stop_sequence") {
        if !v.is_null() && v.as_str().is_none() {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            ));
        }
    }
    let mut content: Vec<ContentPart> = Vec::new();
    let mut has_tool_use = false;
    let mut native_thinking: Vec<Value> = Vec::new();
    for block in content_arr {
        let bobj = block.as_object().ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            )
        })?;
        let btype = bobj.get("type").and_then(|v| v.as_str()).ok_or_else(|| {
            ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            )
        })?;
        match btype {
            "text" => {
                let txt = bobj.get("text").and_then(|v| v.as_str()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                content.push(ContentPart::Text(TextPart {
                    part_type: "text".to_string(),
                    text: txt.to_string(),
                }));
            }
            "tool_use" => {
                let bid = bobj.get("id").and_then(|v| v.as_str()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                let name = bobj.get("name").and_then(|v| v.as_str()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                let input = bobj.get("input").ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                if input != &Value::Null && !input.is_object() && !input.is_array() {
                    // input must be object; but allow any json object, also null not allowed
                    if !input.is_object() {
                        return Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Anthropic response.",
                        ));
                    }
                }
                if input.is_array() {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    ));
                }
                // ensure JSON serializable
                serde_json::to_string(input).map_err(|_| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                has_tool_use = true;
                content.push(ContentPart::ToolCall(ToolCallPart {
                    part_type: "tool_call".to_string(),
                    id: Some(bid.to_string()),
                    name: name.to_string(),
                    arguments: input.clone(),
                }));
            }
            "thinking" => {
                let thinking = bobj
                    .get("thinking")
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| {
                        ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Anthropic response.",
                        )
                    })?;
                if let Some(sig) = bobj.get("signature") {
                    if !sig.is_null() && sig.as_str().is_none() {
                        return Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Anthropic response.",
                        ));
                    }
                }
                serde_json::to_string(block).map_err(|_| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                native_thinking.push(block.clone());
            }
            "redacted_thinking" => {
                let data = bobj.get("data").and_then(|v| v.as_str()).ok_or_else(|| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                let _ = data;
                serde_json::to_string(block).map_err(|_| {
                    ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    )
                })?;
                native_thinking.push(block.clone());
            }
            _ => {
                return Err(ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Anthropic response.",
                ));
            }
        }
    }
    let mut usage: Option<Usage> = None;
    if let Some(uval) = obj.get("usage").and_then(|v| v.as_object()) {
        let mut us = Usage::default();
        let mut has = false;
        if let Some(Value::Number(n)) = uval.get("input_tokens") {
            if let Some(v) = n.as_u64() {
                us.input_tokens = Some(v as u32);
                has = true;
            } else {
                return Err(ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Anthropic response.",
                ));
            }
        } else if uval.contains_key("input_tokens") {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            ));
        }
        if let Some(Value::Number(n)) = uval.get("output_tokens") {
            if let Some(v) = n.as_u64() {
                us.output_tokens = Some(v as u32);
                has = true;
            } else {
                return Err(ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Anthropic response.",
                ));
            }
        } else if uval.contains_key("output_tokens") {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Anthropic response.",
            ));
        }
        if has {
            usage = Some(us);
        }
        // validate cache tokens if present
        for k in &["cache_creation_input_tokens", "cache_read_input_tokens"] {
            if let Some(v) = uval.get(*k) {
                if let Some(n) = v.as_u64() {
                    let _ = n;
                } else {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Anthropic response.",
                    ));
                }
            }
        }
    }
    let stop_reason = obj
        .get("stop_reason")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string());
    let mut meta: HashMap<String, Value> = HashMap::new();
    if let Some(rid) = request_id.clone() {
        meta.insert("requestId".to_string(), Value::String(rid));
    }
    if let Some(sr) = &stop_reason {
        meta.insert("finishReason".to_string(), Value::String(redact(sr)));
    }
    if let Some(Value::String(s)) = obj.get("stop_sequence") {
        if !s.is_empty() {
            meta.insert("stop_sequence".to_string(), Value::String(redact(s)));
        }
    }
    if let Some(uval) = obj.get("usage").and_then(|v| v.as_object()) {
        for k in &["cache_creation_input_tokens", "cache_read_input_tokens"] {
            if let Some(v) = uval.get(*k) {
                meta.insert(k.to_string(), v.clone());
            }
        }
        if let Some(v) = uval.get("cache_creation") {
            meta.insert("cache_creation".to_string(), v.clone());
        }
    }
    if !native_thinking.is_empty() {
        meta.insert(
            "thinking".to_string(),
            Value::Array(native_thinking.clone()),
        );
    }
    let finish = match stop_reason.as_deref() {
        Some("end_turn") | Some("stop_sequence") => "stop",
        Some("max_tokens") => "length",
        Some("tool_use") => "tool_call",
        Some("refusal") => "content_filter",
        _ => "other",
    };
    let final_finish = if has_tool_use { "tool_call" } else { finish };
    Ok(GenerationResponse {
        id: Some(redact(&id)),
        model: Some(redact(&model)),
        content,
        finish_reason: final_finish.to_string(),
        usage,
        provider_metadata: meta,
    })
}

pub(crate) fn anthropic_sse_stream<R: BufRead + Send + 'static>(
    mut reader: R,
    request_id: Option<String>,
    redact: Arc<dyn Fn(&str) -> String + Send + Sync>,
    deadline: Option<Instant>,
    signal: Option<Arc<AtomicBool>>,
) -> impl Iterator<Item = Result<StreamEvent, ConduitError>> + Send {
    struct State<R> {
        reader: R,
        line: String,
        data: Vec<String>,
        event: String,
        started: bool,
        id: Option<String>,
        model: Option<String>,
        stop_reason: Option<String>,
        usage: Option<Usage>,
        final_blocks: HashMap<u32, Value>,
        tool_accum: HashMap<u32, HashMap<String, String>>,
        deadline: Option<Instant>,
        signal: Option<Arc<AtomicBool>>,
        redact: Arc<dyn Fn(&str) -> String + Send + Sync>,
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
                    if Instant::now() >= d {
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
                            "Malformed or incomplete Anthropic stream.",
                        )));
                    }
                    if !self.done {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Anthropic stream.",
                        )));
                    }
                    return None;
                }
                let trimmed = self
                    .line
                    .trim_end_matches(|c| c == '\r' || c == '\n')
                    .to_string();
                if trimmed.is_empty() {
                    if self.data.is_empty() && self.event.is_empty() {
                        continue;
                    }
                    let data_str = self.data.join("\n");
                    let ev_name = self.event.clone();
                    self.data.clear();
                    self.event.clear();
                    if data_str.is_empty() {
                        continue;
                    }
                    let v: Value = match serde_json::from_str(&data_str) {
                        Ok(v) => v,
                        Err(_) => {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Anthropic stream.",
                            )))
                        }
                    };
                    if !v.is_object() {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Anthropic stream.",
                        )));
                    }
                    let v_type = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
                    if v_type == "error" || ev_name == "error" {
                        let body = serde_json::to_string(&v).unwrap_or_default();
                        return Some(Err(anthropic_error(
                            200,
                            &body,
                            self.request_id.clone(),
                            &*self.redact,
                        )));
                    }
                    let obj = v.as_object().unwrap().clone();
                    let typ = obj
                        .get("type")
                        .and_then(|x| x.as_str())
                        .unwrap_or("")
                        .to_string();
                    if typ == "message_start" {
                        if self.started {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Anthropic stream.",
                            )));
                        }
                        let msg = match obj.get("message").and_then(|x| x.as_object()) {
                            Some(m) => m.clone(),
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        let mid = msg
                            .get("id")
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        let mmodel = msg
                            .get("model")
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        if mid.is_none() || mmodel.is_none() {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Anthropic stream.",
                            )));
                        }
                        self.id = mid;
                        self.model = mmodel;
                        if let Some(usage_obj) = msg.get("usage").and_then(|x| x.as_object()) {
                            let mut us = Usage::default();
                            let mut has = false;
                            if let Some(Value::Number(n)) = usage_obj.get("input_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.input_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if let Some(Value::Number(n)) = usage_obj.get("output_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.output_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if has {
                                self.usage = Some(match &self.usage {
                                    Some(prev) => {
                                        let mut n = prev.clone();
                                        if let Some(v) = us.input_tokens {
                                            n.input_tokens = Some(v);
                                        }
                                        if let Some(v) = us.output_tokens {
                                            n.output_tokens = Some(v);
                                        }
                                        n
                                    }
                                    None => us,
                                });
                            }
                        }
                        self.started = true;
                        self.pending.push(StreamEvent::Start {
                            id: self.id.clone().map(|s| (self.redact)(&s)),
                            model: self.model.clone().map(|s| (self.redact)(&s)),
                        });
                        if let Some(u) = &self.usage {
                            self.pending.push(StreamEvent::Usage { usage: u.clone() });
                        }
                        if !self.pending.is_empty() {
                            return Some(Ok(self.pending.remove(0)));
                        }
                        continue;
                    }
                    if !self.started && typ != "ping" {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Anthropic stream.",
                        )));
                    }
                    if typ == "ping" {
                        continue;
                    }
                    if typ == "content_block_start" {
                        let idx = match obj.get("index").and_then(|x| x.as_u64()) {
                            Some(v) => v as u32,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        let block = match obj.get("content_block").and_then(|x| x.as_object()) {
                            Some(b) => b.clone(),
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        let btype = block.get("type").and_then(|x| x.as_str()).unwrap_or("");
                        match btype {
                            "text" => {
                                let txt = block
                                    .get("text")
                                    .and_then(|x| x.as_str())
                                    .unwrap_or("")
                                    .to_string();
                                self.final_blocks
                                    .insert(idx, serde_json::json!({"type":"text","text":txt}));
                            }
                            "tool_use" => {
                                let bid = match block.get("id").and_then(|x| x.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                let bname = match block.get("name").and_then(|x| x.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                let mut m = HashMap::new();
                                m.insert("id".to_string(), bid.clone());
                                m.insert("name".to_string(), bname.clone());
                                m.insert("arguments".to_string(), "".to_string());
                                self.tool_accum.insert(idx, m);
                                self.final_blocks.insert(idx, serde_json::json!({"type":"tool_use","id":bid,"name":bname,"input":{}}));
                                self.pending.push(StreamEvent::ToolCallDelta {
                                    index: idx,
                                    id: Some(bid),
                                    name: Some(bname),
                                    arguments_delta: None,
                                });
                            }
                            "thinking" | "redacted_thinking" => {
                                if btype == "thinking" {
                                    if block.get("thinking").and_then(|x| x.as_str()).is_none() {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )));
                                    }
                                    if let Some(sig) = block.get("signature") {
                                        if !sig.is_null() && sig.as_str().is_none() {
                                            return Some(Err(ConduitError::new(
                                                "ProtocolError",
                                                "Malformed or incomplete Anthropic stream.",
                                            )));
                                        }
                                    }
                                } else {
                                    if block.get("data").and_then(|x| x.as_str()).is_none() {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )));
                                    }
                                }
                                if serde_json::to_string(&Value::Object(block.clone())).is_err() {
                                    return Some(Err(ConduitError::new(
                                        "ProtocolError",
                                        "Malformed or incomplete Anthropic stream.",
                                    )));
                                }
                                let mut stored = serde_json::Map::new();
                                stored.insert("type".to_string(), Value::String(btype.to_string()));
                                if btype == "thinking" {
                                    stored.insert(
                                        "thinking".to_string(),
                                        block.get("thinking").cloned().unwrap(),
                                    );
                                    if let Some(sig) = block.get("signature") {
                                        if !sig.is_null() {
                                            stored.insert("signature".to_string(), sig.clone());
                                        }
                                    }
                                } else {
                                    stored.insert(
                                        "data".to_string(),
                                        block.get("data").cloned().unwrap(),
                                    );
                                }
                                self.final_blocks.insert(idx, Value::Object(stored));
                            }
                            _ => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        }
                        if !self.pending.is_empty() {
                            return Some(Ok(self.pending.remove(0)));
                        }
                        continue;
                    }
                    if typ == "content_block_delta" {
                        let idx = match obj.get("index").and_then(|x| x.as_u64()) {
                            Some(v) => v as u32,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        let delta = match obj.get("delta").and_then(|x| x.as_object()) {
                            Some(d) => d.clone(),
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        let dtype = delta.get("type").and_then(|x| x.as_str()).unwrap_or("");
                        match dtype {
                            "text_delta" => {
                                let txt = match delta.get("text").and_then(|x| x.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                if let Some(block) = self.final_blocks.get_mut(&idx) {
                                    if let Some(o) = block.as_object_mut() {
                                        if o.get("type").and_then(|x| x.as_str()) == Some("text") {
                                            let cur = o
                                                .get("text")
                                                .and_then(|x| x.as_str())
                                                .unwrap_or("")
                                                .to_string();
                                            o.insert("text".to_string(), Value::String(cur + &txt));
                                        } else {
                                            // create if missing
                                            o.insert(
                                                "type".to_string(),
                                                Value::String("text".to_string()),
                                            );
                                            o.insert(
                                                "text".to_string(),
                                                Value::String(txt.clone()),
                                            );
                                        }
                                    }
                                } else {
                                    self.final_blocks.insert(
                                        idx,
                                        serde_json::json!({"type":"text","text":txt.clone()}),
                                    );
                                }
                                self.pending.push(StreamEvent::TextDelta {
                                    index: 0,
                                    text: txt,
                                });
                            }
                            "input_json_delta" => {
                                let frag = match delta.get("partial_json").and_then(|x| x.as_str())
                                {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                let cur = self.tool_accum.get_mut(&idx);
                                if cur.is_none() {
                                    return Some(Err(ConduitError::new(
                                        "ProtocolError",
                                        "Malformed or incomplete Anthropic stream.",
                                    )));
                                }
                                let entry = cur.unwrap();
                                let cur_args = entry.get("arguments").cloned().unwrap_or_default();
                                entry.insert("arguments".to_string(), cur_args + &frag);
                                if !frag.is_empty() {
                                    self.pending.push(StreamEvent::ToolCallDelta {
                                        index: idx,
                                        id: None,
                                        name: None,
                                        arguments_delta: Some(frag),
                                    });
                                }
                            }
                            "thinking_delta" => {
                                let th = match delta.get("thinking").and_then(|x| x.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                if let Some(block) = self.final_blocks.get_mut(&idx) {
                                    if let Some(o) = block.as_object_mut() {
                                        if o.get("type").and_then(|x| x.as_str())
                                            != Some("thinking")
                                        {
                                            return Some(Err(ConduitError::new(
                                                "ProtocolError",
                                                "Malformed or incomplete Anthropic stream.",
                                            )));
                                        }
                                        let cur = o
                                            .get("thinking")
                                            .and_then(|x| x.as_str())
                                            .unwrap_or("")
                                            .to_string();
                                        o.insert("thinking".to_string(), Value::String(cur + &th));
                                    }
                                } else {
                                    return Some(Err(ConduitError::new(
                                        "ProtocolError",
                                        "Malformed or incomplete Anthropic stream.",
                                    )));
                                }
                            }
                            "signature_delta" => {
                                let sig = match delta.get("signature").and_then(|x| x.as_str()) {
                                    Some(s) => s.to_string(),
                                    None => {
                                        return Some(Err(ConduitError::new(
                                            "ProtocolError",
                                            "Malformed or incomplete Anthropic stream.",
                                        )))
                                    }
                                };
                                if let Some(block) = self.final_blocks.get_mut(&idx) {
                                    if let Some(o) = block.as_object_mut() {
                                        if o.get("type").and_then(|x| x.as_str())
                                            != Some("thinking")
                                        {
                                            return Some(Err(ConduitError::new(
                                                "ProtocolError",
                                                "Malformed or incomplete Anthropic stream.",
                                            )));
                                        }
                                        o.insert("signature".to_string(), Value::String(sig));
                                    }
                                } else {
                                    return Some(Err(ConduitError::new(
                                        "ProtocolError",
                                        "Malformed or incomplete Anthropic stream.",
                                    )));
                                }
                            }
                            _ => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        }
                        if !self.pending.is_empty() {
                            return Some(Ok(self.pending.remove(0)));
                        }
                        continue;
                    }
                    if typ == "content_block_stop" {
                        let _idx = match obj.get("index").and_then(|x| x.as_u64()) {
                            Some(v) => v as u32,
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        continue;
                    }
                    if typ == "message_delta" {
                        let delta = match obj.get("delta").and_then(|x| x.as_object()) {
                            Some(d) => d.clone(),
                            None => {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )))
                            }
                        };
                        if let Some(v) = delta.get("stop_reason") {
                            if !v.is_null() && v.as_str().is_none() {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Anthropic stream.",
                                )));
                            }
                            if let Some(s) = v.as_str() {
                                self.stop_reason = Some(s.to_string());
                            }
                        }
                        if let Some(uobj) = obj.get("usage").and_then(|x| x.as_object()) {
                            let mut us = Usage::default();
                            let mut has = false;
                            if let Some(Value::Number(n)) = uobj.get("input_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.input_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if let Some(Value::Number(n)) = uobj.get("output_tokens") {
                                if let Some(v) = n.as_u64() {
                                    us.output_tokens = Some(v as u32);
                                    has = true;
                                }
                            }
                            if has {
                                self.usage = Some(match &self.usage {
                                    Some(prev) => {
                                        let mut n = prev.clone();
                                        if let Some(v) = us.input_tokens {
                                            n.input_tokens = Some(v);
                                        }
                                        if let Some(v) = us.output_tokens {
                                            n.output_tokens = Some(v);
                                        }
                                        n
                                    }
                                    None => us,
                                });
                                self.pending.push(StreamEvent::Usage {
                                    usage: self.usage.clone().unwrap(),
                                });
                            }
                        }
                        if !self.pending.is_empty() {
                            return Some(Ok(self.pending.remove(0)));
                        }
                        continue;
                    }
                    if typ == "message_stop" {
                        if self.stop_reason.is_none() {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Anthropic stream.",
                            )));
                        }
                        let mut sorted: Vec<(u32, Value)> = self
                            .final_blocks
                            .iter()
                            .map(|(k, v)| (*k, v.clone()))
                            .collect();
                        sorted.sort_by_key(|(k, _)| *k);
                        let mut content_blocks: Vec<Value> = Vec::new();
                        for (idx, blk) in sorted {
                            let btype = blk.get("type").and_then(|x| x.as_str()).unwrap_or("");
                            match btype {
                                "text" => {
                                    let txt = blk
                                        .get("text")
                                        .and_then(|x| x.as_str())
                                        .unwrap_or("")
                                        .to_string();
                                    content_blocks
                                        .push(serde_json::json!({"type":"text","text":txt}));
                                }
                                "tool_use" => {
                                    let cur = self.tool_accum.get(&idx);
                                    let input_str = cur
                                        .and_then(|m| m.get("arguments"))
                                        .cloned()
                                        .unwrap_or_default();
                                    let mut input_val: Value = serde_json::json!({});
                                    if !input_str.is_empty() {
                                        match serde_json::from_str::<Value>(&input_str) {
                                            Ok(v) => input_val = v,
                                            Err(_) => {
                                                return Some(Err(ConduitError::new(
                                                    "ProtocolError",
                                                    "Malformed or incomplete Anthropic stream.",
                                                )))
                                            }
                                        }
                                    }
                                    let id = cur.and_then(|m| m.get("id")).cloned().unwrap_or_else(
                                        || {
                                            blk.get("id")
                                                .and_then(|x| x.as_str())
                                                .unwrap_or("")
                                                .to_string()
                                        },
                                    );
                                    let name = cur
                                        .and_then(|m| m.get("name"))
                                        .cloned()
                                        .unwrap_or_else(|| {
                                            blk.get("name")
                                                .and_then(|x| x.as_str())
                                                .unwrap_or("")
                                                .to_string()
                                        });
                                    content_blocks.push(serde_json::json!({"type":"tool_use","id":id,"name":name,"input":input_val}));
                                }
                                "thinking" => {
                                    let th = blk
                                        .get("thinking")
                                        .and_then(|x| x.as_str())
                                        .unwrap_or("")
                                        .to_string();
                                    let mut o = serde_json::Map::new();
                                    o.insert(
                                        "type".to_string(),
                                        Value::String("thinking".to_string()),
                                    );
                                    o.insert("thinking".to_string(), Value::String(th));
                                    if let Some(sig) = blk.get("signature").and_then(|x| x.as_str())
                                    {
                                        o.insert(
                                            "signature".to_string(),
                                            Value::String(sig.to_string()),
                                        );
                                    }
                                    content_blocks.push(Value::Object(o));
                                }
                                "redacted_thinking" => {
                                    let data = blk
                                        .get("data")
                                        .and_then(|x| x.as_str())
                                        .unwrap_or("")
                                        .to_string();
                                    content_blocks.push(
                                        serde_json::json!({"type":"redacted_thinking","data":data}),
                                    );
                                }
                                _ => {}
                            }
                        }
                        let mut usage_val = serde_json::Map::new();
                        if let Some(u) = &self.usage {
                            usage_val.insert(
                                "input_tokens".to_string(),
                                Value::Number((u.input_tokens.unwrap_or(0) as u64).into()),
                            );
                            usage_val.insert(
                                "output_tokens".to_string(),
                                Value::Number((u.output_tokens.unwrap_or(0) as u64).into()),
                            );
                        } else {
                            usage_val.insert("input_tokens".to_string(), Value::Number(0.into()));
                            usage_val.insert("output_tokens".to_string(), Value::Number(0.into()));
                        }
                        let response_input = serde_json::json!({
                            "type":"message",
                            "role":"assistant",
                            "id": self.id.clone().unwrap_or_else(|| "msg_unknown".to_string()),
                            "model": self.model.clone().unwrap_or_else(|| "unknown".to_string()),
                            "content": content_blocks,
                            "stop_reason": self.stop_reason.clone().unwrap(),
                            "stop_sequence": Value::Null,
                            "usage": usage_val
                        });
                        match decode_anthropic(
                            response_input,
                            self.request_id.clone(),
                            &*self.redact,
                        ) {
                            Ok(resp) => {
                                self.done = true;
                                return Some(Ok(StreamEvent::Done { response: resp }));
                            }
                            Err(e) => return Some(Err(e)),
                        }
                    }
                    // unknown type ignore
                    continue;
                } else {
                    // parse field
                    if let Some(rest) = trimmed.strip_prefix("event:") {
                        let v = rest.trim_start_matches(' ').to_string();
                        self.event = v;
                    } else if let Some(rest) = trimmed.strip_prefix("data:") {
                        let v = rest.trim_start_matches(' ').to_string();
                        self.data.push(v);
                    } else if trimmed.starts_with("data") {
                        self.data.push("".to_string());
                    } else if trimmed.starts_with("event") {
                        // malformed event line? treat as empty?
                        self.event = "".to_string();
                    }
                }
            }
        }
    }
    let state = State {
        reader: BufReader::new(reader),
        line: String::new(),
        data: Vec::new(),
        event: String::new(),
        started: false,
        id: None,
        model: None,
        stop_reason: None,
        usage: None,
        final_blocks: HashMap::new(),
        tool_accum: HashMap::new(),
        deadline,
        signal,
        redact,
        request_id,
        done: false,
        pending: Vec::new(),
    };
    state
}
