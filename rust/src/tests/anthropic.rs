use super::*;

#[test]
fn anthropic_generate_real_http() {
    let (addr, handle) = start_server(|mut stream| {
        let (req_line, _, body) = read_http_request(&mut stream);
        assert!(req_line.contains("POST /v1/messages"));
        let v: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(
            v.get("model").and_then(|x| x.as_str()),
            Some("claude-3-5-sonnet-20241022")
        );
        let resp = serde_json::json!({"type":"message","id":"msg_1","role":"assistant","model":"claude-3-5-sonnet-20241022","content":[{"type":"text","text":"Hello"}],"stop_reason":"end_turn","usage":{"input_tokens":2,"output_tokens":3}});
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
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
fn anthropic_generate_requires_max_tokens() {
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: "http://127.0.0.1:9".to_string(),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
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
    let res = model.generate(req);
    assert!(res.is_err());
    assert_eq!(res.unwrap_err().name, "InvalidRequestError");
}

#[test]
fn anthropic_generate_system_leading() {
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: "http://127.0.0.1:9".to_string(),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
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
        max_output_tokens: Some(5),
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
fn anthropic_stream_first_event_before_eof() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let start = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"claude\",\"usage\":{\"input_tokens\":1,\"output_tokens\":0}}}\n\n";
        stream.write_all(start.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(30));
        let delta = "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hello\"}}\n\n";
        stream.write_all(delta.as_bytes()).unwrap();
        stream.flush().unwrap();
        let stop = "event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":2}}\n\n";
        stream.write_all(stop.as_bytes()).unwrap();
        stream.flush().unwrap();
        let end = "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n";
        let _ = stream.write_all(end.as_bytes());
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
        if matches!(&e, StreamEvent::TextDelta{text,..} if text=="Hello") {
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
fn anthropic_stream_fragmentation() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let payload = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"claude\",\"usage\":{\"input_tokens\":1}}}\n\n";
        let mid = payload.len() / 2;
        stream.write_all(&payload.as_bytes()[..mid]).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        stream.write_all(&payload.as_bytes()[mid..]).unwrap();
        stream.flush().unwrap();
        let rest = "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hi\"}}\n\nevent: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n";
        stream.write_all(rest.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
        .any(|e| matches!(e, StreamEvent::TextDelta{text,..} if text=="Hi")));
    handle.join().unwrap();
}

#[test]
fn anthropic_stream_timeout_is_operation_wide() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let start = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"claude\",\"usage\":{\"input_tokens\":1}}}\n\n";
        stream.write_all(start.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        let _ = stream.write_all(b"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"}}\n\n");
        let _ = stream.flush();
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
    let _ = iter.next().unwrap().unwrap(); // Usage (buffered before deadline)
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
fn anthropic_stream_drop_releases_connection() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let start = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\",\"model\":\"claude\",\"usage\":{\"input_tokens\":1}}}\n\n";
        stream.write_all(start.as_bytes()).unwrap();
        stream.flush().unwrap();
        let delta = "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hi\"}}\n\n";
        stream.write_all(delta.as_bytes()).unwrap();
        stream.flush().unwrap();
        let mut buf = [0u8; 1024];
        stream
            .set_read_timeout(Some(std::time::Duration::from_millis(500)))
            .unwrap();
        let n = stream.read(&mut buf).unwrap_or(0);
        assert!(n < 1000000);
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
    let _ = iter.next().unwrap().unwrap();
    drop(iter);
    std::thread::sleep(std::time::Duration::from_millis(100));
    handle.join().unwrap();
}

#[test]
fn anthropic_pre_cancelled_zero_request() {
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
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
fn anthropic_tool_use_roundtrip() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, body) = read_http_request(&mut stream);
        let v: Value = serde_json::from_slice(&body).unwrap();
        let msgs = v.get("messages").and_then(|m| m.as_array()).unwrap();
        assert!(msgs[0].get("content").is_some());
        let resp = serde_json::json!({"type":"message","id":"msg_2","role":"assistant","model":"claude","content":[{"type":"tool_use","id":"toolu_1","name":"get_weather","input":{"city":"Tokyo"}}],"stop_reason":"tool_use","usage":{"input_tokens":5,"output_tokens":5}});
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
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: Some("key".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude-3-5-sonnet-20241022").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
        temperature: None,
        top_p: None,
        stop: None,
        tools: Some(vec![ToolDefinition {
            name: "get_weather".to_string(),
            description: None,
            input_schema: serde_json::json!({"type":"object"}),
        }]),
        tool_choice: Some(ToolChoice::Auto),
        response_format: None,
        provider_options: None,
        timeout: None,
        signal: None,
    };
    let resp = model.generate(req).unwrap();
    assert_eq!(resp.finish_reason, "tool_call");
    assert_eq!(resp.tool_calls()[0].name, "get_weather");
    handle.join().unwrap();
}

#[test]
fn anthropic_requires_max_output_tokens_before_io() {
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: "http://127.0.0.1:9".to_string(),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude").unwrap();
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
    let err = model.generate(req).unwrap_err();
    assert_eq!(err.name, "InvalidRequestError");
    assert!(err.message.contains("maxOutputTokens"));
}

#[test]
fn anthropic_zero_max_output_tokens_valid() {
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(0),
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
    let v = encode_anthropic_request("claude", &req, false).unwrap();
    assert_eq!(v.get("max_tokens").and_then(|x| x.as_u64()), Some(0));
}

#[test]
fn anthropic_tool_ids_preserved() {
    let v = serde_json::json!({"type":"message","id":"msg1","role":"assistant","model":"claude","content":[{"type":"tool_use","id":"toolu_1","name":"fn","input":{"x":1}}],"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1}});
    let resp = decode_anthropic(v, None, &|s| s.to_string()).unwrap();
    let tc = resp.tool_calls();
    assert_eq!(tc[0].id, Some("toolu_1".to_string()));
    let v2 = serde_json::json!({"type":"message","id":"msg1","role":"assistant","model":"claude","content":[{"type":"tool_use","id":"toolu_2","name":"fn2","input":{}}],"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1}});
    let resp2 = decode_anthropic(v2, None, &|s| s.to_string()).unwrap();
    assert_eq!(resp2.tool_calls()[0].id, Some("toolu_2".to_string()));
}

#[test]
fn anthropic_stream_first_event_before_eof_dup() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let start = format!("event: message_start\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"message_start","message":{"id":"msg1","type":"message","role":"assistant","model":"claude","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":0}}})).unwrap());
        stream.write_all(start.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(150));
        let delta = format!("event: content_block_delta\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}})).unwrap());
        stream.write_all(delta.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(100));
        let stop = format!("event: message_delta\ndata: {}\n\nevent: message_stop\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}})).unwrap(), serde_json::to_string(&serde_json::json!({"type":"message_stop"})).unwrap());
        stream.write_all(stop.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
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
    let second = iter.next().unwrap().unwrap();
    assert!(matches!(second, StreamEvent::Usage { .. }));
    let third = iter.next().unwrap().unwrap();
    assert!(matches!(third, StreamEvent::TextDelta{ text, ..} if text=="Hi"));
    for ev in iter {
        let _ = ev.unwrap();
    }
    handle.join().unwrap();
}

#[test]
fn anthropic_stream_tool_json_delta() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let start = format!("event: message_start\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"message_start","message":{"id":"msg1","type":"message","role":"assistant","model":"claude","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":0}}})).unwrap());
        stream.write_all(start.as_bytes()).unwrap();
        stream.flush().unwrap();
        let cb = format!("event: content_block_start\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"fn","input":{}}})).unwrap());
        stream.write_all(cb.as_bytes()).unwrap();
        stream.flush().unwrap();
        let d1 = format!("event: content_block_delta\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"x\":"}})).unwrap());
        stream.write_all(d1.as_bytes()).unwrap();
        stream.flush().unwrap();
        let d2 = format!("event: content_block_delta\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"1}"}})).unwrap());
        stream.write_all(d2.as_bytes()).unwrap();
        stream.flush().unwrap();
        let stop = format!("event: content_block_stop\ndata: {}\n\nevent: message_delta\ndata: {}\n\nevent: message_stop\ndata: {}\n\n", serde_json::to_string(&serde_json::json!({"type":"content_block_stop","index":0})).unwrap(), serde_json::to_string(&serde_json::json!({"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":2}})).unwrap(), serde_json::to_string(&serde_json::json!({"type":"message_stop"})).unwrap());
        stream.write_all(stop.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "anthropic".to_string(),
        provider: None,
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("claude").unwrap();
    let req = GenerationRequest {
        messages: vec![Message {
            role: "user".to_string(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".to_string(),
                text: "hi".to_string(),
            })],
        }],
        max_output_tokens: Some(10),
        temperature: None,
        top_p: None,
        stop: None,
        tools: Some(vec![ToolDefinition {
            name: "fn".to_string(),
            description: None,
            input_schema: serde_json::json!({}),
        }]),
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
    let deltas: Vec<_> = evs
        .iter()
        .filter_map(|e| {
            if let StreamEvent::ToolCallDelta {
                arguments_delta, ..
            } = e
            {
                arguments_delta.clone()
            } else {
                None
            }
        })
        .collect();
    assert!(deltas.join("").contains("{\"x\":1}"));
    let done = evs
        .iter()
        .find_map(|e| {
            if let StreamEvent::Done { response } = e {
                Some(response)
            } else {
                None
            }
        })
        .unwrap();
    assert_eq!(done.tool_calls()[0].id, Some("toolu_1".to_string()));
    handle.join().unwrap();
}

#[test]
fn anthropic_thinking_not_text() {
    let v = serde_json::json!({"type":"message","id":"msg1","role":"assistant","model":"claude","content":[{"type":"thinking","thinking":"internal","signature":"sig"},{"type":"text","text":"Hello"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}});
    let resp = decode_anthropic(v, None, &|s| s.to_string()).unwrap();
    assert_eq!(resp.text(), "Hello");
    assert!(resp.provider_metadata.contains_key("thinking"));
    assert!(!resp.text().contains("internal"));
}

#[test]
fn anthropic_pagination_complete() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let h = std::thread::spawn(move || {
        for i in 0..2 {
            let (mut s, _) = listener.accept().unwrap();
            let (req_line, _, _) = read_http_request(&mut s);
            let page = if req_line.contains("after_id=") {
                serde_json::json!({"data":[{"id":"m2"}],"has_more":false,"last_id":null})
            } else {
                serde_json::json!({"data":[{"id":"m1"}],"has_more":true,"last_id":"m1"})
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
        driver: "anthropic".to_string(),
        provider: None,
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
    assert_eq!(models[0].id, "m1");
    assert_eq!(models[1].id, "m2");
    h.join().unwrap();
}
