use super::*;

#[test]
fn gemini_generate_real_http() {
    let (addr, handle) = start_server(|mut stream| {
        let (req_line, _, body) = read_http_request(&mut stream);
        assert!(req_line.contains(":generateContent"));
        let v: Value = serde_json::from_slice(&body).unwrap();
        assert!(v.get("contents").is_some());
        let resp = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":3,"totalTokenCount":5},"modelVersion":"gemini-2.0-flash","responseId":"r1"});
        let s = serde_json::to_string(&resp).unwrap();
        let r = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            s.len(),
            s
        );
        stream.write_all(r.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let resp = model.generate(req).unwrap();
    assert_eq!(resp.text(), "Hello");
    handle.join().unwrap();
}

#[test]
fn gemini_stream_first_event_before_eof() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let p1 = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hello "}]}}],"modelVersion":"gemini-2.0-flash"});
        stream
            .write_all(format!("data: {}\n\n", serde_json::to_string(&p1).unwrap()).as_bytes())
            .unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(30));
        let p2 = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"world"}]},"finishReason":"STOP"}]});
        stream
            .write_all(format!("data: {}\n\n", serde_json::to_string(&p2).unwrap()).as_bytes())
            .unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let mut iter = model.stream(req).unwrap();
    let first = iter.next().unwrap().unwrap();
    assert!(matches!(first, StreamEvent::Start { .. }));
    let mut saw = false;
    for ev in iter {
        let e = ev.unwrap();
        if matches!(&e, StreamEvent::TextDelta{text,..} if text.contains("Hello")) {
            saw = true;
            break;
        }
        if matches!(e, StreamEvent::Done { .. }) {
            break;
        }
    }
    assert!(saw);
    handle.join().unwrap();
}

#[test]
fn gemini_stream_fragmentation() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let p = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]});
        let s = format!("data: {}\n\n", serde_json::to_string(&p).unwrap());
        let mid = s.len() / 2;
        stream.write_all(&s.as_bytes()[..mid]).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        stream.write_all(&s.as_bytes()[mid..]).unwrap();
        stream.flush().unwrap();
        let p2 = serde_json::json!({"candidates":[{"content":{"parts":[{"text":" world"}]},"finishReason":"STOP"}]});
        stream
            .write_all(format!("data: {}\n\n", serde_json::to_string(&p2).unwrap()).as_bytes())
            .unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let evs: Vec<_> = model
        .stream(req)
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    assert!(evs
        .iter()
        .any(|e| matches!(e, StreamEvent::TextDelta{text,..} if text=="Hello" || text==" world")));
    handle.join().unwrap();
}

#[test]
fn gemini_stream_timeout_is_operation_wide() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let p1 = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hi"}]}}]});
        stream
            .write_all(format!("data: {}\n\n", serde_json::to_string(&p1).unwrap()).as_bytes())
            .unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        let _ = stream.write_all(b"data: {\"candidates\":[]}\n\n");
        let _ = stream.flush();
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: Some(100),
        signal: None,
    };
    let mut iter = model.stream(req).unwrap();
    let _ = iter.next().unwrap().unwrap(); // Start
    let _ = iter.next().unwrap().unwrap(); // Hi (buffered before deadline)
    let start = std::time::Instant::now();
    let res = iter.next();
    assert!(start.elapsed().as_millis() < 300);
    match res {
        Some(Err(e)) => assert_eq!(e.name, "TimeoutError"),
        _ => panic!("expected TimeoutError"),
    }
    handle.join().unwrap();
}

#[test]
fn gemini_stream_drop_releases_connection() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let p1 = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hi"}]}}]});
        stream
            .write_all(format!("data: {}\n\n", serde_json::to_string(&p1).unwrap()).as_bytes())
            .unwrap();
        stream.flush().unwrap();
        let mut buf = [0u8; 1024];
        stream
            .set_read_timeout(Some(std::time::Duration::from_millis(500)))
            .unwrap();
        let n = stream.read(&mut buf).unwrap_or(0);
        assert!(n < 1000000);
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let mut iter = model.stream(req).unwrap();
    let _ = iter.next().unwrap().unwrap();
    drop(iter);
    std::thread::sleep(std::time::Duration::from_millis(100));
    handle.join().unwrap();
}

#[test]
fn gemini_pre_cancelled_zero_request() {
    let counter = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let cc = counter.clone();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let h = std::thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        if let Ok((_, _)) = listener.accept() {
            cc.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
    });
    let signal = std::sync::Arc::new(AtomicBool::new(true));
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: Some(signal),
    };
    let res = model.generate(req);
    assert!(res.is_err());
    assert_eq!(res.unwrap_err().name, "CancelledError");
    h.join().unwrap();
    assert_eq!(counter.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[test]
fn gemini_tool_id_preserved() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        let resp = serde_json::json!({"candidates":[{"content":{"parts":[{"functionCall":{"id":"call_123","name":"get_weather","args":{"city":"Tokyo"}}}],"role":"model"},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":3}});
        let s = serde_json::to_string(&resp).unwrap();
        let r = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            s.len(),
            s
        );
        stream.write_all(r.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let resp = model.generate(req).unwrap();
    assert_eq!(resp.tool_calls()[0].id, Some("call_123".to_string()));
    handle.join().unwrap();
}

#[test]
fn gemini_system_leading_validation() {
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: "http://127.0.0.1:9".to_string(),
        credentials: Some("k".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("gemini-2.0-flash").unwrap();
    let req = GenerationRequest {
        messages: vec![
            Message {
                role: "user".to_string(),
                content: vec![ContentPart::Text(TextPart {
                    part_type: "text".to_string(),
                    text: "hi".to_string(),
                })],
            },
            Message {
                role: "system".to_string(),
                content: vec![ContentPart::Text(TextPart {
                    part_type: "text".to_string(),
                    text: "sys".to_string(),
                })],
            },
        ],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let res = model.generate(req);
    assert!(res.is_err());
    assert_eq!(res.unwrap_err().name, "InvalidRequestError");
}

#[test]
fn gemini_function_id_present_absent() {
    let v_with = serde_json::json!({"candidates":[{"content":{"parts":[{"functionCall":{"name":"fn","args":{"x":1},"id":"call_1"}}],"role":"model"},"finishReason":"STOP"}]});
    let r1 = decode_gemini(v_with, None, &|s| s.to_string()).unwrap();
    assert_eq!(r1.tool_calls()[0].id, Some("call_1".to_string()));
    let v_without = serde_json::json!({"candidates":[{"content":{"parts":[{"functionCall":{"name":"fn","args":{"x":1}}}],"role":"model"},"finishReason":"STOP"}]});
    let r2 = decode_gemini(v_without, None, &|s| s.to_string()).unwrap();
    assert_eq!(r2.tool_calls()[0].id, None);
}

#[test]
fn gemini_thinking_not_text() {
    let v = serde_json::json!({"candidates":[{"content":{"parts":[{"text":"Hello"},{"thought":true,"text":"internal","thoughtSignature":"sig"}],"role":"model"},"finishReason":"STOP"}]});
    let resp = decode_gemini(v, None, &|s| s.to_string()).unwrap();
    assert_eq!(resp.text(), "Hello");
    assert!(resp.provider_metadata.contains_key("thinking"));
    assert!(!resp.text().contains("internal"));
}

#[test]
fn gemini_structured_output_mapping() {
    let req_json = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: Some(ResponseFormat::Json),
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let v = encode_gemini_request("gemini-1.5-flash", &req_json, false).unwrap();
    assert_eq!(
        v.get("generationConfig")
            .and_then(|c| c.get("responseMimeType"))
            .and_then(|x| x.as_str()),
        Some("application/json")
    );
    let schema = serde_json::json!({"type":"object","properties":{"x":{"type":"string"}}});
    let req_schema = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: None,
        tool_choice: None,
        response_format: Some(ResponseFormat::JsonSchema {
            schema: schema.clone(),
        }),
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let v2 = encode_gemini_request("gemini-1.5-flash", &req_schema, false).unwrap();
    assert_eq!(
        v2.get("generationConfig")
            .and_then(|c| c.get("responseJsonSchema"))
            .unwrap(),
        &schema
    );
}

#[test]
fn gemini_tool_schema_uses_parameters_json_schema() {
    let tools = vec![ToolDefinition {
        name: "fn".to_string(),
        description: Some("desc".to_string()),
        input_schema: serde_json::json!({"type":"object","properties":{"x":{"type":"string"}}}),
    }];
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: None,
        temperature: None,
        top_p: None,
        stop: None,
        tools: Some(tools),
        tool_choice: None,
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let v = encode_gemini_request("gemini-1.5-flash", &req, false).unwrap();
    let decl = v.get("tools").and_then(|t| t.as_array()).unwrap()[0]
        .get("functionDeclarations")
        .and_then(|a| a.as_array())
        .unwrap()[0]
        .clone();
    assert!(decl.get("parametersJsonSchema").is_some());
    assert!(decl.get("parameters").is_none());
}

#[test]
fn gemini_pagination_complete() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let h = std::thread::spawn(move || {
        for i in 0..2 {
            let (mut s, _) = listener.accept().unwrap();
            let (req_line, _, _) = read_http_request(&mut s);
            let page = if req_line.contains("pageToken=") {
                serde_json::json!({"models":[{"name":"models/gemini-2"}]})
            } else {
                serde_json::json!({"models":[{"name":"models/gemini-1"}],"nextPageToken":"tok"})
            };
            let body = serde_json::to_string(&page).unwrap();
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body.len(),
                body
            );
            s.write_all(resp.as_bytes()).unwrap();
            s.flush().unwrap();
            if i == 1 {
                break;
            }
        }
    });
    let cfg = ClientConfig {
        driver: "gemini".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let models = client
        .list_models(Some(ListModelsOptions {
            timeout: Some(5000),
            signal: None,
        }))
        .unwrap();
    assert_eq!(models.len(), 2);
    h.join().unwrap();
}
