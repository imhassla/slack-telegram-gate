

```
docker build -t slack_telegram_gate .
docker run -d --restart unless-stopped -p 5000:5000  -p 5000:5000 -v $(pwd):/app slack_telegram_gate
```
