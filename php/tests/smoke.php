<?php
declare(strict_types=1);

require __DIR__.'/../src/Errors.php';
require __DIR__.'/../src/Types.php';
require __DIR__.'/../src/CancellationToken.php';
require __DIR__.'/../src/Client.php';
require __DIR__.'/../src/functions.php';

use Conduit\Client;
use Conduit\ConduitError;
use function Conduit\connect;

$client = connect(['provider'=>'muse', 'endpoint'=>'http://127.0.0.1:43127/v1']);
assert($client instanceof Client);
assert(Client::manifest()['providers']['muse']['driver'] === 'openai-compatible');
try { connect(['driver'=>'not-a-driver']); assert(false); } catch (ConduitError $e) { assert($e->category === 'InvalidRequestError'); }
try { connect(['driver'=>'ollama', 'timeout'=>0]); assert(false); } catch (ConduitError $e) { assert($e->category === 'InvalidRequestError'); }
echo "PHP smoke checks passed\n";
