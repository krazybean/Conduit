use super::*;
use std::io::{Read, Write};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

pub(super) fn start_server<F>(handler: F) -> (std::net::SocketAddr, std::thread::JoinHandle<()>)
where
    F: FnOnce(std::net::TcpStream) + Send + 'static,
{
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let h = std::thread::spawn(move || {
        let (stream, _) = listener.accept().unwrap();
        handler(stream);
    });
    (addr, h)
}

pub(super) fn read_http_request(stream: &mut std::net::TcpStream) -> (String, String, Vec<u8>) {
    let mut buf = Vec::new();
    let mut tmp = [0u8; 4096];
    // Read until \r\n\r\n
    loop {
        let n = stream.read(&mut tmp).unwrap();
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            break;
        }
    }
    let header_end = buf
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .unwrap_or(buf.len());
    let header_str = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut headers = std::collections::HashMap::new();
    let mut lines = header_str.split("\r\n");
    let request_line = lines.next().unwrap_or("").to_string();
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.trim().to_lowercase(), v.trim().to_string());
        }
    }
    let content_len = headers
        .get("content-length")
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(0);
    let mut body = Vec::new();
    let body_start = header_end + 4;
    if buf.len() > body_start {
        body.extend_from_slice(&buf[body_start..]);
    }
    while body.len() < content_len {
        let n = stream.read(&mut tmp).unwrap();
        if n == 0 {
            break;
        }
        body.extend_from_slice(&tmp[..n]);
    }
    body.truncate(content_len);
    (request_line, header_str, body)
}

mod anthropic;
mod client;
mod gemini;
mod ollama;
mod openai;
mod transport;
