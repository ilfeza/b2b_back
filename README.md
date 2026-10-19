# Handmade Marketplace Backend

Backend маркетплейса на Python 3.12 и FastAPI. В репозитории три сервиса: B2B (кабинет продавца), Goods (товары) и Publications (публикации). Сервисы используют PostgreSQL, MinIO для изображений и Kafka для событий.

## Запуск

Нужны Docker и Docker Compose.

```sh
cp .env.example .env
docker compose up --build -d
```

После запуска доступны:

- [B2B API](http://localhost:8080/docs)
- [Goods API](http://localhost:8081/docs)
- [Publications API](http://localhost:8082/docs)

Полный список эндпоинтов: [документация API](docs/API.md).

Для добавления демонстрационных данных:

```sh
docker compose exec b2b python -m scripts.seed --base-url http://localhost:8000
```

В локальном окружении B2B принимает заголовок `X-User-Id: 11111111-1111-4111-8111-111111111111`. Внешние сервисы Users, Orders, Payment и Analytics работают через тестовые адаптеры.

## Тесты

```sh
docker compose --profile test run --build --rm tests
```
