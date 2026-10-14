<?php
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] === 'GET') { echo json_encode(['data'=>[['id'=>'test-model']]]); exit; }
$body = json_decode(file_get_contents('php://input'), true);
if (($body['stream'] ?? false) === true) {
    header('Content-Type: text/event-stream');
    echo "data: ".json_encode(['id'=>'r1','model'=>'m','choices'=>[['index'=>0,'delta'=>['role'=>'assistant'],'finish_reason'=>null]]])."\n\n";
    echo "data: ".json_encode(['choices'=>[['index'=>0,'delta'=>['content'=>'ok'],'finish_reason'=>null]]])."\n\n";
    echo "data: ".json_encode(['choices'=>[['index'=>0,'delta'=>[],'finish_reason'=>'stop']]])."\n\n";
    echo "data: [DONE]\n\n";
    exit;
}
echo json_encode(['id'=>'r1','model'=>'m','choices'=>[['message'=>['role'=>'assistant','content'=>'ok'],'finish_reason'=>'stop']]]);
