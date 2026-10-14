<?php
declare(strict_types=1);
require __DIR__.'/../src/Errors.php'; require __DIR__.'/../src/Types.php'; require __DIR__.'/../src/Client.php'; require __DIR__.'/../src/functions.php';
use function Conduit\connect;
$client = connect(['driver'=>'openai-compatible','endpoint'=>'http://127.0.0.1:43127/v1']);
$response = $client->model('m')->generate('hi');
assert($response->getText() === 'ok');
$events = iterator_to_array($client->model('m')->stream('hi'));
assert($events[0]['type'] === 'start' && end($events)['type'] === 'done');
assert(end($events)['response']->getText() === 'ok');
assert($client->listModels()[0]->id === 'test-model');
echo "PHP HTTP smoke checks passed\n";
