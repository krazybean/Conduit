<?php
declare(strict_types=1);

header('Content-Type: text/event-stream');
header('Cache-Control: no-cache');
ini_set('output_buffering', '0');
ob_implicit_flush(true);
while (ob_get_level() > 0) ob_end_flush();
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }

$first = json_encode(['id'=>'r1','model'=>'m','choices'=>[['index'=>0,'delta'=>['content'=>'first'],'finish_reason'=>null]]], JSON_THROW_ON_ERROR);
$second = json_encode(['choices'=>[['index'=>0,'delta'=>['content'=>' second'],'finish_reason'=>'stop']]], JSON_THROW_ON_ERROR);
echo "data: ".substr($first, 0, 18); flush(); usleep(20000);
echo substr($first, 18)."\n\n"; flush();
usleep(300000);
echo "data: {$second}\n\ndata: [DONE]\n\n"; flush();
