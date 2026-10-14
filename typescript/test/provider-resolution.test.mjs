import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { connect } from "../dist/index.js";

const cases = JSON.parse(await readFile(new URL("../../conformance/provider-resolution.json", import.meta.url), "utf8"));
const responseFor = path => path.endsWith("/api/tags") || path.endsWith("/v1beta/models") ? { models: [] } : { data: [] };
const localEndpoint = /http:\/\/127\.0\.0\.1:0/g;

function configFor(c) {
  return JSON.parse(JSON.stringify(c.config).replace(localEndpoint, "http://127.0.0.1:43127"));
}

function assertError(c) {
  assert.throws(() => connect(configFor(c)), error => error.name === c.expected.error);
}

for (const c of cases) {
  test(`provider resolution: ${c.id}`, async () => {
    if (c.kind === "error") return assertError(c);
    if (c.kind === "resolve") {
      const originalFetch = globalThis.fetch;
      let request;
      globalThis.fetch = async (url, init) => {
        request = { url: String(url), headers: Object.fromEntries(new Headers(init.headers)) };
        return new Response(JSON.stringify(responseFor(new URL(String(url)).pathname)), { status: 200, headers: { "content-type": "application/json" } });
      };
      try { await connect(configFor(c)).listModels(); } finally { globalThis.fetch = originalFetch; }
      const expectedPath = c.expected.driver === "ollama" ? "/api/tags" : "/models";
      const basePath = new URL(c.expected.endpoint).pathname.replace(/\/+$/, "");
      assert.equal(new URL(request.url).pathname, `${basePath}${expectedPath}`);
      return;
    }

    const originalFetch = globalThis.fetch;
    let request;
    globalThis.fetch = async (url, init) => {
      request = { url: String(url), headers: Object.fromEntries(new Headers(init.headers)) };
      return new Response(JSON.stringify(responseFor(new URL(String(url)).pathname)), { status: 200, headers: { "content-type": "application/json" } });
    };
    try {
      const client = connect(configFor(c));
      await client.listModels();
    } finally {
      globalThis.fetch = originalFetch;
    }
    assert.equal(new URL(request.url).pathname, c.expected.path);
    for (const [key, value] of Object.entries(c.expected.headers)) assert.equal(request.headers[key], value);
  });
}
