# Security Policy

## Supported Versions

Conduit is currently in the `0.x` release series. Security fixes are applied to the latest published release on the `main` line; older `0.x` releases should be considered unsupported unless a release note explicitly says otherwise.

Current package versions are kept aligned across npm (`@krazybean/conduit`), PyPI (`conduit-llm`), and crates.io (`conduit-ai`).

## Reporting a Vulnerability

Please do not open a public issue for a suspected vulnerability that could expose credentials, user data, or provider access.

Report security issues privately through GitHub's security reporting features for this repository when available. Include enough information to reproduce and assess the issue, such as:

- affected Conduit package and version
- driver/provider involved
- minimal reproduction steps
- expected and observed behavior
- security impact
- whether credentials, authorization headers, URLs, diagnostics, or model content are involved

Do not include real API keys, tokens, or other secrets in the report. Use obviously fake credentials in reproductions.

Conduit treats credential handling and diagnostic redaction as security boundaries: known credentials and sensitive header values must not leak through diagnostics, while semantic model content must not be silently altered or redacted.
