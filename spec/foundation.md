# Foundation architecture

Conduit has three deliberately separate layers:

1. **Protocol adapters** translate one wire protocol into the normalized request,
   response, stream, error, and usage contracts.
2. **Provider presets** are a small, versioned convenience map from a provider
   name to an existing protocol adapter and documented default endpoint. They do
   not add provider SDKs, routing, fallback, or hidden network discovery.
3. **Optional metadata** travels with normalized responses. Provider-reported
   usage and provider metadata remain absent when the provider did not report
   them; Conduit must not invent zeroes or costs.

An explicit `driver` and `endpoint` always remain valid. A preset is shorthand,
and an explicit endpoint overrides its default. Credentials are supplied by the
caller for each client; Conduit does not persist, fetch, or manage provider
credentials.

Preset presence is not a compatibility claim. A provider enters the verified
conformance matrix only after a reproducible request/response test passes for a
specific protocol and capability set. Muse currently has a documented
OpenAI-compatible surface, but Conduit has not yet run that test.

## Bundled provider contract

`conformance/providers.json` is the canonical provider manifest. It is bundled
with the TypeScript, Python, and Rust packages and each language's conformance
gate compares its bundled copy to the canonical file. Every supported language
must expose the manifest's `provider_preset` and `endpoint_override`
capabilities. A provider preset selects its protocol and default endpoint;
an explicit endpoint wins, and a conflicting explicit driver is rejected.

The manifest's `languages` list is the release support boundary. PHP is
intentionally absent until its implementation and the same conformance gate
pass. All listed presets, including Meta Muse, remain `unverified` until a
reproducible live test establishes protocol and capability coverage.

## Sequenced implementation

1. Ship the preset boundary and shared conformance vocabulary.
2. Normalize structured output by asking the provider for the requested format,
   parsing JSON, and validating the returned value locally with a small JSON
   Schema subset. No schema-generation framework.
3. Preserve provider-reported usage and cost fields; add opt-in, in-memory
   aggregation over requests that pass through a client. Estimated cost is
   caller-supplied pricing only and is explicitly best-effort.
4. Add a PHP Composer package that implements the same contract and runs the
   shared conformance cases. It must not become a second provider framework.

Out of scope: external pricing catalogs, account billing APIs, credential
storage, automatic retries/routing/fallbacks, and live preset downloads.
