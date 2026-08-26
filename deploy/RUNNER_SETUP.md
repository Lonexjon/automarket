# Разовая настройка автодеплоя (GitHub Actions self-hosted runner)

Делается один раз на VPS. После этого `git push` в `main` сам обновляет
код на сервере и перезапускает сайт — без ручного `git pull` в консоли.

## 1. Runner

Токен для команды ниже — со страницы
`https://github.com/Lonexjon/automarket/settings/actions/runners/new`
(Linux/x64), он одноразовый и живёт недолго, генерировать заново при
следующей настройке.

```
cd ~
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64.tar.gz -L https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64.tar.gz
tar xzf actions-runner-linux-x64.tar.gz
./config.sh --url https://github.com/Lonexjon/automarket --token <ТОКЕН_С_САЙТА>
```

На вопросы конфигуратора можно отвечать Enter (значения по умолчанию).

## 2. Runner как systemd-сервис (постоянно в фоне)

```
cd ~/actions-runner
./svc.sh install
./svc.sh start
```

## 3. Сервис самого API/сайта

```
cp ~/automarket/deploy/automarket-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable automarket-api
systemctl start automarket-api
```

## Проверка

```
systemctl status automarket-api --no-pager
curl -s http://localhost:8000/v1/listings?page_size=1
```

Дальше просто `git push` из моей стороны — GitHub Actions сам подхватит
изменения (см. `.github/workflows/deploy.yml`).
