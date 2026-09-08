use conduit::{connect, ClientConfig, GenerationRequest, Message, ContentPart, TextPart};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let endpoint = std::env::var("CONDUIT_ENDPOINT").unwrap_or_else(|_| "http://localhost:11434".into());
    let model_id = std::env::var("CONDUIT_MODEL").unwrap_or_else(|_| "llama3".into());
    let client = connect(ClientConfig { driver: "ollama".into(), endpoint, ..Default::default() })?;
    println!("{:?}", client.list_models(None)?);
    let model = client.model(&model_id)?;
    let req = GenerationRequest { messages: vec![Message { role: "user".into(), content: vec![ContentPart::Text(TextPart { part_type: "text".into(), text: "Hello".into() })] }], ..Default::default() };
    let res = model.generate(req.clone())?;
    println!("{}", res.text());
    for ev in model.stream(req)? { if let conduit::StreamEvent::TextDelta { text, .. } = ev? { print!("{text}"); } }
    println!();
    Ok(())
}
