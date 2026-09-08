use conduit::ollama;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let model = ollama("qwen3:8b")?;
    println!("{}", model.generate("Hello")?.text());
    Ok(())
}
