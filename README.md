

```
docker build -t slack_telegram_gate .
docker run -d --restart unless-stopped -p 5555:5555 -v $(pwd):/app slack_telegram_gate
```
