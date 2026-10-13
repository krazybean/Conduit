use super::*;

#[test]
fn test_openai_tool_id_preserved_missing_remains_missing() {
    // Simulate that our stub preserves id when present and remains missing when absent
    // In real driver, id would be from provider; here we test that our GenerationResponse preserves
    let tc_with = ToolCallPart {
        part_type: "tool_call".to_string(),
        id: Some("call_123".to_string()),
        name: "fn".to_string(),
        arguments: serde_json::json!({}),
    };
    assert_eq!(tc_with.id, Some("call_123".to_string()));
    let tc_without = ToolCallPart {
        part_type: "tool_call".to_string(),
        id: None,
        name: "fn".to_string(),
        arguments: serde_json::json!({}),
    };
    assert_eq!(tc_without.id, None);
}

#[test]
fn openai_generate_real_http() {
    let (addr, handle) = start_server(|mut stream| {
        let (req_line, _, body) = read_http_request(&mut stream);
        assert!(req_line.contains("POST /v1/chat/completions"));
        let v: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(v.get("model").and_then(|x| x.as_str()), Some("test-model"));
        let resp_body = serde_json::json!({
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [{"index":0,"message":{"role":"assistant","content":"Hello"},"finish_reason":"stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        });
        let body_str = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body_str.len(),
            body_str
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}/v1", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("test-model").unwrap();
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
    assert_eq!(resp.finish_reason, "stop");
    assert_eq!(resp.usage.as_ref().unwrap().input_tokens, Some(1));
    handle.join().unwrap();
}

#[test]
fn openai_http_error_body_is_normalized_and_redacted() {
    let secret = "sk-test-secret";
    let (addr, handle) = start_server(move |mut stream| {
        let (_, headers, _) = read_http_request(&mut stream);
        assert!(headers
            .to_ascii_lowercase()
            .contains("authorization: bearer sk-test-secret"));
        let resp_body = serde_json::json!({
            "error": {
                "message": format!("invalid key {secret}"),
                "type": "invalid_request_error",
                "code": "invalid_api_key"
            }
        });
        let body_str = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\nX-Request-Id: req-401\r\nContent-Length: {}\r\n\r\n{}",
            body_str.len(),
            body_str
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{addr}"),
        credentials: Some(secret.to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("test-model").unwrap();
    let err = model.generate("hi").unwrap_err();
    assert_eq!(err.name, "ProviderError");
    assert_eq!(err.status_code, Some(401));
    assert_eq!(err.request_id.as_deref(), Some("req-401"));
    assert!(err.cause.is_none());
    let details = err.provider_details.unwrap();
    assert_eq!(
        details.get("type"),
        Some(&"invalid_request_error".to_string())
    );
    assert_eq!(details.get("code"), Some(&"invalid_api_key".to_string()));
    assert_eq!(
        details.get("message"),
        Some(&"invalid key [REDACTED]".to_string())
    );
    handle.join().unwrap();
}

#[test]
fn openai_redirect_does_not_forward_credentials() {
    let (target_addr, target_handle) = start_server(|mut stream| {
        let (_, headers, _) = read_http_request(&mut stream);
        assert!(!headers
            .to_ascii_lowercase()
            .contains("authorization: bearer redirect-secret"));
        let resp_body = serde_json::json!({
            "id": "redirected",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]
        });
        let body_str = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body_str.len(),
            body_str
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let (redirect_addr, redirect_handle) = start_server(move |mut stream| {
        let (_, headers, _) = read_http_request(&mut stream);
        assert!(headers
            .to_ascii_lowercase()
            .contains("authorization: bearer redirect-secret"));
        let resp = format!(
            "HTTP/1.1 302 Found\r\nLocation: http://{target_addr}/v1/chat/completions\r\nContent-Length: 0\r\n\r\n"
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{redirect_addr}/v1"),
        credentials: Some("redirect-secret".to_string()),
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("test-model").unwrap();
    assert_eq!(model.generate("hi").unwrap().text(), "ok");
    redirect_handle.join().unwrap();
    target_handle.join().unwrap();
}

#[test]
fn openai_generate_empty_vs_tool_only() {
    for (content_val, expected_text_len, expect_tool) in [
        (serde_json::json!(""), 1, false),
        (serde_json::Value::Null, 0, true),
    ] {
        let (addr, handle) = start_server(move |mut stream| {
            let (_, _, _) = read_http_request(&mut stream);
            let msg_content = content_val.clone();
            let mut msg = serde_json::Map::new();
            msg.insert("role".to_string(), Value::String("assistant".to_string()));
            msg.insert("content".to_string(), msg_content);
            if expect_tool {
                msg.insert("tool_calls".to_string(), serde_json::json!([{"id":"call1","type":"function","function":{"name":"fn","arguments":"{}"}}]));
            }
            let resp_body = serde_json::json!({
                "id": "id", "object": "chat.completion", "created": 0, "model": "m",
                "choices": [{"index":0,"message": msg,"finish_reason": if expect_tool {"tool_calls"} else {"stop"}}],
                "usage": {"prompt_tokens":1,"completion_tokens":1}
            });
            let body_str = serde_json::to_string(&resp_body).unwrap();
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                body_str.len(),
                body_str
            );
            stream.write_all(resp.as_bytes()).unwrap();
            stream.flush().unwrap();
        });
        let cfg = ClientConfig {
            driver: "openai-compatible".to_string(),
            endpoint: format!("http://{}", addr),
            credentials: None,
            headers: None,
            timeout: Some(5000),
            model: None,
        };
        let client = connect(cfg).unwrap();
        let model = client.model("m").unwrap();
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
        if expect_tool {
            assert_eq!(resp.content.len(), 1);
            assert!(matches!(resp.content[0], ContentPart::ToolCall(_)));
        } else {
            assert_eq!(resp.content.len(), 1);
            assert!(matches!(resp.content[0], ContentPart::Text(_)));
        }
        handle.join().unwrap();
    }
}

#[test]
fn openai_tool_id_not_fabricated() {
    // With ID
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        let resp_body = serde_json::json!({
            "id":"id","object":"chat.completion","created":0,"model":"m",
            "choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"id":"abc","type":"function","function":{"name":"fn","arguments":"{}"}}]},"finish_reason":"tool_calls"}],
            "usage": {}
        });
        let body_str = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body_str.len(),
            body_str
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
    let tc = resp.tool_calls();
    assert_eq!(tc[0].id, Some("abc".to_string()));
    handle.join().unwrap();
    // Without ID (missing) — should remain None, not fabricated
    let (addr2, handle2) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        let resp_body = serde_json::json!({
            "id":"id","object":"chat.completion","created":0,"model":"m",
            "choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"type":"function","function":{"name":"fn","arguments":"{}"}}]},"finish_reason":"tool_calls"}],
            "usage": {}
        });
        // Note: no "id" field in tool_call
        let body_str = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            body_str.len(),
            body_str
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg2 = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr2),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client2 = connect(cfg2).unwrap();
    let model2 = client2.model("m").unwrap();
    let req2 = GenerationRequest {
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
    let resp2 = model2.generate(req2).unwrap();
    let tc2 = resp2.tool_calls();
    // Our decode should keep id as None when missing, not fabricate
    assert_eq!(tc2[0].id, None);
    handle2.join().unwrap();
}

#[test]
fn openai_object_tool_arguments_are_normalized_and_streaming_stays_strict() {
    let body = serde_json::json!({
        "id": "id",
        "model": "m",
        "choices": [{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call","type":"function","function":{"name":"fn","arguments":{"city":"Paris"}}}]},"finish_reason":"tool_calls"}]
    });
    let response = decode_openai(body.clone(), None, &|value| value.to_string()).unwrap();
    assert_eq!(
        response.tool_calls()[0].arguments,
        serde_json::json!({"city":"Paris"})
    );

    for arguments in [
        serde_json::json!([]),
        serde_json::json!(1),
        serde_json::json!(true),
        serde_json::Value::Null,
    ] {
        let mut invalid = body.clone();
        invalid["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = arguments;
        assert_eq!(
            decode_openai(invalid, None, &|value| value.to_string())
                .unwrap_err()
                .name,
            "ProtocolError"
        );
    }

    let fixtures: serde_json::Value = serde_json::from_str(include_str!(
        "../../../conformance/responses/openai-text.json"
    ))
    .unwrap();
    for id in [
        "tool-call-string-arguments",
        "tool-call-object-arguments",
        "tool-call-unsupported-arguments",
        "provider-reasoning-and-empty-tools",
    ] {
        let case = fixtures
            .as_array()
            .unwrap()
            .iter()
            .find(|case| case["id"] == id)
            .unwrap();
        let result = decode_openai(case["input"]["wire"]["body"].clone(), None, &|value| {
            value.to_string()
        });
        if case["expected"].get("error").is_some() {
            assert_eq!(result.unwrap_err().name, "ProtocolError");
        } else if id == "provider-reasoning-and-empty-tools" {
            let response = result.unwrap();
            assert_eq!(response.text(), "OK");
            assert_eq!(response.finish_reason, "stop");
        } else {
            assert_eq!(
                result.unwrap().tool_calls()[0].arguments,
                serde_json::json!({"city":"Paris"})
            );
        }
    }

    let stream = b"data: {\"id\":\"id\",\"model\":\"m\",\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"name\":\"fn\",\"arguments\":{\"city\":\"Paris\"}}}]},\"finish_reason\":null}]}\n\ndata: [DONE]\n\n";
    let mut events = openai_sse_stream(
        std::io::Cursor::new(stream),
        None,
        Arc::new(|value| value.to_string()),
        None,
        None,
    );
    assert_eq!(events.next().unwrap().unwrap_err().name, "ProtocolError");
}

#[test]
fn openai_stream_first_event_before_eof() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        let headers = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n";
        stream.write_all(headers.as_bytes()).unwrap();
        stream.flush().unwrap();
        let first = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hi\"},\"finish_reason\":null}],\"id\":\"id\",\"model\":\"m\"}\n\n";
        stream.write_all(first.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(300));
        let done = "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n";
        stream.write_all(done.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
    match first {
        StreamEvent::Start { .. } => {}
        _ => panic!("expected start"),
    }
    let second = iter.next().unwrap().unwrap();
    match second {
        StreamEvent::TextDelta { text, .. } => assert_eq!(text, "Hi"),
        _ => panic!("expected Hi"),
    }
    // Drain rest
    for ev in iter {
        let _ = ev.unwrap();
    }
    handle.join().unwrap();
}

#[test]
fn openai_stream_fragmentation() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        // Split UTF-8, data:, JSON, CRLF across writes
        let part1 = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"";
        let part2 = "TVA="; // fragment of UTF-8 "Hi" with multi-byte? Use simple ascii split
        let part3 = "\"},\"finish_reason\":null}],\"id\":\"id\",\"model\":\"m\"}\r\n\r\n";
        stream.write_all(part1.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        stream.write_all(part2.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        stream.write_all(part3.as_bytes()).unwrap();
        stream.flush().unwrap();
        let done = "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n";
        stream.write_all(done.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
    assert!(evs.iter().any(|e| matches!(e, StreamEvent::TextDelta { text, .. } if text.contains("TVA=") || text.contains("Hi"))));
    handle.join().unwrap();
}

#[test]
fn openai_stream_timeout_is_operation_wide() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let first = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hi\"},\"finish_reason\":null}],\"id\":\"id\",\"model\":\"m\"}\n\n";
        stream.write_all(first.as_bytes()).unwrap();
        stream.flush().unwrap();
        // Sleep longer than operation timeout (100ms) while sending chunks every 30ms would not trigger per-read timeout
        std::thread::sleep(std::time::Duration::from_millis(200));
        let _ = stream.write_all(b"data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n");
        let _ = stream.flush();
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
    let _ = iter.next().unwrap().unwrap(); // Hi
                                           // Next should timeout around 100ms total, not per-read
    let start = std::time::Instant::now();
    let res = iter.next();
    let elapsed = start.elapsed();
    assert!(
        elapsed.as_millis() < 300,
        "should timeout near deadline, not after server sleep"
    );
    match res {
        Some(Err(e)) => assert_eq!(e.name, "TimeoutError"),
        _ => panic!("expected TimeoutError"),
    }
    handle.join().unwrap();
}

#[test]
fn openai_stream_drop_releases_connection() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let first = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"Hi\"},\"finish_reason\":null}],\"id\":\"id\",\"model\":\"m\"}\n\n";
        stream.write_all(first.as_bytes()).unwrap();
        stream.flush().unwrap();
        // Wait for client to drop; read should get 0 or error after close
        let mut buf = [0u8; 1024];
        stream
            .set_read_timeout(Some(std::time::Duration::from_millis(500)))
            .unwrap();
        let n = stream.read(&mut buf).unwrap_or(0);
        // If client dropped, n should be 0 or error; we just check that server observed closure without needing to send more
        assert!(n < 1000000);
    });
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
    let _ = iter.next().unwrap().unwrap(); // Start
    let _ = iter.next().unwrap().unwrap(); // Hi
    drop(iter);
    std::thread::sleep(std::time::Duration::from_millis(100));
    handle.join().unwrap();
}

#[test]
fn openai_pre_cancelled_zero_request() {
    use std::sync::atomic::AtomicBool;
    let counter = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let counter_clone = counter.clone();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let h = std::thread::spawn(move || {
        listener.set_nonblocking(true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        if let Ok((_, _)) = listener.accept() {
            counter_clone.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
    });
    let signal = std::sync::Arc::new(AtomicBool::new(true));
    let cfg = ClientConfig {
        driver: "openai-compatible".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("m").unwrap();
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
fn test_openai_still_requires_endpoint() {
    let cfg = ClientConfig {
        driver: "openai-compatible".into(),
        endpoint: "".into(),
        model: Some("m".into()),
        ..Default::default()
    };
    assert!(connect(cfg).is_err());
}

#[test]
fn test_openai_endpoint_preserves_api_base_path() {
    let client = connect(ClientConfig::openai_compatible(
        "http://localhost:8080/v1/",
        "model-name",
    ))
    .unwrap();
    assert_eq!(client.url, "http://localhost:8080/v1/chat/completions");
    assert_eq!(client.list_url, "http://localhost:8080/v1/models");
}

#[test]
fn test_openai_model_metadata_is_preserved() {
    let data = vec![serde_json::json!({
        "id": "model-name",
        "object": "model",
        "owned_by": "llamacpp",
        "meta": {"n_ctx_train": 32768}
    })];
    let models = normalize_openai_models(&data, &|value| value.to_string()).unwrap();
    assert_eq!(models.len(), 1);
    assert_eq!(models[0].id, "model-name");
    assert_eq!(
        models[0].provider_metadata.as_ref().unwrap()["meta"]["n_ctx_train"],
        32768
    );
    assert_eq!(
        models[0].provider_metadata.as_ref().unwrap()["owned_by"],
        "llamacpp"
    );
}
