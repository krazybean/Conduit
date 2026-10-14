use std::collections::HashMap;
use std::sync::{atomic::AtomicBool, Arc};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub enum Driver {
    OpenAICompatible,
    Ollama,
    Anthropic,
    Gemini,
}

impl Driver {
    pub(crate) fn from_str(s: &str) -> Option<Self> {
        match s {
            "openai-compatible" => Some(Self::OpenAICompatible),
            "ollama" => Some(Self::Ollama),
            "anthropic" => Some(Self::Anthropic),
            "gemini" => Some(Self::Gemini),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ClientConfig {
    pub driver: String,
    pub provider: Option<String>,
    pub endpoint: String,
    pub credentials: Option<String>,
    pub headers: Option<HashMap<String, String>>,
    pub timeout: Option<u32>,
    pub model: Option<String>,
}
#[allow(clippy::derivable_impls)]
impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            driver: String::new(),
            provider: None,
            endpoint: String::new(),
            credentials: None,
            headers: None,
            timeout: None,
            model: None,
        }
    }
}
impl ClientConfig {
    pub fn ollama(model: impl Into<String>) -> Self {
        Self {
            driver: "ollama".into(),
            endpoint: "http://localhost:11434".into(),
            model: Some(model.into()),
            ..Default::default()
        }
    }
    pub fn anthropic(model: impl Into<String>) -> Self {
        Self {
            driver: "anthropic".into(),
            endpoint: "https://api.anthropic.com".into(),
            model: Some(model.into()),
            ..Default::default()
        }
    }
    pub fn gemini(model: impl Into<String>) -> Self {
        Self {
            driver: "gemini".into(),
            endpoint: "https://generativelanguage.googleapis.com".into(),
            model: Some(model.into()),
            ..Default::default()
        }
    }
    pub fn openai_compatible(endpoint: impl Into<String>, model: impl Into<String>) -> Self {
        Self {
            driver: "openai-compatible".into(),
            endpoint: endpoint.into(),
            model: Some(model.into()),
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TextPart {
    #[serde(rename = "type")]
    pub part_type: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolDefinition {
    pub name: String,
    pub description: Option<String>,
    #[serde(rename = "inputSchema")]
    pub input_schema: Value,
}

#[derive(Debug, Clone)]
pub enum ToolChoice {
    Auto,
    None_,
    Required,
    Named(String),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCallPart {
    #[serde(rename = "type")]
    pub part_type: String,
    pub id: Option<String>,
    pub name: String,
    pub arguments: Value,
}

#[derive(Debug, Clone)]
pub struct ToolResultPart {
    pub call_id: Option<String>,
    pub name: Option<String>,
    pub content: String,
}

#[derive(Debug, Clone)]
pub enum ContentPart {
    Text(TextPart),
    ToolCall(ToolCallPart),
    ToolResult(ToolResultPart),
}

#[derive(Debug, Clone)]
pub struct Message {
    pub role: String,
    pub content: Vec<ContentPart>,
}
impl Message {
    pub fn user(text: impl Into<String>) -> Self {
        Self {
            role: "user".into(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".into(),
                text: text.into(),
            })],
        }
    }
    pub fn system(text: impl Into<String>) -> Self {
        Self {
            role: "system".into(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".into(),
                text: text.into(),
            })],
        }
    }
    pub fn assistant(text: impl Into<String>) -> Self {
        Self {
            role: "assistant".into(),
            content: vec![ContentPart::Text(TextPart {
                part_type: "text".into(),
                text: text.into(),
            })],
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct GenerationRequest {
    pub messages: Vec<Message>,
    pub max_output_tokens: Option<u32>,
    pub temperature: Option<f64>,
    pub top_p: Option<f64>,
    pub stop: Option<Vec<String>>,
    pub tools: Option<Vec<ToolDefinition>>,
    pub tool_choice: Option<ToolChoice>,
    pub response_format: Option<ResponseFormat>,
    pub provider_options: Option<HashMap<String, Value>>,
    pub timeout: Option<u32>,
    pub signal: Option<Arc<AtomicBool>>,
}
impl From<&str> for GenerationRequest {
    fn from(s: &str) -> Self {
        Self {
            messages: vec![Message::user(s)],
            ..Default::default()
        }
    }
}
impl From<String> for GenerationRequest {
    fn from(s: String) -> Self {
        Self {
            messages: vec![Message::user(s)],
            ..Default::default()
        }
    }
}

#[derive(Debug, Clone)]
pub enum ResponseFormat {
    Text,
    Json,
    JsonSchema { schema: Value },
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Usage {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total_tokens: Option<u32>,
}

#[derive(Debug, Clone)]
pub struct GenerationResponse {
    pub id: Option<String>,
    pub model: Option<String>,
    pub content: Vec<ContentPart>,
    pub finish_reason: String,
    pub usage: Option<Usage>,
    pub provider_metadata: HashMap<String, Value>,
}
impl GenerationResponse {
    pub fn text(&self) -> String {
        self.content
            .iter()
            .filter_map(|p| match p {
                ContentPart::Text(t) => Some(t.text.clone()),
                _ => None,
            })
            .collect()
    }
    pub fn tool_calls(&self) -> Vec<&ToolCallPart> {
        self.content
            .iter()
            .filter_map(|p| match p {
                ContentPart::ToolCall(t) => Some(t),
                _ => None,
            })
            .collect()
    }
}

#[derive(Debug, Clone)]
pub struct ModelInfo {
    pub id: String,
    pub name: Option<String>,
    pub provider_metadata: Option<HashMap<String, Value>>,
}

#[derive(Debug, Clone)]
pub struct ListModelsOptions {
    pub timeout: Option<u32>,
    pub signal: Option<Arc<AtomicBool>>,
}

#[derive(Debug, Clone)]
pub enum StreamEvent {
    Start {
        id: Option<String>,
        model: Option<String>,
    },
    TextDelta {
        index: u32,
        text: String,
    },
    ToolCallDelta {
        index: u32,
        id: Option<String>,
        name: Option<String>,
        arguments_delta: Option<String>,
    },
    Usage {
        usage: Usage,
    },
    Done {
        response: GenerationResponse,
    },
}
