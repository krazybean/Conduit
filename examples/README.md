# Examples

[TypeScript/JavaScript text generation](typescript/generate.mjs) is runnable using
Node 22.13+ and the built local package:

```sh
npm ci --prefix typescript
npm run build --prefix typescript
CONDUIT_ENDPOINT=http://localhost:1234/v1 CONDUIT_MODEL=my-model node examples/typescript/generate.mjs
```

The endpoint must be a running Chat Completions-compatible API base URL. Supply
CONDUIT_API_KEY in the environment only when that endpoint requires bearer auth.
The TypeScript test suite also executes this example against its local mock
server. Python/Rust examples remain reserved until their implementations exist.
