## Preparations

On the telegram side:

1) Create a bot and save its token. [BotFather](https://t.me/BotFather)

2) Add this bot to telegram chats that will be integrated with slack.
   
3) You must have full administrator rights in the corresponding chats, and also delegate administrator rights to the bot.

On the slack side:

1) Create [App](https://api.slack.com/apps)

2) Select `Create app from manifest`, select workspace and set the following yaml:
```
display_information:
  name: Telegram gate
features:
  bot_user:
    display_name: Telegram gate
    always_online: true
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - files:write
      - groups:history
      - groups:read
      - incoming-webhook
      - remote_files:read
      - users:read
      - files:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

3) Go to `OAuth Tokens` and install it to your workspace, allowing permissions.
   
- Now you have the Bot User OAuth Token (starts with xoxb).

- The party connecting from Slack to Telegram transfers the `Channel ID` of the channel that needs to be wired to the Telegram chat and this `Bot User OAuth Token` to the owners of the server of this application.


4) Go to `Event Subscriptions` and switch On `Enable Events`.
   
- Here you will need to specify the URL that the integration server will return after launch.

- The party setting up the integration and owning this application server provides this link to partners for connection.

## Install
With venv environment (python3-full and python3-pip required):

```bash
sudo apt update && sudo apt install git python3-full python3-pip -y
git clone https://github.com/imhassla/slack-telegram-gate
cd slack-telegram-gate
python3 -m venv env
source env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

With docker:
```bash
git clone https://github.com/imhassla/slack-telegram-gate
cd slack-telegram-gate
docker build -t slack_telegram_gate .
docker run -d --restart unless-stopped -p 5555:5555 -v $(pwd):/app slack_telegram_gate
```

## Configs
The application supports working with only one Telegram bot, but with multiple Slack bot tokens in parallel.
It also supports adding one Slack bot to different chats of the same space to connect with the corresponding chats in Telegram.

Each pair of Telegram-Slack channels is indicated by a separate project name in the configuration.
Adding new channels does not require restarting the application. The config is checked for updates once a minute.

The example configuration in "config.yaml" should be modified and filled with the following data:

- telegram bot token 
- telegram chat and slack channel ID's for linking
- slack bot token

## License

This script is distributed under the MIT license. 
