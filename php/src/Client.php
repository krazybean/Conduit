<?php
declare(strict_types=1);

namespace Conduit;

final class Model
{
    public function __construct(private readonly Client $client, private readonly string $id) {}
    public function generate(array|string $request): GenerationResponse { return $this->client->generate($this->id, $request, false); }
    public function stream(array|string $request): \Generator { return $this->client->stream($this->id, $request); }
    public function id(): string { return $this->id; }
}

final class Client
{
    private const DRIVERS = ['openai-compatible','ollama','anthropic','gemini'];
    private const PROTECTED = ['authorization','proxy-authorization','cookie','host','content-type','content-length','connection','transfer-encoding','upgrade','trailer','te','keep-alive','x-api-key','anthropic-version','x-goog-api-key'];
    private function __construct(private readonly string $driver, private readonly string $endpoint, private readonly array $headers, private readonly ?int $timeout, private readonly array $secrets) {}
    private static function manifestData(): array { static $manifest; return $manifest ??= json_decode(file_get_contents(__DIR__.'/../providers.json'), true, 512, JSON_THROW_ON_ERROR); }

    public static function connect(array $config): self|Model
    {
        $allowed = ['driver','provider','endpoint','credentials','headers','timeout','model'];
        if (array_diff(array_keys($config), $allowed)) self::invalid('Unrecognized client configuration field.');
        $provider = $config['provider'] ?? null;
        $manifest = self::manifestData();
        if ($provider !== null && (!is_string($provider) || !isset($manifest['providers'][$provider]))) self::invalid('Unknown provider preset.');
        $preset = $provider === null ? null : $manifest['providers'][$provider];
        $driver = $config['driver'] ?? ($preset['driver'] ?? null);
        if (!in_array($driver, self::DRIVERS, true)) self::invalid('Unknown driver.');
        if ($preset !== null && isset($config['driver']) && $config['driver'] !== $preset['driver']) self::invalid('provider and driver must describe the same protocol.');
        $endpoint = $config['endpoint'] ?? ($preset['endpoint'] ?? ['ollama'=>'http://localhost:11434','anthropic'=>'https://api.anthropic.com','gemini'=>'https://generativelanguage.googleapis.com'][$driver] ?? null);
        if (!is_string($endpoint) || !preg_match('~^https?://[^/?#]+(?:/[^?#]*)?$~', $endpoint) || str_contains($endpoint, '@')) self::invalid('endpoint must be an HTTP(S) API base URL without userinfo, query, or fragment.');
        $endpoint = rtrim($endpoint, '/');
        $credential = $config['credentials'] ?? null;
        if ($credential !== null && (!is_string($credential) || $credential === '' || !preg_match('/^[A-Za-z0-9._~+\/-]+=*$/', $credential))) self::invalid('credentials must be a nonempty bearer token.');
        $timeout = $config['timeout'] ?? null; self::validateTimeout($timeout);
        $headers = ['content-type'=>'application/json']; $secrets = $credential ? [$credential, rawurlencode($credential)] : [];
        if (isset($config['headers']) && !is_array($config['headers'])) self::invalid('headers must be a string-valued object.');
        foreach (($config['headers'] ?? []) as $name => $value) {
            if (!is_string($name) || in_array(strtolower($name), self::PROTECTED, true)) self::invalid('Custom header conflicts with a protected header.');
            if (!is_string($value) || preg_match('/[\r\n]/', $name.$value)) self::invalid('Invalid custom HTTP header.');
            $headers[strtolower($name)] = $value; if ($value !== '') { $secrets[] = $value; $secrets[] = rawurlencode($value); }
        }
        if ($driver === 'anthropic') { if ($credential) $headers['x-api-key'] = $credential; $headers['anthropic-version'] = '2023-06-01'; }
        elseif ($driver === 'gemini') { if ($credential) $headers['x-goog-api-key'] = $credential; }
        elseif ($credential) $headers['authorization'] = 'Bearer '.$credential;
        $client = new self($driver, $endpoint, $headers, $timeout, $secrets);
        if (isset($config['model'])) return $client->model($config['model']);
        return $client;
    }
    public function model(string $id): Model { if (trim($id) === '') self::invalid('model must be a nonempty string.'); return new Model($this, $id); }
    public static function manifest(): array { return self::manifestData(); }
    public function listModels(array $options = []): array
    {
        if (array_diff(array_keys($options), ['timeout'])) self::invalid('Unrecognized listModels option.');
        self::validateTimeout($options['timeout'] ?? null); $timeout = $options['timeout'] ?? $this->timeout;
        $path = $this->driver === 'ollama' ? '/api/tags' : ($this->driver === 'anthropic' ? '/v1/models' : ($this->driver === 'gemini' ? '/v1beta/models' : '/models'));
        $all = []; $cursor = null; $seen = [];
        for ($page = 0; $page < 100; $page++) {
            $url = $this->endpoint.$path;
            if ($cursor !== null) $url .= '?'.($this->driver === 'anthropic' ? 'after_id=' : 'pageToken=').rawurlencode($cursor);
            if ($cursor !== null && isset($seen[$cursor])) self::protocol('Malformed pagination cursor.');
            if ($cursor !== null) $seen[$cursor] = true;
            $value = $this->decodeJson($this->request('GET', $url, null, $timeout)['body']);
            $items = $this->driver === 'ollama' || $this->driver === 'gemini' ? ($value['models'] ?? null) : ($value['data'] ?? null);
            if (!is_array($items)) self::protocol('Provider returned malformed model list.');
            foreach ($items as $m) $all[] = $this->modelInfo($m);
            if ($this->driver === 'anthropic' && ($value['has_more'] ?? false) === true && (!is_string($value['last_id'] ?? null) || trim($value['last_id']) === '')) self::protocol('Malformed pagination: last_id required when has_more is true.');
            $next = $this->driver === 'anthropic' ? (($value['has_more'] ?? false) ? $value['last_id'] : null) : ($value['nextPageToken'] ?? null);
            if ($next === null || $next === '') return $all;
            if (!is_string($next) || $next === $cursor || ($items === [] && $next !== null)) self::protocol('Malformed pagination cursor.');
            $cursor = $next;
        }
        self::protocol('Too many pagination pages.');
    }

    private function modelInfo(mixed $m): ModelInfo
    {
        if (!is_array($m)) self::protocol('Provider returned malformed model list.');
        if ($this->driver === 'gemini') $id = isset($m['name']) && is_string($m['name']) ? preg_replace('~^models/~', '', $m['name']) : null;
        else $id = $this->driver === 'ollama' ? ($m['name'] ?? $m['model'] ?? null) : ($m['id'] ?? null);
        if (!is_string($id) || trim($id) === '') self::protocol('Provider returned malformed model list.');
        $name = $this->driver === 'anthropic' ? ($m['display_name'] ?? null) : ($this->driver === 'gemini' ? ($m['displayName'] ?? null) : ($this->driver === 'ollama' && isset($m['name']) ? $m['name'] : null));
        $meta = $m; unset($meta['id'], $meta['name'], $meta['model'], $meta['display_name'], $meta['displayName']);
        if ($this->driver === 'gemini' && isset($m['name']) && $m['name'] !== $id) $meta['name'] = $m['name'];
        return new ModelInfo($this->redact($id), is_string($name) && trim($name) !== '' ? $this->redact($name) : null, $meta ? $this->redactValue($meta) : null);
    }
    public function generate(string $model, array|string $request, bool $streaming): GenerationResponse
    {
        $req = self::normalize($request); $body = $this->encode($model, $req, $streaming); $url = $this->url($model, $streaming); $r = $this->request('POST', $url, $body, $req['timeout'] ?? $this->timeout); if ($r['status'] < 200 || $r['status'] >= 300) $this->providerError($r); return $this->decode($this->decodeJson($r['body']), $r['headers']);
    }
    public function stream(string $model, array|string $request): \Generator
    {
        $req = self::normalize($request); $body = $this->encode($model, $req, true);
        if (!function_exists('curl_init')) throw new ConduitError('ConnectionError', 'PHP ext-curl is required for streaming.');
        $timeout = $req['timeout'] ?? $this->timeout; $deadline = $timeout === null ? null : microtime(true) + $timeout / 1000;
        $signal = $req['signal'] ?? null; $multi = curl_multi_init(); $handle = curl_init($this->url($model, true));
        $events = []; $deferredUsage = []; $error = null; $status = 0; $responseHeaders = []; $errorBody = ''; $parser = ['buffer'=>'','event'=>null,'data'=>[],'dataBytes'=>0,'done'=>false];
        $semantic = ['started'=>false,'id'=>null,'model'=>null,'finish'=>null,'finishRaw'=>null,'text'=>'','sawText'=>false,'content'=>[],'usage'=>null,'tools'=>[],'done'=>false,'metadata'=>[],'initialUsageEmitted'=>false];
        $addUsage = function (?array $raw, array $map) use (&$events, &$deferredUsage, &$semantic, &$error): void {
            if ($raw === null) return;
            $next = $semantic['usage'] ?? [];
            foreach ($map as $from=>$to) if (array_key_exists($from,$raw)) { if (!is_int($raw[$from]) || $raw[$from] < 0) { $error = new ConduitError('ProtocolError','Malformed provider usage.'); return; } $next[$to] = $raw[$from]; }
            if ($next) { $semantic['usage'] = $next; $event = ['type'=>'usage','usage'=>$next]; if ($this->driver === 'gemini') $deferredUsage[] = $event; else $events[] = $event; }
        };
        $push = function (string $payload, ?string $eventName = null) use (&$events, &$error, &$semantic, &$responseHeaders, $addUsage): void {
            if ($payload === '[DONE]') {
                if (!$semantic['started'] || $semantic['finish'] === null) { $error = new ConduitError('ProtocolError', 'Malformed or incomplete provider stream.'); return; }
                $finalContent = ($semantic['sawText'] || $semantic['text'] !== '') ? [['type'=>'text','text'=>$semantic['text']]] : [];
                foreach ($semantic['content'] as $part) if (($part['type'] ?? null) === 'tool_call') $finalContent[] = $part;
                if (isset($responseHeaders['x-request-id'])) $semantic['metadata']['requestId'] = $this->redact($responseHeaders['x-request-id']);
                $events[] = ['type'=>'done', 'response'=>new GenerationResponse($semantic['id'], $semantic['model'], $finalContent, $semantic['finish'], $semantic['usage'], $semantic['metadata'])]; $semantic['done'] = true; return;
            }
            $value = json_decode($payload, true);
            if (!is_array($value)) { $error = new ConduitError('ProtocolError', 'Malformed provider stream.'); return; }
            if ($this->driver === 'openai-compatible' && array_key_exists('usage', $value) && array_key_exists('content', $value['choices'][0]['delta'] ?? []) && ($value['choices'][0]['delta']['content'] ?? null) === '') $semantic['sawText'] = true;
            if (($value['error'] ?? null) !== null) { $error = new ConduitError('ProviderError', is_array($value['error']) ? (string)($value['error']['message'] ?? 'Provider returned an in-band error.') : 'Provider returned an in-band error.'); return; }
            if ($this->driver === 'anthropic') {
                if ($eventName === 'message_start') { $m = $value['message'] ?? null; if (!is_array($m) || !is_string($m['id']??null) || !is_string($m['model']??null)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['id']=$m['id']; $semantic['model']=$m['model']; $semantic['started']=true; $semantic['metadata']['requestId']=$responseHeaders['request-id']??null; $events[]=['type'=>'start','id'=>$m['id'],'model'=>$m['model']]; foreach (['input_tokens'=>'input_tokens','output_tokens'=>'output_tokens'] as $from=>$to) if (isset($m['usage'][$from])) $semantic['usage'][$to]=$m['usage'][$from]; return; }
                if (!$semantic['started']) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; }
                if ($eventName === 'content_block_start') { $b=$value['content_block']??null; if (is_array($b)&&($b['type']??null)==='tool_use') { if ($semantic['usage'] && !$semantic['initialUsageEmitted']) { $events[]=['type'=>'usage','usage'=>$semantic['usage']]; $semantic['initialUsageEmitted']=true; } $semantic['tools'][$value['index']??0]=['id'=>$b['id']??null,'name'=>$b['name']??null,'arguments'=>'']; $events[]=['type'=>'tool_call_delta','index'=>(int)($value['index']??0),'id'=>$b['id'],'name'=>$b['name']]; } return; }
                if ($eventName === 'content_block_delta') { $d=$value['delta']??null; $index=(int)($value['index']??0); if (!is_array($d)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } if (($d['type']??null)==='text_delta') { if (!is_string($d['text']??null)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['text'].=$d['text']; $semantic['content'][]=['type'=>'text','text'=>$d['text']]; $events[]=['type'=>'text_delta','index'=>$index,'text'=>$d['text']]; } elseif (($d['type']??null)==='input_json_delta' && isset($semantic['tools'][$index])) { $semantic['tools'][$index]['arguments'].=(string)($d['partial_json']??''); $events[]=['type'=>'tool_call_delta','index'=>$index,'argumentsDelta'=>(string)($d['partial_json']??'')]; } else { $error = new ConduitError('ProtocolError','Malformed provider stream '.$eventName); } return; }
                if ($eventName === 'content_block_stop') return;
                if ($eventName === 'message_delta') { if ($semantic['usage'] && !$semantic['initialUsageEmitted']) { $events[]=['type'=>'usage','usage'=>$semantic['usage']]; $semantic['initialUsageEmitted']=true; } $d=$value['delta']??[]; if (isset($d['stop_reason'])&&is_string($d['stop_reason'])) { $semantic['finishRaw']=$d['stop_reason']; $semantic['finish']=$d['stop_reason']==='tool_use'?'tool_call':($d['stop_reason']==='max_tokens'?'length':'stop'); $semantic['metadata']['finishReason']=$d['stop_reason']; } $addUsage($value['usage']??null,['input_tokens'=>'input_tokens','output_tokens'=>'output_tokens']); return; }
                if ($eventName === 'message_stop') { if ($semantic['finish']===null) $semantic['finish']='stop'; foreach ($semantic['tools'] as $tool) { $args=json_decode($tool['arguments'] ?: '{}',true); if (!is_array($args)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['content'][]=['type'=>'tool_call',...(!empty($tool['id'])?['id'=>$tool['id']]:[]),'name'=>$tool['name'],'arguments'=>$args]; } return; }
                return;
            }
            if ($this->driver === 'ollama') {
                $chunk = $value['message']['content'] ?? $value['response'] ?? '';
                if (!is_string($chunk)) { $error = new ConduitError('ProtocolError', 'Malformed provider stream.'); return; }
                if (!$semantic['started']) { $semantic['started'] = true; $events[] = ['type'=>'start']; }
                if ($chunk !== '') { $semantic['text'] .= $chunk; $semantic['content'][]=['type'=>'text','text'=>$chunk]; $events[] = ['type'=>'text_delta','index'=>0,'text'=>$chunk]; }
                if (($value['done'] ?? false) === true) { $semantic['finish'] = 'stop'; $semantic['done'] = true; $addUsage($value,['prompt_eval_count'=>'input_tokens','eval_count'=>'output_tokens']); }
                return;
            }
            if ($this->driver === 'gemini') { $parts = $value['candidates'][0]['content']['parts'] ?? null; if (!is_array($parts)) { $error = new ConduitError('ProtocolError', 'Malformed provider stream.'); return; } if (!$semantic['started']) { $semantic['started']=true; $events[]=['type'=>'start']; } foreach ($parts as $p) { if (isset($p['text'])) { if (!is_string($p['text'])) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['text'].=$p['text']; $semantic['content'][]=['type'=>'text','text'=>$p['text']]; $events[]=['type'=>'text_delta','index'=>0,'text'=>$p['text']]; } elseif (isset($p['functionCall'])) { $f=$p['functionCall']; if (!is_array($f)||!is_string($f['name']??null)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['tools'][]=['id'=>$f['id']??null,'name'=>$f['name'],'arguments'=>json_encode($f['args']??[])]; $i=count($semantic['tools'])-1; $events[]=['type'=>'tool_call_delta','index'=>$i,...(!empty($f['id'])?['id'=>$f['id']]:[]),'name'=>$f['name'],'argumentsDelta'=>json_encode($f['args']??[])]; } } if (isset($value['candidates'][0]['finishReason'])&&is_string($value['candidates'][0]['finishReason'])) { $raw=$value['candidates'][0]['finishReason']; $semantic['metadata']['finishReason']=$raw; $semantic['finish']=match($raw){'STOP'=>'stop','MAX_TOKENS'=>'length','SAFETY','RECITATION','BLOCKLIST','PROHIBITED_CONTENT','SPII','IMAGE_SAFETY'=>'content_filter',default=>'other'}; } $addUsage($value['usageMetadata']??null,['promptTokenCount'=>'input_tokens','candidatesTokenCount'=>'output_tokens','totalTokenCount'=>'total_tokens']); return; }
            $choice = $value['choices'][0] ?? null; if (!is_array($choice) || (isset($choice['index']) && $choice['index'] !== 0)) { if (isset($value['usage'])) { $addUsage($value['usage'],['prompt_tokens'=>'input_tokens','completion_tokens'=>'output_tokens','total_tokens'=>'total_tokens']); return; } $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } $delta=$choice['delta']??null; if (!is_array($delta)) { $error = new ConduitError('ProtocolError','Malformed provider stream.'); return; } if (!$semantic['started']) { $semantic['started']=true; $semantic['id']=isset($value['id'])&&is_string($value['id'])?$value['id']:null; $semantic['model']=isset($value['model'])&&is_string($value['model'])?$value['model']:null; $events[]=['type'=>'start',...($semantic['id']!==null?['id'=>$semantic['id']]:[]),...($semantic['model']!==null?['model'=>$semantic['model']]:[])]; } if (array_key_exists('content',$delta)) { if ($delta['content']!==null&&!is_string($delta['content'])) { $error=new ConduitError('ProtocolError','Malformed provider stream.'); return; } if ($delta['content']!==null&&$delta['content']!=='') { $semantic['text'].=$delta['content']; $semantic['content'][]=['type'=>'text','text'=>$delta['content']]; $events[]=['type'=>'text_delta','index'=>0,'text'=>$delta['content']]; } } foreach (($delta['tool_calls']??[]) as $tc) { $i=(int)($tc['index']??0); $f=$tc['function']??[]; if (!is_array($f)) { $error=new ConduitError('ProtocolError','Malformed provider stream.'); return; } if (!isset($semantic['tools'][$i])) $semantic['tools'][$i]=['id'=>null,'name'=>'','arguments'=>'']; if (isset($tc['id'])) $semantic['tools'][$i]['id']=$tc['id']; if (isset($f['name'])) $semantic['tools'][$i]['name'].=$f['name']; if (isset($f['arguments'])) { if (!is_string($f['arguments'])) { $error=new ConduitError('ProtocolError','Malformed provider stream.'); return; } $semantic['tools'][$i]['arguments'].=$f['arguments']; } $event=['type'=>'tool_call_delta','index'=>$i]; if (isset($tc['id'])) $event['id']=$tc['id']; if (isset($f['name'])) $event['name']=$f['name']; if (isset($f['arguments'])) $event['argumentsDelta']=$f['arguments']; $events[]=$event; }
            if (isset($choice['finish_reason'])&&is_string($choice['finish_reason'])) { $semantic['finishRaw']=$choice['finish_reason']; $semantic['metadata']['finishReason']=$this->redact($choice['finish_reason']); $semantic['finish']=$semantic['tools']?'tool_call':match($choice['finish_reason']){'stop'=>'stop','length'=>'length','content_filter'=>'content_filter',default=>'other'}; }
            if (isset($value['usage'])) $addUsage($value['usage'],['prompt_tokens'=>'input_tokens','completion_tokens'=>'output_tokens','total_tokens'=>'total_tokens']);
            foreach ($semantic['tools'] as $i=>$tool) if (!isset($tool['_emitted'])) { $args=json_decode($tool['arguments']?:'{}',true); if (is_array($args)) $semantic['content'][]=['type'=>'tool_call',...(!empty($tool['id'])?['id'=>$tool['id']]:[]),'name'=>$tool['name'],'arguments'=>$args]; $semantic['tools'][$i]['_emitted']=true; }
        };
        $feed = function (string $chunk) use (&$parser, &$error, $push): void {
            $parser['buffer'] .= str_replace(["\r\n", "\r"], "\n", $chunk);
            if (strlen($parser['buffer']) + $parser['dataBytes'] > 1048576) { $error = new ConduitError('ProtocolError', 'Provider stream event exceeded the 1 MiB limit.'); return; }
            $eventName = $parser['event'];
            while (($break = strpos($parser['buffer'], "\n")) !== false) {
                $line = substr($parser['buffer'], 0, $break); $parser['buffer'] = substr($parser['buffer'], $break + 1); $line = rtrim($line, "\r"); $line = ltrim($line, "\xEF\xBB\xBF");
                if ($line === '') { if ($parser['data']) { $push(implode("\n", $parser['data']), $eventName); $parser['data'] = []; $parser['dataBytes'] = 0; $eventName = null; $parser['event'] = null; } continue; }
                if ($this->driver === 'ollama') { $push($line); continue; }
                $colon = strpos($line, ':'); $field = $colon === false ? $line : substr($line, 0, $colon); $value = $colon === false ? '' : substr($line, $colon + 1); if (str_starts_with($value, ' ')) $value = substr($value, 1);
                if ($field === 'event') { $eventName = $value; $parser['event'] = $value; } elseif ($field === 'data') { $parser['data'][] = $value; $parser['dataBytes'] += strlen($value); }
            }
        };
        $progress = function ($ch, int $downloadTotal, int $downloaded, int $uploadTotal, int $uploaded) use ($signal, $deadline, &$error): int { if (is_object($signal) && method_exists($signal, 'isCancelled') && $signal->isCancelled()) { $error = new ConduitError('CancelledError', 'Request cancelled by caller.'); return 1; } if ($deadline !== null && microtime(true) >= $deadline) { $error = new ConduitError('TimeoutError', 'Request deadline exceeded.'); return 1; } return 0; };
        if (!curl_setopt_array($handle, [CURLOPT_POST=>true, CURLOPT_POSTFIELDS=>$body, CURLOPT_HTTPHEADER=>array_merge(array_map(fn($k,$v)=>$k.': '.$v,array_keys($this->headers),$this->headers), ['accept: text/event-stream']), CURLOPT_WRITEFUNCTION=>function ($ch, string $chunk) use ($feed, &$error, &$status, &$errorBody): int { if ($error !== null) return 0; if ($status >= 400) { $errorBody .= strlen($errorBody) < 65536 ? substr($chunk, 0, 65536 - strlen($errorBody)) : ''; return strlen($chunk); } $feed($chunk); return $error === null ? strlen($chunk) : 0; }, CURLOPT_HEADERFUNCTION=>function ($ch, string $line) use (&$status, &$responseHeaders): int { if (preg_match('/^HTTP\/\S+\s+(\d+)/', $line, $m)) $status = (int)$m[1]; elseif (str_contains($line, ':')) { [$k,$v] = array_map('trim', explode(':', $line, 2)); $responseHeaders[strtolower($k)] = $v; } return strlen($line); }, CURLOPT_NOPROGRESS=>false, CURLOPT_XFERINFOFUNCTION=>$progress, CURLOPT_TIMEOUT_MS=>$timeout ?? 0, CURLOPT_CONNECTTIMEOUT_MS=>$timeout ?? 0])) throw new ConduitError('ConnectionError', 'Could not configure provider connection.');
        curl_multi_add_handle($multi, $handle);
        try {
            $running = 0; do { do { $multiCode = curl_multi_exec($multi, $running); } while ($multiCode === CURLM_CALL_MULTI_PERFORM); if ($multiCode !== CURLM_OK) throw new ConduitError('ConnectionError', 'Provider connection failed.'); while ($events) yield array_shift($events); if ($error !== null) throw $error; if ($running > 0 && !$events) { $selected = curl_multi_select($multi, 1.0); if ($selected === -1) usleep(1000); } } while ($running > 0);
            if ($error !== null) throw $error;
            $curlResult = CURLE_OK;
            while (($info = curl_multi_info_read($multi)) !== false) $curlResult = $info['result'];
            $curlError = curl_errno($handle);
            $curlMessage = curl_error($handle);
            if ($curlResult !== CURLE_OK || $curlError !== 0) {
                if ($deadline !== null && microtime(true) >= $deadline) throw new ConduitError('TimeoutError', 'Request deadline exceeded.');
                throw new ConduitError('ConnectionError', $curlMessage !== '' ? 'Provider connection failed: '.$curlMessage : 'Provider connection failed.');
            }
            $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
            if ($status === 0) throw new ConduitError('ConnectionError', 'Provider returned no HTTP response.');
            if ($status < 200 || $status >= 300) $this->providerError(['status'=>$status,'body'=>$errorBody,'headers'=>$responseHeaders]);
            if ($this->driver === 'ollama' && $parser['buffer'] !== '') { $push($parser['buffer']); $parser['buffer'] = ''; }
            if ($parser['buffer'] !== '' || $parser['data']) throw new ConduitError('ProtocolError', 'Malformed or incomplete provider stream.');
            if ($this->driver === 'anthropic' && !$semantic['done'] && $semantic['finish'] !== null) { if (isset($responseHeaders['request-id'])) $semantic['metadata']['requestId'] = $this->redact($responseHeaders['request-id']); $finalContent = $semantic['text'] === '' ? [] : [['type'=>'text','text'=>$semantic['text']]]; foreach ($semantic['content'] as $part) if (($part['type'] ?? null) === 'tool_call') $finalContent[] = $part; $events[]=['type'=>'done','response'=>new GenerationResponse($semantic['id'],$semantic['model'],$finalContent,$semantic['finish'],$semantic['usage'],$semantic['metadata'])]; $semantic['done']=true; }
            if ($this->driver === 'gemini' && !$semantic['done'] && $semantic['finish'] !== null) { if (isset($responseHeaders['x-request-id'])) $semantic['metadata']['requestId'] = $this->redact($responseHeaders['x-request-id']); foreach ($deferredUsage as $event) $events[] = $event; $finalContent = $semantic['text'] === '' ? [] : [['type'=>'text','text'=>$semantic['text']]]; foreach ($semantic['content'] as $part) if (($part['type'] ?? null) === 'tool_call') $finalContent[] = $part; $events[]=['type'=>'done','response'=>new GenerationResponse($semantic['id'],$semantic['model'],$finalContent,$semantic['finish'],$semantic['usage'],$semantic['metadata'])]; $semantic['done']=true; }
            if (!$semantic['done']) throw new ConduitError('ProtocolError', 'Malformed or incomplete provider stream.');
            while ($events) yield array_shift($events);
        } finally { curl_multi_remove_handle($multi, $handle); curl_multi_close($multi); }
    }
    private static function normalize(array|string $request): array {
        if (is_string($request)) return ['messages'=>[['role'=>'user','content'=>$request]]];
        $aliases=['maxOutputTokens'=>'max_output_tokens','topP'=>'top_p','toolChoice'=>'tool_choice','responseFormat'=>'response_format','providerOptions'=>'provider_options'];
        foreach ($aliases as $from=>$to) if (array_key_exists($from,$request) && !array_key_exists($to,$request)) $request[$to]=$request[$from];
        $allowed=['messages','max_output_tokens','temperature','top_p','stop','tools','tool_choice','response_format','provider_options','timeout','signal'];
        if (array_diff(array_keys($request),$allowed)) self::invalid('Unrecognized generation request field.');
        if (!isset($request['messages']) || !is_array($request['messages']) || !$request['messages']) self::invalid('messages must be a nonempty array.');
        foreach ($request['messages'] as $message) {
            if (!is_array($message) || !in_array($message['role'] ?? null, ['system','user','assistant','tool'], true)) self::invalid('Invalid message role.');
            $parts = is_string($message['content'] ?? null) ? [['type'=>'text','text'=>$message['content']]] : ($message['content'] ?? null);
            if (!is_array($parts) || !$parts) self::invalid('Message content must be nonempty.');
            $hasCall = $hasResult = $hasText = false;
            foreach ($parts as $part) {
                if (!is_array($part) || !isset($part['type'])) self::invalid('Invalid content part.');
                if ($part['type'] === 'text') { if (!is_string($part['text'] ?? null)) self::invalid('Invalid text content part.'); $hasText = true; }
                elseif ($part['type'] === 'tool_call') { if ($message['role'] !== 'assistant' || !is_string($part['name'] ?? null) || trim($part['name']) === '') self::invalid('Invalid tool call.'); $hasCall = true; }
                elseif ($part['type'] === 'tool_result') { if ($message['role'] !== 'tool' || !array_key_exists('content', $part)) self::invalid('Invalid tool result.'); $hasResult = true; }
                else self::invalid('Invalid content part type.');
            }
            if ($message['role'] === 'tool' && (!$hasResult || $hasText || $hasCall)) self::invalid('Tool messages must contain only tool_result parts.');
            if ($message['role'] !== 'tool' && $hasResult) self::invalid('Only tool messages may contain tool_result parts.');
        }
        if (isset($request['tools'])) self::validateTools($request['tools']);
        if (isset($request['response_format'])) {
            $format=$request['response_format']; if (!is_array($format) || !in_array($format['type']??null,['text','json','json_schema'],true)) self::invalid('response_format.type must be text, json, or json_schema.');
            if (($format['type']??null)==='json_schema' && !array_key_exists('schema',$format)) self::invalid('response_format.schema is required for json_schema.');
        }
        if (isset($request['timeout'])) self::validateTimeout($request['timeout']);
        if (isset($request['signal']) && is_object($request['signal']) && method_exists($request['signal'],'isCancelled') && $request['signal']->isCancelled()) throw new ConduitError('CancelledError','Request cancelled by caller.');
        if (isset($request['provider_options'])) { if (!is_array($request['provider_options'])) self::invalid('provider_options must be an object.'); self::json($request['provider_options']); }
        return $request;
    }
    private function url(string $model, bool $stream): string { if ($this->driver === 'gemini') return $this->endpoint.'/v1beta/models/'.rawurlencode($model).($stream ? ':streamGenerateContent?alt=sse' : ':generateContent'); return $this->endpoint.($this->driver === 'ollama' ? '/api/chat' : ($this->driver === 'anthropic' ? '/v1/messages' : '/chat/completions')); }
    private function encode(string $model, array $r, bool $stream): string {
        if ($this->driver === 'anthropic' && isset($r['response_format'])) throw new ConduitError('UnsupportedCapabilityError','response_format is not supported for Anthropic.');
        $out = match ($this->driver) {
            'ollama'=>['model'=>$model,'messages'=>$this->messages($r['messages']),'stream'=>$stream],
            'anthropic'=>['model'=>$model,'messages'=>$this->messages(array_values(array_filter($r['messages'], fn($m) => $m['role'] !== 'system'))),'max_tokens'=>$r['max_output_tokens'] ?? 4096,'stream'=>$stream],
            'gemini'=>['contents'=>$this->geminiMessages($r['messages'])],
            default=>['model'=>$model,'messages'=>$this->messages($r['messages']),'stream'=>$stream],
        };
        if ($this->driver === 'anthropic') {
            $system = array_values(array_filter($r['messages'], fn($m) => $m['role'] === 'system'));
            if ($system) $out['system'] = implode('', array_map(fn($m) => implode('', array_map(fn($p) => $p['text'], is_string($m['content']) ? [['text'=>$m['content']]] : $m['content'])), $system));
        }
        if ($this->driver === 'gemini') {
            $system = array_values(array_filter($r['messages'], fn($m) => $m['role'] === 'system'));
            if ($system) $out['systemInstruction'] = ['parts'=>array_map(fn($m) => ['text'=>is_string($m['content']) ? $m['content'] : implode('', array_map(fn($p) => $p['text'], $m['content']))], $system)];
        }
        if ($this->driver === 'gemini') {
            $format = $r['response_format'] ?? null;
            $out['generationConfig'] = array_filter(['maxOutputTokens'=>$r['max_output_tokens'] ?? null,'temperature'=>$r['temperature'] ?? null,'topP'=>$r['top_p'] ?? null,'stopSequences'=>$r['stop'] ?? null,'responseMimeType'=>$format && ($format['type'] ?? null) !== 'text' ? 'application/json' : null,'responseJsonSchema'=>$format['schema'] ?? null], fn($v) => $v !== null);
        } else foreach (['max_output_tokens'=>'max_tokens','temperature'=>'temperature','top_p'=>'top_p','stop'=>$this->driver === 'anthropic' ? 'stop_sequences' : 'stop'] as $from=>$to) if (array_key_exists($from,$r)) $out[$to]=$r[$from];
        if ($this->driver !== 'gemini' && isset($r['response_format']) && ($r['response_format']['type'] ?? null) !== 'text') $out['response_format']=($r['response_format']['type']==='json')?['type'=>'json_object']:['type'=>'json_schema','json_schema'=>['name'=>'response','strict'=>true,'schema'=>$r['response_format']['schema']]];
        if (!empty($r['tools'])) $out['tools']=$this->tools($r['tools']);
        if (!empty($r['provider_options'])) $out=array_replace_recursive($r['provider_options'],$out);
        return self::json($out);
    }
    private function messages(array $messages): array { return array_map(function($m): array { $role=$m['role']; $parts=is_string($m['content'])?[['type'=>'text','text'=>$m['content']]]:$m['content']; if ($role==='tool') { $p=$parts[0]; $content=$p['content']; $text=is_string($content)?$content:implode('',array_map(fn($x)=>$x['text']??'', $content)); return ['role'=>'tool','content'=>$text,...(!empty($p['callId'])?['tool_call_id'=>$p['callId']]:[])]; } $text=array_values(array_filter($parts,fn($p)=>($p['type']??'')==='text')); $calls=array_values(array_filter($parts,fn($p)=>($p['type']??'')==='tool_call')); if ($calls) return ['role'=>'assistant','content'=>$text?implode('',array_map(fn($p)=>$p['text'],$text)):null,'tool_calls'=>array_map(fn($p)=>['id'=>$p['id']??('call_'.bin2hex(random_bytes(6))),'type'=>'function','function'=>['name'=>$p['name'],'arguments'=>self::json($p['arguments']??[])]],$calls)]; $textValue=implode('',array_map(fn($p)=>$p['text'],$text)); return ['role'=>$role,'content'=>in_array($this->driver,['anthropic','ollama'],true)?$textValue:array_values($text)]; }, $messages); }
    private function geminiMessages(array $messages): array { $out=[]; foreach ($messages as $m) { $parts=is_string($m['content'])?[['type'=>'text','text'=>$m['content']]]:$m['content']; if ($m['role']==='system') continue; if ($m['role']==='tool') { foreach ($parts as $p) { $text=is_string($p['content'])?$p['content']:implode('',array_map(fn($x)=>$x['text']??'', $p['content'])); $out[]=['role'=>'user','parts'=>[['functionResponse'=>['name'=>$p['name']??$p['callId']??'tool','response'=>json_decode($text,true) ?? ['result'=>$text]]]]]; } continue; } $out[]=['role'=>$m['role']==='assistant'?'model':'user','parts'=>array_map(fn($p)=>($p['type']??'')==='tool_call'?['functionCall'=>array_filter(['id'=>$p['id']??null,'name'=>$p['name'],'args'=>$p['arguments']??[]],fn($v)=>$v!==null)]:['text'=>$p['text']??''],$parts)]; } return $out; }
    private function tools(array $tools): array { return $this->driver === 'anthropic' ? array_map(fn($t)=>['name'=>$t['name'],...(!empty($t['description'])?['description'=>$t['description']]:[]),'input_schema'=>$t['inputSchema']??$t['input_schema']??[]],$tools) : array_map(fn($t)=>['type'=>'function','function'=>['name'=>$t['name'],...(!empty($t['description'])?['description'=>$t['description']]:[]),'parameters'=>$t['inputSchema']??$t['input_schema']??[]]],$tools); }
    private static function validateTools(mixed $tools): void { if (!is_array($tools) || !$tools) self::invalid('tools must be a nonempty array.'); $names=[]; foreach ($tools as $tool) { if (!is_array($tool) || !is_string($tool['name']??null) || trim($tool['name'])==='' || isset($names[$tool['name']])) self::invalid('Each tool must have a unique nonempty name.'); $names[$tool['name']]=true; self::json($tool['inputSchema']??$tool['input_schema']??null); } }
    private static function json(mixed $value): string { try { return json_encode($value, JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR); } catch (\JsonException) { self::invalid('Value must contain acyclic JSON data.'); } }
    private function request(string $method, string $url, ?string $body, ?int $timeout): array { self::validateTimeout($timeout); $context=stream_context_create(['http'=>['method'=>$method,'header'=>$this->headerString(),'content'=>$body??'','ignore_errors'=>true,'timeout'=>($timeout??30000)/1000], 'ssl'=>['verify_peer'=>true,'verify_peer_name'=>true]]); $start=microtime(true); $raw=@file_get_contents($url,false,$context); if ($raw===false) throw new ConduitError($timeout!==null && microtime(true)-$start >= $timeout/1000 ? 'TimeoutError':'ConnectionError','Provider connection failed.'); $headers=[]; $status=200; foreach ($http_response_header??[] as $h) { if (preg_match('/^HTTP\/\S+\s+(\d+)/',$h,$m)) { $status=(int)$m[1]; } elseif (str_contains($h,':')) { [$k,$v]=array_map('trim',explode(':',$h,2)); $headers[strtolower($k)]=$v; } } return ['status'=>$status,'headers'=>$headers,'body'=>$raw]; }
    private function headerString(): string { return implode("\r\n", array_map(fn($k,$v)=>$k.': '.$v,array_keys($this->headers),$this->headers)); }
    private function decodeJson(string $body): array { $v=json_decode($body,true); if (!is_array($v)) self::protocol('Provider returned invalid JSON.'); return $v; }
    private function decode(array $v, array $headers): GenerationResponse { if ($this->driver==='ollama') { if (!is_string($v['model']??null) || !is_array($v['message']??null) || !is_string($v['message']['content']??null)) self::protocol('Malformed provider response.'); return new GenerationResponse($v['created_at']??null,$v['model'],[['type'=>'text','text'=>$v['message']['content']]],($v['done_reason']??'stop')==='stop'?'stop':'other'); } if ($this->driver==='anthropic') return $this->decodeAnthropic($v,$headers); if ($this->driver==='gemini') return $this->decodeGemini($v,$headers); if (!isset($v['choices']) || !is_array($v['choices']) || count($v['choices']) !== 1) self::protocol('Malformed provider response.'); $c=$v['choices'][0]; if (!is_array($c)||!is_array($c['message']??null)||!array_key_exists('finish_reason',$c)) self::protocol('Malformed provider response.'); $m=$c['message']; $parts=[]; if ($m['content'] !== null && !is_string($m['content'])) self::protocol('Malformed provider response.'); if (is_string($m['content']??null)) $parts[]=['type'=>'text','text'=>$m['content']]; foreach (($m['tool_calls']??[]) as $tc) { if (!is_array($tc)||!is_array($tc['function']??null)||!is_string($tc['function']['name']??null)) self::protocol('Malformed provider response.'); $args=is_string($tc['function']['arguments']??null)?json_decode($tc['function']['arguments'],true):($tc['function']['arguments']??null); if (!is_array($args)) self::protocol('Malformed provider response.'); $parts[]=['type'=>'tool_call',...(!empty($tc['id'])?['id'=>$tc['id']]:[]),'name'=>$tc['function']['name'],'arguments'=>$args]; } $metadata=[]; if (isset($c['finish_reason']) && is_string($c['finish_reason'])) $metadata['finishReason']=$this->redact($c['finish_reason']); if (isset($headers['x-request-id'])) $metadata['requestId']=$this->redact($headers['x-request-id']); return new GenerationResponse(isset($v['id'])&&is_string($v['id'])?$this->redact($v['id']):null,isset($v['model'])&&is_string($v['model'])?$this->redact($v['model']):null,$parts,$parts && array_filter($parts,fn($p)=>$p['type']==='tool_call')?'tool_call':match($c['finish_reason']){'tool_calls'=>'tool_call','stop'=>'stop','length'=>'length','content_filter'=>'content_filter',default=>'other'},$this->usage($v['usage']??null),$metadata); }
    private function decodeAnthropic(array $v, array $headers): GenerationResponse { if (($v['type']??null)!=='message'||($v['role']??null)!=='assistant'||!is_string($v['id']??null)||!is_string($v['model']??null)||!is_array($v['content']??null)) self::protocol('Malformed provider response.'); $parts=[]; foreach ($v['content'] as $p) { if (!is_array($p)) self::protocol('Malformed provider response.'); if (($p['type']??null)==='text' && is_string($p['text']??null)) $parts[]=['type'=>'text','text'=>$p['text']]; elseif (($p['type']??null)==='tool_use' && is_string($p['id']??null) && is_string($p['name']??null) && is_array($p['input']??null)) $parts[]=['type'=>'tool_call','id'=>$p['id'],'name'=>$p['name'],'arguments'=>$p['input']]; else self::protocol('Malformed provider response.'); } $metadata=[]; if (isset($v['stop_reason']) && is_string($v['stop_reason'])) $metadata['finishReason']=$this->redact($v['stop_reason']); if (isset($headers['request-id'])) $metadata['requestId']=$this->redact($headers['request-id']); $finish=match($v['stop_reason']??null){'tool_use'=>'tool_call','max_tokens'=>'length','refusal'=>'content_filter',null=>'other',default=>'stop'}; return new GenerationResponse($this->redact($v['id']),$this->redact($v['model']),$parts,$finish,$this->normalizedUsage($v['usage']??null,['input_tokens'=>'input_tokens','output_tokens'=>'output_tokens']),$metadata); }
    private function decodeGemini(array $v, array $headers): GenerationResponse { $candidates=$v['candidates']??null; if (!is_array($candidates)||count($candidates)!==1||!is_array($candidates[0]['content']??null)||!is_array($candidates[0]['content']['parts']??null)) { if (is_array($v['promptFeedback']??null)&&is_string($v['promptFeedback']['blockReason']??null)) return new GenerationResponse(null,null,[],'content_filter',$this->geminiUsage($v['usageMetadata']??null),['finishReason'=>$this->redact($v['promptFeedback']['blockReason'])]); self::protocol('Malformed provider response.'); } $c=$candidates[0]; $parts=[]; $hasTool=false; foreach ($c['content']['parts'] as $p) { if (!is_array($p)) self::protocol('Malformed provider response.'); if (isset($p['text'])) { if (!is_string($p['text'])) self::protocol('Malformed provider response.'); $parts[]=['type'=>'text','text'=>$p['text']]; } elseif (is_array($p['functionCall']??null)&&is_string($p['functionCall']['name']??null)) { $parts[]=['type'=>'tool_call',...(!empty($p['functionCall']['id'])?['id'=>$p['functionCall']['id']]:[]),'name'=>$p['functionCall']['name'],'arguments'=>is_array($p['functionCall']['args']??null)?$p['functionCall']['args']:[]]; $hasTool=true; } else self::protocol('Malformed provider response.'); } $metadata=[]; if (isset($headers['x-request-id'])) $metadata['requestId']=$this->redact($headers['x-request-id']); if (isset($c['finishReason'])&&is_string($c['finishReason'])) $metadata['finishReason']=$this->redact($c['finishReason']); foreach (['modelVersion','responseId'] as $k) if (isset($v[$k])&&is_string($v[$k])) $metadata[$k]=$this->redact($v[$k]); return new GenerationResponse(isset($v['responseId'])&&is_string($v['responseId'])?$this->redact($v['responseId']):null,isset($v['modelVersion'])&&is_string($v['modelVersion'])?$this->redact($v['modelVersion']):null,$parts,$hasTool?'tool_call':match($c['finishReason']??''){ 'STOP'=>'stop','MAX_TOKENS'=>'length','SAFETY','RECITATION','BLOCKLIST','PROHIBITED_CONTENT','SPII','IMAGE_SAFETY'=>'content_filter',default=>'other'},$this->geminiUsage($v['usageMetadata']??null),$metadata); }
    private function usage(?array $u): ?array { return $this->normalizedUsage($u,['prompt_tokens'=>'input_tokens','completion_tokens'=>'output_tokens','total_tokens'=>'total_tokens']); }
    private function geminiUsage(?array $u): ?array { return $this->normalizedUsage($u,['promptTokenCount'=>'input_tokens','candidatesTokenCount'=>'output_tokens','totalTokenCount'=>'total_tokens']); }
    private function normalizedUsage(?array $u, array $map): ?array { if ($u===null) return null; $out=[]; foreach ($map as $from=>$to) if (array_key_exists($from,$u)) { if (!is_int($u[$from])||$u[$from]<0) self::protocol('Malformed provider usage.'); $out[$to]=$u[$from]; } return $out ?: null; }
    private function parseSse(string $body): array { $events=[]; $text=''; foreach (preg_split('/\R\R+/',trim($body)) as $block) { $data=[]; foreach (preg_split('/\R/',$block) as $line) if (str_starts_with($line,'data:')) $data[]=ltrim(substr($line,5)); if (!$data) continue; $payload=implode("\n",$data); if ($payload==='[DONE]') continue; $v=json_decode($payload,true); if (!is_array($v)) self::protocol('Malformed provider stream.'); if ($this->driver==='gemini') { foreach (($v['candidates'][0]['content']['parts']??[]) as $p) if (isset($p['text'])) $events[]=['type'=>'text_delta','index'=>0,'text'=>$p['text']]; } else { $d=$v['choices'][0]['delta']??[]; if (isset($d['content'])) { $text.=$d['content']; $events[]=['type'=>'text_delta','index'=>0,'text'=>$d['content']]; } } } return [['type'=>'start'],...$events,['type'=>'done', 'response'=>new GenerationResponse(null,null,$text===''?[]:[['type'=>'text','text'=>$text]],'stop')]]; }
    private function parseNdjson(string $body): array { $events=[['type'=>'start']]; $text=''; foreach (preg_split('/\R+/',trim($body)) as $line) { if (!$line) continue; $v=json_decode($line,true); if (!is_array($v)) self::protocol('Malformed provider stream.'); $chunk=$v['message']['content']??$v['response']??''; if ($chunk!=='') {$text.=$chunk;$events[]=['type'=>'text_delta','index'=>0,'text'=>$chunk];} } $events[]=['type'=>'done','response'=>new GenerationResponse(null,null,$text===''?[]:[['type'=>'text','text'=>$text]],'stop')]; return $events; }
    private function providerError(array $r): never { $v=json_decode($r['body'],true); $error=is_array($v['error']??null)?$v['error']:[]; $msg=is_array($v)?($error['message']??$v['message']??null):null; $code=is_string($error['code']??null)?$error['code']:null; $requestId=$r['headers']['x-request-id']??$r['headers']['request-id']??null; throw new ConduitError(match($r['status']){401=>'AuthenticationError',403=>'AuthorizationError',408=>'TimeoutError',429=>'RateLimitError',400,422=>'InvalidRequestError',default=>'ProviderError'}, is_string($msg)?$this->redact($msg):'Provider returned HTTP '.$r['status'].'.', $r['status'], $code === null ? null : $this->redact($code), is_string($requestId)?$this->redact($requestId):null, $this->redactValue($error)); }
    private function redact(string $value): string { foreach (array_unique($this->secrets) as $secret) if ($secret !== '') $value = str_replace($secret, '[REDACTED]', $value); return $value; }
    private function redactValue(mixed $value): mixed { if (is_string($value)) return $this->redact($value); if (is_array($value)) return array_map(fn($v)=>$this->redactValue($v), $value); return $value; }
    private static function validateTimeout(mixed $v): void { if ($v!==null && (!is_int($v)||$v<1||$v>2147483647)) self::invalid('timeout must be an integer from 1 through 2147483647 milliseconds.'); }
    private static function invalid(string $m): never { throw new ConduitError('InvalidRequestError',$m); }
    private static function protocol(string $m): never { throw new ConduitError('ProtocolError',$m); }
}
