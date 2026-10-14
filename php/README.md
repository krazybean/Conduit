# Conduit PHP

PHP 8.2+ Composer package for the Conduit provider-neutral API. It has no runtime Composer dependencies and uses PHP's native HTTP streams.

Install it from Packagist:

```sh
composer require krazybean/conduit
```

```php
use function Conduit\connect;

$model = connect(['provider' => 'openai', 'credentials' => getenv('OPENAI_API_KEY'), 'model' => 'gpt-4o-mini']);
$response = $model->generate('Hello');
echo $response->getText();
```

This repository is an automated PHP-only export of the canonical `krazybean/Conduit/php` directory. Releases are synchronized from Conduit SemVer tags after the source release is authorized.
