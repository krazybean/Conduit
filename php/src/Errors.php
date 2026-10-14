<?php
declare(strict_types=1);

namespace Conduit;

class ConduitError extends \RuntimeException
{
    public function __construct(
        public readonly string $category,
        string $message,
        public readonly ?int $statusCode = null,
        public readonly ?string $providerCode = null,
        public readonly ?string $requestId = null,
        public readonly ?array $providerDetails = null,
        public readonly ?array $cause = null,
    ) { parent::__construct($message); }
}
