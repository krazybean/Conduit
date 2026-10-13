use crate::*;
use serde_json::Value;

pub(crate) fn encode_gemini_tools(tools: &Option<Vec<ToolDefinition>>) -> Option<Value> {
    let ts = tools.as_ref()?;
    let decls: Vec<Value> = ts
        .iter()
        .map(|t| {
            let mut o = serde_json::Map::new();
            o.insert("name".to_string(), Value::String(t.name.clone()));
            if let Some(d) = &t.description {
                o.insert("description".to_string(), Value::String(d.clone()));
            }
            o.insert("parametersJsonSchema".to_string(), t.input_schema.clone());
            Value::Object(o)
        })
        .collect();
    let mut inner = serde_json::Map::new();
    inner.insert("functionDeclarations".to_string(), Value::Array(decls));
    Some(Value::Array(vec![Value::Object(inner)]))
}

pub(crate) fn encode_gemini_tool_choice(choice: &Option<ToolChoice>) -> Option<Value> {
    let c = choice.as_ref()?;
    let v = match c {
        ToolChoice::Auto => serde_json::json!({"functionCallingConfig":{"mode":"AUTO"}}),
        ToolChoice::None_ => serde_json::json!({"functionCallingConfig":{"mode":"NONE"}}),
        ToolChoice::Required => serde_json::json!({"functionCallingConfig":{"mode":"ANY"}}),
        ToolChoice::Named(n) => {
            serde_json::json!({"functionCallingConfig":{"mode":"ANY","allowedFunctionNames":[n]}})
        }
    };
    Some(v)
}

pub(crate) fn encode_gemini_format(fmt: &Option<ResponseFormat>) -> Option<Value> {
    let f = fmt.as_ref()?;
    match f {
        ResponseFormat::Text => None,
        ResponseFormat::Json => Some(serde_json::json!({"responseMimeType":"application/json"})),
        ResponseFormat::JsonSchema { schema } => Some(
            serde_json::json!({"responseMimeType":"application/json","responseJsonSchema": schema}),
        ),
    }
}

pub(crate) fn gemini_error(
    status: u16,
    body: &str,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> ConduitError {
    let mut details: HashMap<String, String> = HashMap::new();
    if let Ok(v) = serde_json::from_str::<Value>(body) {
        if let Some(obj) = v.as_object() {
            let err_obj: Option<&serde_json::Map<String, Value>> =
                obj.get("error").and_then(|e| e.as_object()).or(Some(obj));
            if let Some(eo) = err_obj {
                if let Some(Value::String(s)) = eo.get("message") {
                    details.insert("message".to_string(), redact(s));
                }
                if let Some(Value::String(s)) = eo.get("status") {
                    details.insert("type".to_string(), redact(s));
                }
                if let Some(v) = eo.get("code") {
                    if let Some(n) = v.as_i64() {
                        details.insert("code".to_string(), n.to_string());
                    } else if let Some(n) = v.as_u64() {
                        details.insert("code".to_string(), n.to_string());
                    } else if let Some(s) = v.as_str() {
                        details.insert("code".to_string(), s.to_string());
                    }
                }
                if let Some(Value::String(s)) = eo.get("reason") {
                    details.insert("type".to_string(), redact(s));
                }
            }
        }
    }
    let mut d = details.clone();
    if d.is_empty() {
        // leave empty to let http_failure fill default message
    }
    http_failure(status, d, request_id, false)
}

pub(crate) fn encode_gemini_request(
    model: &str,
    req: &GenerationRequest,
    _streaming: bool,
) -> Result<Value, ConduitError> {
    let _ = model;
    // providerOptions owned check (including generationConfig subfields)
    if let Some(po) = &req.provider_options {
        let owned = [
            "contents",
            "systemInstruction",
            "generationConfig",
            "tools",
            "toolConfig",
            "safetySettings",
            "cachedContent",
        ];
        for k in po.keys() {
            if owned.contains(&k.as_str()) {
                return Err(ConduitError::new(
                    "InvalidRequestError",
                    "providerOptions conflicts with a Conduit-owned field.",
                ));
            }
        }
        if let Some(Value::Object(gc)) = po.get("generationConfig") {
            let conflict = [
                "maxOutputTokens",
                "temperature",
                "topP",
                "top_p",
                "stopSequences",
                "responseMimeType",
                "responseSchema",
                "responseJsonSchema",
                "candidateCount",
            ];
            for k in &conflict {
                if gc.contains_key(*k) {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "providerOptions.generationConfig conflicts with Conduit-owned field.",
                    ));
                }
            }
        }
        for v in po.values() {
            let mut seen = HashSet::new();
            json_validate(v, &mut seen)?;
        }
    }
    let mut system_parts: Vec<String> = Vec::new();
    let mut contents: Vec<Value> = Vec::new();
    let mut seen_non_system = false;
    for m in &req.messages {
        let role = &m.role;
        if role == "system" {
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
        if role == "tool" {
            let mut function_responses: Vec<Value> = Vec::new();
            for p in &m.content {
                match p {
                    ContentPart::ToolResult(tr) => {
                        let name = tr.name.as_ref().ok_or_else(|| {
                            ConduitError::new(
                                "InvalidRequestError",
                                "Tool result name is required for Gemini.",
                            )
                        })?;
                        if name.trim().is_empty() {
                            return Err(ConduitError::new(
                                "InvalidRequestError",
                                "Tool result name is required for Gemini.",
                            ));
                        }
                        let text = tr.content.clone();
                        let response_obj: Value = match serde_json::from_str::<Value>(&text) {
                            Ok(v) if v.is_object() => v,
                            Ok(v) if v.is_array() => serde_json::json!({"result": text}),
                            Ok(v) => {
                                if v.is_object() {
                                    v
                                } else {
                                    serde_json::json!({"result": text})
                                }
                            }
                            Err(_) => serde_json::json!({"result": text}),
                        };
                        let mut fr = serde_json::Map::new();
                        let mut inner = serde_json::Map::new();
                        if let Some(id) = &tr.call_id {
                            if !id.is_empty() {
                                inner.insert("id".to_string(), Value::String(id.clone()));
                            }
                        }
                        inner.insert("name".to_string(), Value::String(name.clone()));
                        inner.insert("response".to_string(), response_obj);
                        fr.insert("functionResponse".to_string(), Value::Object(inner));
                        function_responses.push(Value::Object(fr));
                    }
                    _ => {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Tool messages must contain tool_result parts.",
                        ))
                    }
                }
            }
            contents.push(serde_json::json!({"role":"user","parts": function_responses}));
            continue;
        }
        // user or assistant
        if role != "user" && role != "assistant" {
            return Err(ConduitError::new(
                "ProtocolError",
                "Invalid role for Gemini.",
            ));
        }
        let mut parts_out: Vec<Value> = Vec::new();
        for p in &m.content {
            match p {
                ContentPart::Text(t) => parts_out.push(serde_json::json!({"text": t.text})),
                ContentPart::ToolCall(tc) => {
                    if role != "assistant" {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Only assistant messages may contain tool_call parts.",
                        ));
                    }
                    if tc.name.trim().is_empty() {
                        return Err(ConduitError::new(
                            "InvalidRequestError",
                            "Tool call name required.",
                        ));
                    }
                    let mut fc = serde_json::Map::new();
                    if let Some(id) = &tc.id {
                        if !id.is_empty() {
                            fc.insert("id".to_string(), Value::String(id.clone()));
                        }
                    }
                    fc.insert("name".to_string(), Value::String(tc.name.clone()));
                    fc.insert("args".to_string(), tc.arguments.clone());
                    // validate JSON serializable
                    serde_json::to_string(&fc.get("args").unwrap()).map_err(|_| {
                        ConduitError::new(
                            "ProtocolError",
                            "Malformed or unsupported Gemini response.",
                        )
                    })?;
                    parts_out.push(serde_json::json!({"functionCall": fc}));
                }
                ContentPart::ToolResult(_) => {
                    return Err(ConduitError::new(
                        "InvalidRequestError",
                        "Only tool messages may contain tool_result parts.",
                    ))
                }
            }
        }
        let gemini_role = if role == "assistant" {
            "model"
        } else {
            role.as_str()
        };
        if gemini_role != "user" && gemini_role != "model" {
            return Err(ConduitError::new(
                "ProtocolError",
                "Invalid role for Gemini.",
            ));
        }
        contents.push(serde_json::json!({"role": gemini_role, "parts": parts_out}));
    }
    let mut body = serde_json::Map::new();
    if let Some(po) = &req.provider_options {
        for (k, v) in po {
            body.insert(k.clone(), v.clone());
        }
    }
    body.insert("contents".to_string(), Value::Array(contents));
    if !system_parts.is_empty() {
        body.insert(
            "systemInstruction".to_string(),
            serde_json::json!({"parts": system_parts.iter().map(|t| serde_json::json!({"text": t})).collect::<Vec<_>>()}),
        );
    }
    let mut generation_config = serde_json::Map::new();
    if let Some(n) = req.max_output_tokens {
        generation_config.insert("maxOutputTokens".to_string(), Value::Number(n.into()));
    }
    if let Some(v) = req.temperature {
        generation_config.insert(
            "temperature".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(v) = req.top_p {
        generation_config.insert(
            "topP".to_string(),
            serde_json::Number::from_f64(v).unwrap().into(),
        );
    }
    if let Some(stop) = &req.stop {
        generation_config.insert(
            "stopSequences".to_string(),
            Value::Array(stop.iter().map(|s| Value::String(s.clone())).collect()),
        );
    }
    if let Some(wf) = encode_gemini_format(&req.response_format) {
        if let Some(m) = wf.get("responseMimeType") {
            generation_config.insert("responseMimeType".to_string(), m.clone());
        }
        if let Some(s) = wf.get("responseJsonSchema") {
            generation_config.insert("responseJsonSchema".to_string(), s.clone());
        }
    }
    if !generation_config.is_empty() {
        body.insert(
            "generationConfig".to_string(),
            Value::Object(generation_config),
        );
    }
    if let Some(wt) = encode_gemini_tools(&req.tools) {
        body.insert("tools".to_string(), wt);
    }
    if let Some(wc) = encode_gemini_tool_choice(&req.tool_choice) {
        body.insert("toolConfig".to_string(), wc);
    }
    Ok(Value::Object(body))
}

pub(crate) fn gemini_usage_from_metadata(meta: &serde_json::Map<String, Value>) -> Option<Usage> {
    let mut usage = Usage::default();
    let mut has = false;
    if let Some(Value::Number(n)) = meta.get("promptTokenCount") {
        if let Some(v) = n.as_u64() {
            usage.input_tokens = Some(v as u32);
            has = true;
        }
    }
    if let Some(Value::Number(n)) = meta.get("candidatesTokenCount") {
        if let Some(v) = n.as_u64() {
            usage.output_tokens = Some(v as u32);
            has = true;
        }
    }
    if let Some(Value::Number(n)) = meta.get("totalTokenCount") {
        if let Some(v) = n.as_u64() {
            usage.total_tokens = Some(v as u32);
            has = true;
        }
    }
    if has {
        Some(usage)
    } else {
        None
    }
}

pub(crate) fn decode_gemini(
    value: Value,
    request_id: Option<String>,
    redact: &dyn Fn(&str) -> String,
) -> Result<GenerationResponse, ConduitError> {
    let obj = value.as_object().ok_or_else(|| {
        ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
    })?;
    if obj.get("error").and_then(|v| v.as_object()).is_some() {
        return Err(gemini_error(200, &value.to_string(), request_id, redact));
    }
    let candidates_val = obj.get("candidates");
    let candidates = match candidates_val.and_then(|v| v.as_array()) {
        Some(arr) if !arr.is_empty() => arr,
        _ => {
            // Check promptFeedback blockReason
            if let Some(pf) = obj.get("promptFeedback").and_then(|v| v.as_object()) {
                if let Some(Value::String(br)) = pf.get("blockReason") {
                    let usage = obj
                        .get("usageMetadata")
                        .and_then(|v| v.as_object())
                        .and_then(|m| gemini_usage_from_metadata(m));
                    let mut meta: HashMap<String, Value> = HashMap::new();
                    if let Some(rid) = request_id.clone() {
                        meta.insert("requestId".to_string(), Value::String(rid));
                    }
                    if let Some(Value::String(mv)) = obj.get("modelVersion") {
                        meta.insert("modelVersion".to_string(), Value::String(redact(mv)));
                    }
                    if let Some(Value::String(rid)) = obj.get("responseId") {
                        meta.insert("responseId".to_string(), Value::String(redact(rid)));
                    }
                    meta.insert("finishReason".to_string(), Value::String(redact(br)));
                    return Ok(GenerationResponse {
                        id: obj
                            .get("responseId")
                            .and_then(|v| v.as_str())
                            .map(|s| redact(s)),
                        model: obj
                            .get("modelVersion")
                            .and_then(|v| v.as_str())
                            .map(|s| redact(s)),
                        content: vec![],
                        finish_reason: "content_filter".to_string(),
                        usage,
                        provider_metadata: meta,
                    });
                }
            }
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Gemini response.",
            ));
        }
    };
    let candidate = candidates[0].as_object().ok_or_else(|| {
        ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
    })?;
    let content_obj = candidate
        .get("content")
        .and_then(|v| v.as_object())
        .ok_or_else(|| {
            ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
        })?;
    let parts = content_obj
        .get("parts")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
        })?;
    let mut normalized: Vec<ContentPart> = Vec::new();
    let mut has_function_call = false;
    let mut native_parts: Vec<Value> = Vec::new();
    for part in parts {
        let pobj = part.as_object().ok_or_else(|| {
            ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
        })?;
        if pobj.get("thought") == Some(&Value::Bool(true))
            || pobj
                .get("thoughtSignature")
                .and_then(|v| v.as_str())
                .is_some()
        {
            native_parts.push(part.clone());
            continue;
        }
        if let Some(Value::String(txt)) = pobj.get("text") {
            normalized.push(ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: txt.clone(),
            }));
        } else if let Some(fc) = pobj.get("functionCall").and_then(|v| v.as_object()) {
            let name = fc.get("name").and_then(|v| v.as_str()).ok_or_else(|| {
                ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
            })?;
            if name.trim().is_empty() {
                return Err(ConduitError::new(
                    "ProtocolError",
                    "Malformed or unsupported Gemini response.",
                ));
            }
            if let Some(v) = fc.get("id") {
                if !v.is_string() {
                    return Err(ConduitError::new(
                        "ProtocolError",
                        "Malformed or unsupported Gemini response.",
                    ));
                }
            }
            let args = fc
                .get("args")
                .cloned()
                .unwrap_or_else(|| Value::Object(Default::default()));
            // validate JSON serializable
            serde_json::to_string(&args).map_err(|_| {
                ConduitError::new("ProtocolError", "Malformed or unsupported Gemini response.")
            })?;
            has_function_call = true;
            let id = fc
                .get("id")
                .and_then(|v| v.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string());
            normalized.push(ContentPart::ToolCall(ToolCallPart {
                part_type: "tool_call".to_string(),
                id,
                name: name.to_string(),
                arguments: args,
            }));
        } else if pobj
            .get("functionResponse")
            .and_then(|v| v.as_object())
            .is_some()
        {
            continue;
        } else if pobj.get("thought") == Some(&Value::Bool(true))
            || pobj
                .get("thoughtSignature")
                .and_then(|v| v.as_str())
                .is_some()
            || pobj
                .get("executableCode")
                .and_then(|v| v.as_object())
                .is_some()
            || pobj
                .get("codeExecutionResult")
                .and_then(|v| v.as_object())
                .is_some()
        {
            native_parts.push(part.clone());
            continue;
        } else if pobj.is_empty() {
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Gemini response.",
            ));
        } else {
            // Unknown but maybe native thought field
            let is_thought = pobj.get("thought") == Some(&Value::Bool(true))
                || pobj
                    .get("thoughtSignature")
                    .and_then(|v| v.as_str())
                    .is_some();
            if is_thought {
                native_parts.push(part.clone());
                continue;
            }
            return Err(ConduitError::new(
                "ProtocolError",
                "Malformed or unsupported Gemini response.",
            ));
        }
    }
    let finish_raw = candidate.get("finishReason").and_then(|v| v.as_str());
    let mut finish = match finish_raw {
        Some("STOP") => "stop",
        Some("MAX_TOKENS") => "length",
        Some("SAFETY")
        | Some("RECITATION")
        | Some("BLOCKLIST")
        | Some("PROHIBITED_CONTENT")
        | Some("SPII")
        | Some("IMAGE_SAFETY") => "content_filter",
        Some(_) => "other",
        None => {
            if normalized.is_empty() && native_parts.is_empty() {
                "other"
            } else {
                "stop"
            }
        }
    }
    .to_string();
    if has_function_call && finish != "content_filter" {
        finish = "tool_call".to_string();
    }
    let usage = obj
        .get("usageMetadata")
        .and_then(|v| v.as_object())
        .and_then(|m| gemini_usage_from_metadata(m));
    let mut meta: HashMap<String, Value> = HashMap::new();
    if let Some(rid) = request_id.clone() {
        meta.insert("requestId".to_string(), Value::String(rid));
    }
    if let Some(fr) = finish_raw {
        meta.insert("finishReason".to_string(), Value::String(redact(fr)));
    }
    if let Some(Value::String(mv)) = obj.get("modelVersion") {
        meta.insert("modelVersion".to_string(), Value::String(redact(mv)));
    }
    if let Some(Value::String(rid)) = obj.get("responseId") {
        meta.insert("responseId".to_string(), Value::String(redact(rid)));
    }
    if let Some(um) = obj.get("usageMetadata").and_then(|v| v.as_object()) {
        for k in &[
            "cachedContentTokenCount",
            "thoughtsTokenCount",
            "toolUsePromptTokenCount",
        ] {
            if let Some(v) = um.get(*k) {
                if let Some(n) = v.as_u64() {
                    // safe integer check already via u64
                    meta.insert(k.to_string(), Value::Number(n.into()));
                }
            }
        }
    }
    if let Some(sr) = candidate.get("safetyRatings") {
        // ensure JSON serializable
        if serde_json::to_string(sr).is_ok() {
            meta.insert("safetyRatings".to_string(), sr.clone());
        }
    }
    if !native_parts.is_empty() {
        meta.insert("thinking".to_string(), Value::Array(native_parts));
    }
    let id = obj
        .get("responseId")
        .and_then(|v| v.as_str())
        .map(|s| redact(s));
    let model = obj
        .get("modelVersion")
        .and_then(|v| v.as_str())
        .map(|s| redact(s));
    Ok(GenerationResponse {
        id,
        model,
        content: normalized,
        finish_reason: finish,
        usage,
        provider_metadata: meta,
    })
}

pub(crate) fn gemini_sse_stream<R: BufRead + Send + 'static>(
    reader: R,
    request_id: Option<String>,
    redact: Arc<dyn Fn(&str) -> String + Send + Sync>,
    deadline: Option<Instant>,
    signal: Option<Arc<AtomicBool>>,
) -> impl Iterator<Item = Result<StreamEvent, ConduitError>> + Send {
    struct State<R> {
        reader: R,
        line: String,
        data: Vec<String>,
        started: bool,
        finish_reason: Option<String>,
        usage: Option<Usage>,
        aggregated: Vec<Value>,
        last_raw: Option<Value>,
        first_rid: Option<String>,
        first_mid: Option<String>,
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
                    // EOF: finalize
                    if !self.started {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Gemini stream.",
                        )));
                    }
                    if self.done {
                        return None;
                    }
                    // handle any buffered data
                    if !self.data.is_empty() {
                        let joined = self.data.join("\n");
                        self.data.clear();
                        if !joined.is_empty() && joined != "[DONE]" && !joined.trim().is_empty() {
                            let v: Value = match serde_json::from_str(&joined) {
                                Ok(v) => v,
                                Err(_) => {
                                    return Some(Err(ConduitError::new(
                                        "ProtocolError",
                                        "Malformed or incomplete Gemini stream.",
                                    )))
                                }
                            };
                            if !v.is_object() {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Gemini stream.",
                                )));
                            }
                            if v.get("error").and_then(|e| e.as_object()).is_some() {
                                return Some(Err(gemini_error(
                                    200,
                                    &joined,
                                    self.request_id.clone(),
                                    &*self.redact,
                                )));
                            }
                            let cands = v.get("candidates");
                            if cands.is_some() && cands.and_then(|v| v.as_array()).is_none() {
                                return Some(Err(ConduitError::new(
                                    "ProtocolError",
                                    "Malformed or incomplete Gemini stream.",
                                )));
                            }
                            if let Some(arr) = cands.and_then(|v| v.as_array()) {
                                if !arr.is_empty() {
                                    if let Some(cand) = arr[0].as_object() {
                                        if let Some(fr) =
                                            cand.get("finishReason").and_then(|v| v.as_str())
                                        {
                                            self.finish_reason = Some(fr.to_string());
                                        }
                                        if let Some(content) =
                                            cand.get("content").and_then(|v| v.as_object())
                                        {
                                            if let Some(parts) =
                                                content.get("parts").and_then(|v| v.as_array())
                                            {
                                                for part in parts {
                                                    let pobj = match part.as_object() {
                                                        Some(o) => o,
                                                        None => {
                                                            return Some(Err(ConduitError::new(
                                                                "ProtocolError",
                                                                "Malformed or incomplete Gemini stream.",
                                                            )))
                                                        }
                                                    };
                                                    if let Some(txt) =
                                                        pobj.get("text").and_then(|v| v.as_str())
                                                    {
                                                        if !txt.is_empty() {
                                                            self.aggregated.push(part.clone());
                                                            self.pending.push(
                                                                StreamEvent::TextDelta {
                                                                    index: 0,
                                                                    text: txt.to_string(),
                                                                },
                                                            );
                                                        }
                                                    } else if let Some(fc) = pobj
                                                        .get("functionCall")
                                                        .and_then(|v| v.as_object())
                                                    {
                                                        if fc
                                                            .get("name")
                                                            .and_then(|v| v.as_str())
                                                            .is_none()
                                                        {
                                                            return Some(Err(ConduitError::new(
                                                                "ProtocolError",
                                                                "Malformed or incomplete Gemini stream.",
                                                            )));
                                                        }
                                                        if let Some(v) = fc.get("id") {
                                                            if !v.is_string() {
                                                                return Some(Err(ConduitError::new("ProtocolError","Malformed or incomplete Gemini stream.")));
                                                            }
                                                        }
                                                        let args_str = serde_json::to_string(
                                                            fc.get("args").unwrap_or(
                                                                &Value::Object(Default::default()),
                                                            ),
                                                        )
                                                        .unwrap_or("{}".to_string());
                                                        self.aggregated.push(part.clone());
                                                        let idx = self
                                                            .aggregated
                                                            .iter()
                                                            .filter(|p| {
                                                                p.get("functionCall").is_some()
                                                            })
                                                            .count()
                                                            as u32
                                                            - 1;
                                                        self.pending.push(
                                                            StreamEvent::ToolCallDelta {
                                                                index: idx,
                                                                id: fc
                                                                    .get("id")
                                                                    .and_then(|v| v.as_str())
                                                                    .filter(|s| !s.is_empty())
                                                                    .map(|s| s.to_string()),
                                                                name: fc
                                                                    .get("name")
                                                                    .and_then(|v| v.as_str())
                                                                    .map(|s| s.to_string()),
                                                                arguments_delta: Some(args_str),
                                                            },
                                                        );
                                                    } else if pobj.get("thought")
                                                        == Some(&Value::Bool(true))
                                                        || pobj
                                                            .get("thoughtSignature")
                                                            .and_then(|v| v.as_str())
                                                            .is_some()
                                                        || pobj
                                                            .get("executableCode")
                                                            .and_then(|v| v.as_object())
                                                            .is_some()
                                                    {
                                                        self.aggregated.push(part.clone());
                                                    } else if pobj.is_empty() {
                                                        return Some(Err(ConduitError::new("ProtocolError","Malformed or incomplete Gemini stream.")));
                                                    } else {
                                                        let is_thought = pobj.get("thought")
                                                            == Some(&Value::Bool(true))
                                                            || pobj
                                                                .get("thoughtSignature")
                                                                .and_then(|v| v.as_str())
                                                                .is_some();
                                                        if is_thought {
                                                            self.aggregated.push(part.clone());
                                                        } else {
                                                            return Some(Err(ConduitError::new("ProtocolError","Malformed or incomplete Gemini stream.")));
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                            if let Some(um) = v.get("usageMetadata").and_then(|v| v.as_object()) {
                                if let Some(u) = gemini_usage_from_metadata(um) {
                                    let merged = match &self.usage {
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
                                    };
                                    self.usage = Some(merged.clone());
                                    self.pending.push(StreamEvent::Usage { usage: merged });
                                }
                            }
                            self.last_raw = Some(v);
                            if !self.pending.is_empty() {
                                return Some(Ok(self.pending.remove(0)));
                            }
                        }
                    }
                    // Build final response
                    let rid_final = self
                        .last_raw
                        .as_ref()
                        .and_then(|v| v.get("responseId"))
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                        .or(self.first_rid.clone());
                    let mid_final = self
                        .last_raw
                        .as_ref()
                        .and_then(|v| v.get("modelVersion"))
                        .and_then(|v| v.as_str())
                        .map(|s| s.to_string())
                        .or(self.first_mid.clone());
                    let mut final_input = serde_json::Map::new();
                    let mut cand = serde_json::Map::new();
                    cand.insert(
                        "content".to_string(),
                        serde_json::json!({"parts": self.aggregated.clone()}),
                    );
                    cand.insert(
                        "finishReason".to_string(),
                        Value::String(self.finish_reason.clone().unwrap_or("STOP".to_string())),
                    );
                    // preserve safetyRatings if present in last_raw
                    if let Some(last) = &self.last_raw {
                        if let Some(arr) = last.get("candidates").and_then(|v| v.as_array()) {
                            if let Some(c0) = arr.first().and_then(|v| v.as_object()) {
                                if let Some(sr) = c0.get("safetyRatings") {
                                    cand.insert("safetyRatings".to_string(), sr.clone());
                                }
                            }
                        }
                    }
                    final_input.insert(
                        "candidates".to_string(),
                        Value::Array(vec![Value::Object(cand)]),
                    );
                    if let Some(u) = &self.usage {
                        let mut um = serde_json::Map::new();
                        um.insert(
                            "promptTokenCount".to_string(),
                            Value::Number((u.input_tokens.unwrap_or(0) as u64).into()),
                        );
                        um.insert(
                            "candidatesTokenCount".to_string(),
                            Value::Number((u.output_tokens.unwrap_or(0) as u64).into()),
                        );
                        let tot = u
                            .total_tokens
                            .unwrap_or(u.input_tokens.unwrap_or(0) + u.output_tokens.unwrap_or(0));
                        um.insert(
                            "totalTokenCount".to_string(),
                            Value::Number((tot as u64).into()),
                        );
                        final_input.insert("usageMetadata".to_string(), Value::Object(um));
                    }
                    if let Some(rid) = rid_final {
                        final_input.insert("responseId".to_string(), Value::String(rid));
                    }
                    if let Some(mid) = mid_final {
                        final_input.insert("modelVersion".to_string(), Value::String(mid));
                    }
                    let resp = match decode_gemini(
                        Value::Object(final_input),
                        self.request_id.clone(),
                        &*self.redact,
                    ) {
                        Ok(r) => r,
                        Err(e) => return Some(Err(e)),
                    };
                    self.done = true;
                    return Some(Ok(StreamEvent::Done { response: resp }));
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
                    if data_str.is_empty() || data_str == "[DONE]" || data_str.trim().is_empty() {
                        continue;
                    }
                    let v: Value = match serde_json::from_str(&data_str) {
                        Ok(v) => v,
                        Err(_) => {
                            return Some(Err(ConduitError::new(
                                "ProtocolError",
                                "Malformed or incomplete Gemini stream.",
                            )))
                        }
                    };
                    if !v.is_object() {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Gemini stream.",
                        )));
                    }
                    if v.get("error").and_then(|e| e.as_object()).is_some() {
                        return Some(Err(gemini_error(
                            200,
                            &data_str,
                            self.request_id.clone(),
                            &*self.redact,
                        )));
                    }
                    let cands = v.get("candidates");
                    if cands.is_some() && cands.and_then(|x| x.as_array()).is_none() {
                        return Some(Err(ConduitError::new(
                            "ProtocolError",
                            "Malformed or incomplete Gemini stream.",
                        )));
                    }
                    if !self.started {
                        self.started = true;
                        let mid = v
                            .get("modelVersion")
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        let rid = v
                            .get("responseId")
                            .and_then(|x| x.as_str())
                            .map(|s| s.to_string());
                        if let Some(r) = &rid {
                            self.first_rid = Some(r.clone());
                        }
                        if let Some(m) = &mid {
                            self.first_mid = Some(m.clone());
                        }
                        let ev = StreamEvent::Start {
                            id: rid.clone().map(|s| (self.redact)(&s)),
                            model: mid.clone().map(|s| (self.redact)(&s)),
                        };
                        self.pending.push(ev);
                        // usage may be in same chunk, handle below but we already queued start
                    }
                    if let Some(arr) = cands.and_then(|x| x.as_array()) {
                        if !arr.is_empty() {
                            if let Some(cand) = arr[0].as_object() {
                                if let Some(fr) = cand.get("finishReason").and_then(|x| x.as_str())
                                {
                                    self.finish_reason = Some(fr.to_string());
                                }
                                if let Some(content) =
                                    cand.get("content").and_then(|x| x.as_object())
                                {
                                    if let Some(parts) =
                                        content.get("parts").and_then(|x| x.as_array())
                                    {
                                        for part in parts {
                                            let pobj =
                                                match part.as_object() {
                                                    Some(o) => o,
                                                    None => return Some(Err(ConduitError::new(
                                                        "ProtocolError",
                                                        "Malformed or incomplete Gemini stream.",
                                                    ))),
                                                };
                                            if let Some(txt) =
                                                pobj.get("text").and_then(|x| x.as_str())
                                            {
                                                if !txt.is_empty() {
                                                    self.aggregated.push(part.clone());
                                                    self.pending.push(StreamEvent::TextDelta {
                                                        index: 0,
                                                        text: txt.to_string(),
                                                    });
                                                }
                                            } else if let Some(fc) =
                                                pobj.get("functionCall").and_then(|x| x.as_object())
                                            {
                                                if fc.get("name").and_then(|x| x.as_str()).is_none()
                                                {
                                                    return Some(Err(ConduitError::new(
                                                        "ProtocolError",
                                                        "Malformed or incomplete Gemini stream.",
                                                    )));
                                                }
                                                if let Some(v) = fc.get("id") {
                                                    if !v.is_string() {
                                                        return Some(Err(ConduitError::new("ProtocolError","Malformed or incomplete Gemini stream.")));
                                                    }
                                                }
                                                // validate args serializable
                                                let args_val = fc
                                                    .get("args")
                                                    .cloned()
                                                    .unwrap_or(Value::Object(Default::default()));
                                                if serde_json::to_string(&args_val).is_err() {
                                                    return Some(Err(ConduitError::new(
                                                        "ProtocolError",
                                                        "Malformed or incomplete Gemini stream.",
                                                    )));
                                                }
                                                let args_str = serde_json::to_string(&args_val)
                                                    .unwrap_or("{}".to_string());
                                                self.aggregated.push(part.clone());
                                                let idx = self
                                                    .aggregated
                                                    .iter()
                                                    .filter(|p| p.get("functionCall").is_some())
                                                    .count()
                                                    as u32
                                                    - 1;
                                                self.pending.push(StreamEvent::ToolCallDelta {
                                                    index: idx,
                                                    id: fc
                                                        .get("id")
                                                        .and_then(|x| x.as_str())
                                                        .filter(|s| !s.is_empty())
                                                        .map(|s| s.to_string()),
                                                    name: fc
                                                        .get("name")
                                                        .and_then(|x| x.as_str())
                                                        .map(|s| s.to_string()),
                                                    arguments_delta: Some(args_str),
                                                });
                                            } else if pobj.get("thought")
                                                == Some(&Value::Bool(true))
                                                || pobj
                                                    .get("thoughtSignature")
                                                    .and_then(|x| x.as_str())
                                                    .is_some()
                                                || pobj
                                                    .get("executableCode")
                                                    .and_then(|x| x.as_object())
                                                    .is_some()
                                            {
                                                self.aggregated.push(part.clone());
                                                continue;
                                            } else if pobj.is_empty() {
                                                return Some(Err(ConduitError::new(
                                                    "ProtocolError",
                                                    "Malformed or incomplete Gemini stream.",
                                                )));
                                            } else {
                                                let is_thought = pobj.get("thought")
                                                    == Some(&Value::Bool(true))
                                                    || pobj
                                                        .get("thoughtSignature")
                                                        .and_then(|x| x.as_str())
                                                        .is_some();
                                                if is_thought {
                                                    self.aggregated.push(part.clone());
                                                    continue;
                                                }
                                                return Some(Err(ConduitError::new(
                                                    "ProtocolError",
                                                    "Malformed or incomplete Gemini stream.",
                                                )));
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if let Some(um) = v.get("usageMetadata").and_then(|x| x.as_object()) {
                        if let Some(u) = gemini_usage_from_metadata(um) {
                            let merged = match &self.usage {
                                Some(prev) => {
                                    let mut n = prev.clone();
                                    if let Some(val) = u.input_tokens {
                                        n.input_tokens = Some(val);
                                    }
                                    if let Some(val) = u.output_tokens {
                                        n.output_tokens = Some(val);
                                    }
                                    if let Some(val) = u.total_tokens {
                                        n.total_tokens = Some(val);
                                    }
                                    n
                                }
                                None => u.clone(),
                            };
                            self.usage = Some(merged.clone());
                            self.pending.push(StreamEvent::Usage { usage: merged });
                        }
                    }
                    self.last_raw = Some(v);
                    if !self.pending.is_empty() {
                        return Some(Ok(self.pending.remove(0)));
                    }
                    continue;
                } else {
                    // parse field
                    if let Some(rest) = trimmed.strip_prefix("data:") {
                        let v = rest.trim_start_matches(' ').to_string();
                        self.data.push(v);
                    } else if let Some(rest) = trimmed.strip_prefix("event:") {
                        // ignore event name
                        let _ = rest;
                    } else if trimmed.starts_with("data") {
                        self.data.push("".to_string());
                    }
                }
            }
        }
    }
    let state = State {
        reader: BufReader::new(reader),
        line: String::new(),
        data: Vec::new(),
        started: false,
        finish_reason: None,
        usage: None,
        aggregated: Vec::new(),
        last_raw: None,
        first_rid: None,
        first_mid: None,
        deadline,
        signal,
        redact,
        request_id,
        done: false,
        pending: Vec::new(),
    };
    state
}
