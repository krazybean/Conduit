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
        $headers = ['content-type'=>'application/json']; $secrets = $credential ? [$credential] : [];
        foreach (($config['headers'] ?? []) as $name => $value) {
            if (!is_string($name) || in_array(strtolower($name), self::PROTECTED, true)) self::invalid('Custom header conflicts with a protected header.');
            if (!is_string($value) || preg_match('/[\r\n]/', $name.$value)) self::invalid('Invalid custom HTTP header.');
            $headers[strtolower($name)] = $value; if ($value !== '') $secrets[] = $value;
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
        self::validateTimeout($options['timeout'] ?? null); $url = $this->endpoint.($this->driver === 'ollama' ? '/api/tags' : ($this->driver === 'anthropic' ? '/v1/models' : ($this->driver === 'gemini' ? '/v1beta/models' : '/models')));
        $page = $this->request('GET', $url, null, $options['timeout'] ?? $this->timeout); $value = $this->decodeJson($page['body']);
        $items = $this->driver === 'ollama' ? ($value['models'] ?? null) : ($value['data'] ?? ($value['models'] ?? null));
        if (!is_array($items)) self::protocol('Provider returned malformed model list.');
        return array_map(function($m): ModelInfo { if (!is_array($m)) self::protocol('Provider returned malformed model list.'); $id = $m['id'] ?? $m['name'] ?? $m['model'] ?? null; if (!is_string($id) || trim($id)==='') self::protocol('Provider returned malformed model list.'); return new ModelInfo($id, isset($m['name']) && is_string($m['name']) ? $m['name'] : null, $m); }, $items);
    }
    public function generate(string $model, array|string $request, bool $streaming): GenerationResponse
    {
        $req = self::normalize($request); $body = $this->encode($model, $req, $streaming); $url = $this->url($model, $streaming); $r = $this->request('POST', $url, $body, $req['timeout'] ?? $this->timeout); if ($r['status'] < 200 || $r['status'] >= 300) $this->providerError($r); return $this->decode($this->decodeJson($r['body']), $r['headers']);
    }
    public function stream(string $model, array|string $request): \Generator
    {
        $req = self::normalize($request); $body = $this->encode($model, $req, true); $r = $this->request('POST', $this->url($model, true), $body, $req['timeout'] ?? $this->timeout); if ($r['status'] < 200 || $r['status'] >= 300) $this->providerError($r);
        $events = $this->driver === 'ollama' ? $this->parseNdjson($r['body']) : $this->parseSse($r['body']); foreach ($events as $event) yield $event;
    }
    private static function normalize(array|string $request): array {
        if (is_string($request)) return ['messages'=>[['role'=>'user','content'=>$request]]];
        $aliases=['maxOutputTokens'=>'max_output_tokens','topP'=>'top_p','toolChoice'=>'tool_choice','responseFormat'=>'response_format','providerOptions'=>'provider_options'];
        foreach ($aliases as $from=>$to) if (array_key_exists($from,$request) && !array_key_exists($to,$request)) $request[$to]=$request[$from];
        $allowed=['messages','max_output_tokens','temperature','top_p','stop','tools','tool_choice','response_format','provider_options','timeout','signal'];
        if (array_diff(array_keys($request),$allowed)) self::invalid('Unrecognized generation request field.');
        if (!isset($request['messages']) || !is_array($request['messages']) || !$request['messages']) self::invalid('messages must be a nonempty array.');
        if (isset($request['response_format'])) {
            $format=$request['response_format']; if (!is_array($format) || !in_array($format['type']??null,['text','json','json_schema'],true)) self::invalid('response_format.type must be text, json, or json_schema.');
            if (($format['type']??null)==='json_schema' && !array_key_exists('schema',$format)) self::invalid('response_format.schema is required for json_schema.');
        }
        if (isset($request['timeout'])) self::validateTimeout($request['timeout']);
        if (isset($request['signal']) && is_object($request['signal']) && method_exists($request['signal'],'isCancelled') && $request['signal']->isCancelled()) throw new ConduitError('CancelledError','Request cancelled by caller.');
        return $request;
    }
    private function url(string $model, bool $stream): string { if ($this->driver === 'gemini') return $this->endpoint.'/v1beta/models/'.rawurlencode($model).($stream ? ':streamGenerateContent?alt=sse' : ':generateContent'); return $this->endpoint.($this->driver === 'ollama' ? '/api/chat' : ($this->driver === 'anthropic' ? '/v1/messages' : '/chat/completions')); }
    private function encode(string $model, array $r, bool $stream): string { $msgs = $r['messages']; if ($this->driver==='anthropic' && isset($r['response_format'])) throw new ConduitError('UnsupportedCapabilityError','response_format is not supported for Anthropic.'); $out = match ($this->driver) { 'ollama'=>['model'=>$model,'messages'=>$this->messages($msgs),'stream'=>$stream], 'anthropic'=>['model'=>$model,'messages'=>$this->messages($msgs),'max_tokens'=>$r['max_output_tokens'] ?? 4096,'stream'=>$stream], 'gemini'=>['contents'=>$this->geminiMessages($msgs)], default=>['model'=>$model,'messages'=>$this->messages($msgs),'stream'=>$stream] }; if ($this->driver === 'gemini') { $format=$r['response_format']??null; $out['generationConfig'] = array_filter(['maxOutputTokens'=>$r['max_output_tokens'] ?? null,'temperature'=>$r['temperature'] ?? null,'topP'=>$r['top_p'] ?? null,'stopSequences'=>$r['stop'] ?? null,'responseMimeType'=>$format && ($format['type']??null)!=='text'?'application/json':null,'responseJsonSchema'=>$format['schema']??null]); } else foreach (['max_output_tokens'=>'max_tokens','temperature'=>'temperature','top_p'=>'top_p','stop'=>'stop'] as $from=>$to) if (array_key_exists($from,$r)) $out[$to]=$r[$from]; if (isset($r['response_format']) && ($r['response_format']['type']??null)!=='text') $out['response_format']=($r['response_format']['type']==='json')?['type'=>'json_object']:['type'=>'json_schema','json_schema'=>['name'=>'response','strict'=>true,'schema'=>$r['response_format']['schema']]]; if (!empty($r['tools'])) $out['tools']=$this->tools($r['tools']); if (!empty($r['provider_options'])) $out=array_replace_recursive($r['provider_options'],$out); return self::json($out); }
    private function messages(array $messages): array { return array_map(function($m): array { $role=$m['role']; $content=$m['content']; if (is_string($content)) return ['role'=>$role,'content'=>[['type'=>'text','text'=>$content]]]; return ['role'=>$role,'content'=>array_values(array_filter($content, fn($p)=>($p['type']??'')==='text'))]; }, $messages); }
    private function geminiMessages(array $messages): array { return array_map(fn($m)=>['role'=>$m['role']==='assistant'?'model':'user','parts'=>array_map(fn($p)=>['text'=>$p['text']??''], is_string($m['content']) ? [['text'=>$m['content']]] : $m['content'])], $messages); }
    private function tools(array $tools): array { return $this->driver === 'anthropic' ? array_map(fn($t)=>['name'=>$t['name'],'description'=>$t['description']??null,'input_schema'=>$t['input_schema']??$t['inputSchema']],$tools) : array_map(fn($t)=>['type'=>'function','function'=>['name'=>$t['name'],'description'=>$t['description']??null,'parameters'=>$t['parameters']??$t['input_schema']??$t['inputSchema']]],$tools); }
    private function request(string $method, string $url, ?string $body, ?int $timeout): array { self::validateTimeout($timeout); $context=stream_context_create(['http'=>['method'=>$method,'header'=>$this->headerString(),'content'=>$body??'','ignore_errors'=>true,'timeout'=>($timeout??30000)/1000], 'ssl'=>['verify_peer'=>true,'verify_peer_name'=>true]]); $start=microtime(true); $raw=@file_get_contents($url,false,$context); if ($raw===false) throw new ConduitError($timeout!==null && microtime(true)-$start >= $timeout/1000 ? 'TimeoutError':'ConnectionError','Provider connection failed.'); $headers=[]; $status=200; foreach ($http_response_header??[] as $h) { if (preg_match('/^HTTP\/\S+\s+(\d+)/',$h,$m)) { $status=(int)$m[1]; } elseif (str_contains($h,':')) { [$k,$v]=array_map('trim',explode(':',$h,2)); $headers[strtolower($k)]=$v; } } return ['status'=>$status,'headers'=>$headers,'body'=>$raw]; }
    private function headerString(): string { return implode("\r\n", array_map(fn($k,$v)=>$k.': '.$v,array_keys($this->headers),$this->headers)); }
    private function decodeJson(string $body): array { $v=json_decode($body,true); if (!is_array($v)) self::protocol('Provider returned invalid JSON.'); return $v; }
    private function decode(array $v, array $headers): GenerationResponse { if ($this->driver==='ollama') return new GenerationResponse($v['created_at']??null,$v['model']??null,[['type'=>'text','text'=>$v['message']['content']??$v['response']??'']],($v['done_reason']??'stop')==='stop'?'stop':'other'); if ($this->driver==='anthropic') return $this->decodeAnthropic($v); if ($this->driver==='gemini') return $this->decodeGemini($v); $c=$v['choices'][0]??null; if (!is_array($c)||!is_array($c['message']??null)) self::protocol('Malformed provider response.'); $m=$c['message']; $parts=[]; if (is_string($m['content']??null)) $parts[]=['type'=>'text','text'=>$m['content']]; foreach (($m['tool_calls']??[]) as $tc) $parts[]=['type'=>'tool_call','id'=>$tc['id']??null,'name'=>$tc['function']['name']??'','arguments'=>is_string($tc['function']['arguments']??null)?json_decode($tc['function']['arguments'],true):($tc['function']['arguments']??[])]; return new GenerationResponse($v['id']??null,$v['model']??null,$parts,($m['tool_calls']??[])?'tool_call':match($c['finish_reason']??'other'){ 'stop'=>'stop','length'=>'length','content_filter'=>'content_filter',default=>'other'},$this->usage($v['usage']??null),['request_id'=>$headers['x-request-id']??null]); }
    private function decodeAnthropic(array $v): GenerationResponse { $parts=[]; foreach (($v['content']??[]) as $p) if (($p['type']??'')==='text') $parts[]=['type'=>'text','text'=>$p['text']]; elseif (($p['type']??'')==='tool_use') $parts[]=['type'=>'tool_call','id'=>$p['id']??null,'name'=>$p['name']??'','arguments'=>$p['input']??[]]; return new GenerationResponse(null,null,$parts,($v['stop_reason']??'')==='tool_use'?'tool_call':(($v['stop_reason']??'')==='max_tokens'?'length':'stop'),$v['usage']??null); }
    private function decodeGemini(array $v): GenerationResponse { $parts=[]; foreach (($v['candidates'][0]['content']['parts']??[]) as $p) if (isset($p['text'])) $parts[]=['type'=>'text','text'=>$p['text']]; elseif (isset($p['functionCall'])) $parts[]=['type'=>'tool_call','name'=>$p['functionCall']['name'],'arguments'=>$p['functionCall']['args']??[]]; return new GenerationResponse(null,null,$parts,'stop',$v['usageMetadata']??null); }
    private function usage(?array $u): ?array { return $u ? array_filter(['input_tokens'=>$u['prompt_tokens']??null,'output_tokens'=>$u['completion_tokens']??null,'total_tokens'=>$u['total_tokens']??null],fn($v)=>$v!==null) : null; }
    private function parseSse(string $body): array { $events=[]; $text=''; foreach (preg_split('/\R\R+/',trim($body)) as $block) { $data=[]; foreach (preg_split('/\R/',$block) as $line) if (str_starts_with($line,'data:')) $data[]=ltrim(substr($line,5)); if (!$data) continue; $payload=implode("\n",$data); if ($payload==='[DONE]') continue; $v=json_decode($payload,true); if (!is_array($v)) self::protocol('Malformed provider stream.'); if ($this->driver==='gemini') { foreach (($v['candidates'][0]['content']['parts']??[]) as $p) if (isset($p['text'])) $events[]=['type'=>'text_delta','index'=>0,'text'=>$p['text']]; } else { $d=$v['choices'][0]['delta']??[]; if (isset($d['content'])) { $text.=$d['content']; $events[]=['type'=>'text_delta','index'=>0,'text'=>$d['content']]; } } } return [['type'=>'start'],...$events,['type'=>'done', 'response'=>new GenerationResponse(null,null,$text===''?[]:[['type'=>'text','text'=>$text]],'stop')]]; }
    private function parseNdjson(string $body): array { $events=[['type'=>'start']]; $text=''; foreach (preg_split('/\R+/',trim($body)) as $line) { if (!$line) continue; $v=json_decode($line,true); if (!is_array($v)) self::protocol('Malformed provider stream.'); $chunk=$v['message']['content']??$v['response']??''; if ($chunk!=='') {$text.=$chunk;$events[]=['type'=>'text_delta','index'=>0,'text'=>$chunk];} } $events[]=['type'=>'done','response'=>new GenerationResponse(null,null,$text===''?[]:[['type'=>'text','text'=>$text]],'stop')]; return $events; }
    private function providerError(array $r): never { $v=json_decode($r['body'],true); $msg=is_array($v)?($v['error']['message']??$v['message']??null):null; throw new ConduitError(match($r['status']){401=>'AuthenticationError',403=>'AuthorizationError',408=>'TimeoutError',429=>'RateLimitError',400,422=>'InvalidRequestError',default=>'ProviderError'}, is_string($msg)?$msg:'Provider returned HTTP '.$r['status'].'.', $r['status'], is_array($v['error']??null)?($v['error']['code']??null):null, $r['headers']['x-request-id']??null); }
    private static function json(mixed $v): string { $s=json_encode($v,JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR); return $s; }
    private static function validateTimeout(mixed $v): void { if ($v!==null && (!is_int($v)||$v<1||$v>2147483647)) self::invalid('timeout must be an integer from 1 through 2147483647 milliseconds.'); }
    private static function invalid(string $m): never { throw new ConduitError('InvalidRequestError',$m); }
    private static function protocol(string $m): never { throw new ConduitError('ProtocolError',$m); }
}
