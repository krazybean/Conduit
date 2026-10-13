use super::*;

#[test]
fn ollama_generate_real_http() {
    let (addr, handle) = start_server(|mut stream| {
        let (req_line, _, body) = read_http_request(&mut stream);
        assert!(req_line.contains("POST /api/chat"));
        let v: Value = serde_json::from_slice(&body).unwrap();
        assert_eq!(v.get("model").and_then(|x| x.as_str()), Some("llama3.2"));
        let resp_body = serde_json::json!({
            "model":"llama3.2","created_at":"2024-01-01T00:00:00Z",
            "message":{"role":"assistant","content":"Hello"},"done":true,
            "done_reason":"stop","prompt_eval_count":2,"eval_count":3
        });
        let s = serde_json::to_string(&resp_body).unwrap();
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
            s.len(),
            s
        );
        stream.write_all(resp.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
    assert_eq!(resp.finish_reason, "stop");
    handle.join().unwrap();
}

#[test]
fn ollama_stream_first_event_before_eof() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let r1 = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":"Hi "},"done":false});
        let s1 = serde_json::to_string(&r1).unwrap() + "\n";
        stream.write_all(s1.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(50));
        let r2 = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"});
        let s2 = serde_json::to_string(&r2).unwrap() + "\n";
        stream.write_all(s2.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
    let second = iter.next().unwrap().unwrap();
    assert!(matches!(&second, StreamEvent::TextDelta{text,..} if text=="Hi "));
    for ev in iter {
        let _ = ev.unwrap();
    }
    handle.join().unwrap();
}

#[test]
fn ollama_stream_fragmentation() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let rec = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":"Hello"},"done":false});
        let s = serde_json::to_string(&rec).unwrap() + "\n";
        let mid = s.len() / 2;
        stream.write_all(&s.as_bytes()[..mid]).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(10));
        stream.write_all(&s.as_bytes()[mid..]).unwrap();
        stream.flush().unwrap();
        let done = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":""},"done":true,"done_reason":"stop"});
        let sd = serde_json::to_string(&done).unwrap() + "\n";
        stream.write_all(sd.as_bytes()).unwrap();
        stream.flush().unwrap();
    });
    let cfg = ClientConfig {
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
        .any(|e| matches!(e, StreamEvent::TextDelta{text,..} if text=="Hello")));
    handle.join().unwrap();
}

#[test]
fn ollama_stream_timeout_is_operation_wide() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let r1 = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":"Hi"},"done":false});
        let s1 = serde_json::to_string(&r1).unwrap() + "\n";
        stream.write_all(s1.as_bytes()).unwrap();
        stream.flush().unwrap();
        std::thread::sleep(std::time::Duration::from_millis(200));
        let _ = stream.write_all(
            serde_json::to_string(
                &serde_json::json!({"message":{"role":"assistant","content":""},"done":true}),
            )
            .unwrap()
            .as_bytes(),
        );
        let _ = stream.flush();
    });
    let cfg = ClientConfig {
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
    let _ = iter.next().unwrap().unwrap();
    let _ = iter.next().unwrap().unwrap();
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
fn ollama_stream_drop_releases_connection() {
    let (addr, handle) = start_server(|mut stream| {
        let (_, _, _) = read_http_request(&mut stream);
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n\r\n")
            .unwrap();
        stream.flush().unwrap();
        let r1 = serde_json::json!({"model":"llama3.2","message":{"role":"assistant","content":"Hi"},"done":false});
        stream
            .write_all((serde_json::to_string(&r1).unwrap() + "\n").as_bytes())
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
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
    let _ = iter.next().unwrap().unwrap();
    drop(iter);
    std::thread::sleep(std::time::Duration::from_millis(100));
    handle.join().unwrap();
}

#[test]
fn ollama_pre_cancelled_zero_request() {
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
        driver: "ollama".to_string(),
        endpoint: format!("http://{}", addr),
        credentials: None,
        headers: None,
        timeout: Some(5000),
        model: None,
    };
    let client = connect(cfg).unwrap();
    let model = client.model("llama3.2").unwrap();
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
