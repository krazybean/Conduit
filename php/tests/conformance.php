<?php
declare(strict_types=1);

require __DIR__.'/../src/Errors.php'; require __DIR__.'/../src/Types.php'; require __DIR__.'/../src/CancellationToken.php'; require __DIR__.'/../src/Client.php'; require __DIR__.'/../src/functions.php';
use Conduit\ConduitError;
use function Conduit\connect;

function fixture(string $path): mixed { return json_decode(file_get_contents(__DIR__.'/../../conformance/'.$path), true, 512, JSON_THROW_ON_ERROR); }
function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }
function canonical(mixed $value): mixed { if (!is_array($value)) return $value; if (array_is_list($value)) return array_map('canonical', $value); $out=[]; foreach ($value as $key=>$item) $out[$key]=canonical($item); ksort($out); return $out; }
function semanticResponse(Conduit\GenerationResponse $r): array { $metadata=[]; if (isset($r->providerMetadata['finishReason'])) $metadata['finish_reason']=$r->providerMetadata['finishReason']; if (isset($r->providerMetadata['requestId'])) $metadata['request_id']=$r->providerMetadata['requestId']; foreach (['modelVersion','responseId'] as $key) if (isset($r->providerMetadata[$key])) $metadata[$key]=$r->providerMetadata[$key]; $out=['content'=>$r->content,'finish_reason'=>$r->finishReason]; if ($r->id!==null) $out['id']=$r->id; if ($r->model!==null) $out['model']=$r->model; if ($r->usage!==null) $out['usage']=$r->usage; if ($metadata) $out['provider_metadata']=$metadata; return $out; }
function semanticEvent(array $e): array { if ($e['type'] !== 'done') return $e; return ['type'=>'done','response'=>semanticResponse($e['response'])]; }
function runCase(array $case, string $kind, int $n): void {
    $tmp = tempnam(sys_get_temp_dir(), 'conduit-case-'); $request = tempnam(sys_get_temp_dir(), 'conduit-request-'); file_put_contents($tmp, json_encode($case, JSON_THROW_ON_ERROR));
    $port = random_int(45000, 49900); $cmd = escapeshellarg(PHP_BINARY).' -S 127.0.0.1:'.$port.' -t '.escapeshellarg(__DIR__).' '.escapeshellarg(__DIR__.'/conformance_router.php');
    $env = array_merge($_ENV, ['CONDUIT_CASE_FILE'=>$tmp,'CONDUIT_REQUEST_FILE'=>$request]); $proc = proc_open($cmd, [1=>['pipe','w'],2=>['pipe','w']], $pipes, __DIR__, $env); check(is_resource($proc), 'fixture server failed to start');
    for ($i=0; $i<50 && !@fsockopen('127.0.0.1',$port); $i++) usleep(10000);
    try {
        $driver=$case['driver']; $client=connect(['driver'=>$driver,'endpoint'=>"http://127.0.0.1:$port/v1",'credentials'=>'fixture-token']);
        if ($kind==='request') { $in=$case['input']['request']; $r=$client->model($case['input']['model'])->generate($in); $wire=json_decode(file_get_contents($request),true); check(canonical($wire['body'])===canonical($case['expected']['wire_request']['body']), $case['id'].' request body mismatch: '.json_encode($wire['body']).' != '.json_encode($case['expected']['wire_request']['body'])); check($r instanceof Conduit\GenerationResponse, $case['id'].' response missing'); }
        elseif ($kind==='response') { try { $r=$client->model('fixture-model')->generate('hello'); check(!isset($case['expected']['error']), $case['id'].' expected an error'); $expected=$case['expected']['response']; $actual=semanticResponse($r); foreach (['id','model','content','finish_reason','usage'] as $key) check(($actual[$key]??null)===($expected[$key]??null), $case['id'].' response '.$key.' mismatch'); } catch (ConduitError $e) { check(isset($case['expected']['error']) && $e->category === $case['expected']['error']['category'], $case['id'].' unexpected '.$e->category); } }
        elseif ($kind==='stream') { try { $events=[]; foreach ($client->model('fixture-model')->stream('hello') as $event) $events[]=semanticEvent($event); check(!isset($case['expected']['error']), $case['id'].' expected an error'); check(canonical($events)===canonical($case['expected']['events']), $case['id'].' stream mismatch: '.json_encode($events).' != '.json_encode($case['expected']['events'])); } catch (ConduitError $e) { check(isset($case['expected']['error']) && $e->category === $case['expected']['error']['category'], $case['id'].' unexpected '.$e->category.': '.$e->getMessage()); } }
        elseif ($kind==='models') { try { $models=$client->listModels(); check(!isset($case['expected']['error']), $case['id'].' expected an error'); $actual=array_map(static fn($model)=>$model->jsonSerialize(), $models); $expected=$case['expected']['models']??$case['expected']??[]; check(canonical($actual)===canonical($expected), $case['id'].' models mismatch: '.json_encode($actual).' != '.json_encode($expected)); } catch (ConduitError $e) { check(isset($case['expected']['error']) && $e->category === $case['expected']['error']['category'], $case['id'].' unexpected '.$e->category.': '.$e->getMessage()); } }
    } finally { proc_terminate($proc); proc_close($proc); @unlink($tmp); @unlink($request); }
}

$groups=[['requests/openai-text.json','request'],['requests/anthropic-text.json','request'],['requests/gemini-text.json','request'],['responses/openai-text.json','response'],['responses/anthropic-text.json','response'],['responses/gemini-text.json','response'],['streams/openai-text.json','stream'],['streams/anthropic-text.json','stream'],['streams/gemini-text.json','stream'],['models/anthropic-models.json','models'],['models/gemini-models.json','models'],['models/ollama-tags.json','models'],['models/openai-models.json','models']];
$passed=0; foreach ($groups as [$file,$kind]) { $cases=fixture($file); if (!is_array($cases) || isset($cases['id'])) $cases=[$cases]; foreach ($cases as $case) { runCase($case,$kind,$passed++); } }
echo "PHP shared conformance checks passed: $passed cases\n";
