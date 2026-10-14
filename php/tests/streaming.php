<?php
declare(strict_types=1);

require __DIR__.'/../src/Errors.php';
require __DIR__.'/../src/Types.php';
require __DIR__.'/../src/CancellationToken.php';
require __DIR__.'/../src/Client.php';
require __DIR__.'/../src/functions.php';

use Conduit\ConduitError;
use Conduit\CancellationToken;
use function Conduit\connect;

$port = (int)(getenv('CONDUIT_TEST_PORT') ?: 43128);
$model = connect(['driver'=>'openai-compatible', 'endpoint'=>"http://127.0.0.1:{$port}"])->model('m');
$stream = $model->stream('hi');
$started = microtime(true);
$event = $stream->current();
assert($event['type'] === 'start');
$stream->next();
assert($stream->current()['type'] === 'text_delta' && $stream->current()['text'] === 'first');
assert(microtime(true) - $started < 0.25, 'first event was buffered until the delayed event');
$stream->next();
assert($stream->current()['text'] === ' second');

$token = new CancellationToken();
$stream = $model->stream(['messages'=>[['role'=>'user','content'=>'hi']], 'signal'=>$token]);
assert($stream->current()['type'] === 'start');
$token->cancel();
try { while (true) $stream->next(); } catch (ConduitError $e) { assert($e->category === 'CancelledError'); }

$stream = $model->stream(['messages'=>[['role'=>'user','content'=>'hi']], 'timeout'=>100]);
assert($stream->current()['type'] === 'start');
try { while (true) $stream->next(); } catch (ConduitError $e) { assert($e->category === 'TimeoutError'); }

echo "PHP incremental streaming checks passed\n";
