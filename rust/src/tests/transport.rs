#[test]
#[ignore]
fn host_incremental_http_streaming() {
    // Would require tiny localhost server that sends first SSE, flush, sleep, then more
    // Verified via Python host suite; Rust uses same ureq incremental read_line logic
}

#[test]
#[ignore]
fn host_early_drop_releases_connection() {
    // Would verify dropping iterator closes HTTP connection promptly
}

#[test]
#[ignore]
fn host_timeout_covers_entire_stream() {
    // Would verify timeout 100ms with chunks at 30ms/60ms still times out at 100ms
}
