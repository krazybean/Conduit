<?php
declare(strict_types=1);

$case = json_decode(file_get_contents((string) getenv('CONDUIT_CASE_FILE')), true, 512, JSON_THROW_ON_ERROR);
$wire = $case['input']['wire'] ?? $case['input']['response']['wire'] ?? [];
$requestCase = !isset($wire['status']);
$pageToken = $_GET['pageToken'] ?? null;
if (($case['driver'] ?? null) === 'gemini' && ($case['operation'] ?? null) === 'list_models' && $pageToken === 'token-123') {
    $wire['body'] = [
        'models' => [['name' => 'models/gemini-1.5-pro', 'displayName' => 'Pro']],
    ];
}
$wire += ['status'=>200, 'headers'=>[]];
$body = file_get_contents('php://input');
if (getenv('CONDUIT_REQUEST_FILE')) file_put_contents((string) getenv('CONDUIT_REQUEST_FILE'), json_encode(['method'=>$_SERVER['REQUEST_METHOD'],'path'=>$_SERVER['REQUEST_URI'],'body'=>json_decode($body, true)], JSON_THROW_ON_ERROR));
http_response_code((int) ($wire['status'] ?? 200));
foreach (($wire['headers'] ?? []) as $name => $value) header($name.': '.$value);
if (isset($wire['fragments_base64'])) {
    ini_set('output_buffering', '0'); ob_implicit_flush(true); while (ob_get_level() > 0) ob_end_flush();
    foreach ($wire['fragments_base64'] as $fragment) { echo base64_decode($fragment, true); flush(); }
} elseif (array_key_exists('body_text', $wire)) echo $wire['body_text'];
else {
    $body = $wire['body'] ?? ($requestCase ? (str_contains($case['driver'] ?? '', 'anthropic') ? ['id'=>'msg','type'=>'message','role'=>'assistant','model'=>'fixture','content'=>[['type'=>'text','text'=>'ok']],'stop_reason'=>'end_turn','usage'=>['input_tokens'=>1,'output_tokens'=>1]] : (($case['driver'] ?? '') === 'gemini' ? ['candidates'=>[['content'=>['parts'=>[['text'=>'ok']]],'finishReason'=>'STOP']]] : ['id'=>'r','model'=>'fixture','choices'=>[['message'=>['role'=>'assistant','content'=>'ok'],'finish_reason'=>'stop']]])) : []);
    echo json_encode($body, JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
}
