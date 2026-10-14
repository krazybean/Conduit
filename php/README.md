# Conduit PHP

PHP 8.2+ Composer package for the Conduit provider-neutral API. It has no runtime Composer dependencies and uses PHP's native HTTP streams.

```php
use function Conduit\connect;

$model = connect(['provider' => 'openai', 'credentials' => getenv('OPENAI_API_KEY'), 'model' => 'gpt-4o-mini']);
$response = $model->generate('Hello');
echo $response->getText();
```

This package is experimental until it passes the repository's shared conformance suite. Packagist metadata is prepared in `composer.json`; nothing is published by CI.
