<?php
declare(strict_types=1);

namespace Conduit;

function connect(array $config = []): Client|Model
{
    return Client::connect($config);
}
