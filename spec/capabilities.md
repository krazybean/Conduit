# Capabilities

`CapabilityStatus` is `supported`, `unsupported`, or `unknown`.
`CapabilitySet` maps these names to statuses: `streaming`, `tools`,
`structured_output`, `vision`, `reasoning_controls`, `model_listing`.
Missing evidence means `unknown`. No embeddings capability until an embedding
operation exists. Reasoning controls refer to explicit native options, not a
new operation. Scaffold documents are not claims of implemented support.

Keep protocol/driver evidence distinct from model evidence. Sources may be known
driver behavior, remotely returned model metadata, or explicit caller model
configuration. Protocol representation alone is not proof of model support.
An implementation that cannot expose an operation reports it unsupported.

For model-dependent features, derive effective support conservatively:

| Protocol/implementation | Model | Effective |
| --- | --- | --- |
| unsupported | any | unsupported |
| any | unsupported | unsupported |
| supported | supported | supported |
| otherwise | otherwise | unknown |

Caller evidence may fill unknown model facts, not override a known driver
limitation; contradictory evidence resolves conservatively to unsupported.
`model_listing` is client-level and uses driver/provider evidence directly.
No requirement for model evidence on a model-independent operation.

`capabilities()` is local and performs no hidden I/O. Explicit model listing may
return evidence; no speculative separate discovery API is needed. Do not advertise
known model support by recognizing a name substring. Unknown is not a reason to
silently omit a feature: attempt it if representable and normalize a provider
rejection. Known unsupported requests fail explicitly before I/O where possible.
