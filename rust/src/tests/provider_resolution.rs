use super::*;
use serde_json::Value;
use std::collections::HashMap;

fn cases() -> Vec<Value> {
    serde_json::from_str(include_str!(
        "../../../conformance/provider-resolution.json"
    ))
    .unwrap()
}

fn config(case: &Value) -> ClientConfig {
    let input = &case["config"];
    let mut endpoint = input["endpoint"].as_str().unwrap_or("").to_string();
    endpoint = endpoint.replace("http://127.0.0.1:0", "http://127.0.0.1:43127");
    let headers = input["headers"].as_object().map(|values| {
        values
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().unwrap().to_string()))
            .collect::<HashMap<_, _>>()
    });
    ClientConfig {
        driver: input["driver"].as_str().unwrap_or("").to_string(),
        provider: input["provider"].as_str().map(str::to_string),
        endpoint,
        credentials: input["credentials"].as_str().map(str::to_string),
        headers,
        ..Default::default()
    }
}

#[test]
fn shared_provider_resolution_cases() {
    for case in cases() {
        let result = connect(config(&case));
        match case["kind"].as_str().unwrap() {
            "error" => match result {
                Err(error) => assert_eq!(error.name, case["expected"]["error"]),
                Ok(_) => panic!("{} unexpectedly succeeded", case["id"]),
            },
            "resolve" => {
                let client = result.unwrap();
                let driver = match client.driver {
                    Driver::OpenAICompatible => "openai-compatible",
                    Driver::Ollama => "ollama",
                    Driver::Anthropic => "anthropic",
                    Driver::Gemini => "gemini",
                };
                assert_eq!(driver, case["expected"]["driver"]);
                assert_eq!(client.endpoint, case["expected"]["endpoint"]);
            }
            "request" => {
                let client = result.unwrap();
                let expected = &case["expected"];
                assert_eq!(client.endpoint, config(&case).endpoint);
                assert!(client
                    .list_url
                    .ends_with(expected["path"].as_str().unwrap()));
                for (key, value) in expected["headers"].as_object().unwrap() {
                    assert_eq!(client.headers.get(key).map(String::as_str), value.as_str());
                }
            }
            kind => panic!("unknown fixture kind {kind}"),
        }
    }
}
