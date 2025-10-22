# Настройка API ключей

Приложение ищет секреты в переменных окружения и вспомогательных файлах, используя следующий порядок:

1. Значения, выставленные в окружении процесса (`export BLOCKCHAIN_COM_API_KEY=...`).
2. Файл `api_keys.env` или `.env`, расположенный рядом с `archeblow_desktop.py` либо в текущей рабочей директории. Вы можете указать другой путь через переменную окружения `ARCHEBLOW_API_KEYS_FILE`.
3. Вшитые значения по умолчанию (если заданы для конкретного сервиса).

## Пример содержимого `api_keys.env`

```
BLOCKCHAIN_COM_API_KEY=ваш_ключ
BLOCKCYPHER_API_KEY=...
ETHERSCAN_API_KEY=...
TRONGRID_API_KEY=...
POLYGONSCAN_API_KEY=...
BLOCKCHAIR_API_KEY=...
CHAINZ_API_KEY=...
COINGECKO_API_KEY=...
OFAC_API_KEY=N/A
HEURISTIC_MIXER_TOKEN=N/A
ARCHEBLOW_AI_ANALYST=N/A
ARCHEBLOW_MONITORING_WEBHOOK=https://hooks.example/api
```

После изменения файла перезапустите приложение, чтобы новые значения подхватились.

## Webhook мониторинга

Переменная `ARCHEBLOW_MONITORING_WEBHOOK` позволяет указать URL, на который будут отправляться уведомления об ошибках публичных API. Формат полезной нагрузки — JSON c полями `timestamp`, `level`, `source`, `message` и `details`. Если переменная не задана, события остаются внутри приложения и доступны в дашборде и деталях анализа.
