<?php
declare(strict_types=1);

namespace Conduit;

final class GenerationResponse implements \JsonSerializable
{
    public function __construct(
        public readonly ?string $id,
        public readonly ?string $model,
        public readonly array $content,
        public readonly string $finishReason,
        public readonly ?array $usage = null,
        public readonly array $providerMetadata = [],
    ) {}
    public function getText(): string { return implode('', array_map(fn(array $p): string => $p['text'] ?? '', array_filter($this->content, fn($p): bool => ($p['type'] ?? null) === 'text'))); }
    public function getToolCalls(): array { return array_values(array_filter($this->content, fn($p): bool => ($p['type'] ?? null) === 'tool_call')); }
    public function jsonSerialize(): array { return ['id'=>$this->id,'model'=>$this->model,'content'=>$this->content,'finish_reason'=>$this->finishReason,'usage'=>$this->usage,'provider_metadata'=>$this->providerMetadata,'text'=>$this->getText(),'tool_calls'=>$this->getToolCalls()]; }
}

final class ModelInfo implements \JsonSerializable
{
    public function __construct(public readonly string $id, public readonly ?string $name = null, public readonly ?array $providerMetadata = null) {}
    public function jsonSerialize(): array { return ['id'=>$this->id, ...($this->name === null ? [] : ['name'=>$this->name]), ...($this->providerMetadata === null ? [] : ['provider_metadata'=>$this->providerMetadata])]; }
}
