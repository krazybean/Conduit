import { createServer } from "node:http";

export async function server(t, handle) {
  const requests = [];
  const http = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push({ method: request.method, path: request.url, headers: request.headers, body: JSON.parse(Buffer.concat(chunks).toString()) });
    try { await handle(request, response, requests.at(-1)); } catch { response.destroy(); }
  });
  await new Promise((resolve, reject) => { http.once("error", reject); http.listen(0, "127.0.0.1", resolve); });
  t.after(() => new Promise(resolve => { http.closeAllConnections(); http.close(resolve); }));
  return { endpoint: `http://127.0.0.1:${http.address().port}/v1`, requests };
}

