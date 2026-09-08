use conduit::{connect, ClientConfig, GenerationRequest, Message, ContentPart, TextPart};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let endpoint = std::env::var("CONDUIT_ENDPOINT").unwrap_or_else(|_| "http://localhost:1234/v1".into());
    let model_id = std::env::var("CONDUIT_MODEL").unwrap_or_else(|_| "my-model".into());
    let client = connect(ClientConfig { driver: "openai-compatible".into(), endpoint, credentials: std::env::var("CONDUIT_API_KEY").ok(), ..Default::default() })?;
    println!("{:?}", client.list_models(None)?);
    let model = client.model(&model_id)?;
    let req = GenerationRequest { messages: vec![Message { role: "user".into(), content: vec![ContentPart::Text(TextPart { part_type: "text".into(), text: "Hello".into() })] }], ..Default::default() };
    let res = model.generate(req.clone())?;
    println!("{} {:?}", res.text(), res.usage);
    for ev in model.stream(req)? { if let conduit::StreamEvent::TextDelta { text, .. } = ev? { print!("{text}"); } }
    println!();
    Ok(())
}
