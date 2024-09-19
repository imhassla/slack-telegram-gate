from utils import download_file_from_slack, process_reply_message, find_project_by_chat_id, get_slack_username, find_project_by_slack_channel
from utils import get_server_ip, start_config_monitor, process_reply_to_message, update_slack_thread_ts_by_string, save_thread_ts, load_config
from flask import Flask, request, jsonify
from slack_sdk import WebClient
import subprocess
import threading
import telebot
import logging
import sqlite3
import requests
import signal
import sys
import os

# Setting up logging
logging.basicConfig(
    filename='integration.log', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

current_config = load_config()
conn = sqlite3.connect('messages.db', check_same_thread=False)
cursor = conn.cursor()

# Setting up a Telegram bot
telegram_bot = telebot.TeleBot(current_config['settings']['telegram_bot_gate_token'])
logging.debug("Telegram bot configured")

def send_text_to_slack(message, slack_client, project, sender_name, telegram_username):
    thread_ts = process_reply_message(message, project)

    slack_message_text = f"{sender_name} \n{telegram_username}\n\n{message.text}"
    slack_response = slack_client.chat_postMessage(
        channel=project['slack_channel_id'],
        text=slack_message_text,
        thread_ts=thread_ts  
    )
    logging.debug(f"Text message sent to Slack for a project {project['project_name']} with ts={slack_response['ts']}")

    # Maintain correspondence between Telegram message_id and Slack ts
    save_thread_ts(message.message_id,  slack_response['ts'], project['project_name'])
    logging.debug(f"Matching message_id {message.message_id} and ts {slack_response['ts']} saved in database.")

def send_media_to_slack(message, slack_client, project, sender_name, telegram_username):
    thread_ts = process_reply_message(message, project)

    # Get the correct file_id depending on the media message type
    content_types = {
        'photo': lambda msg: msg.photo[-1].file_id,
        'document': lambda msg: msg.document.file_id,
        'audio': lambda msg: msg.audio.file_id,
        'video': lambda msg: msg.video.file_id,
        'animation': lambda msg: msg.animation.file_id,
        'voice': lambda msg: msg.voice.file_id 
    }

    file_id = content_types.get(message.content_type, lambda _: None)(message)
    if not file_id:
        logging.error(f"Unknown content type: {message.content_type}")
        return

    file_info = telegram_bot.get_file(file_id)
    file_url = f'https://api.telegram.org/file/bot{current_config["settings"]["telegram_bot_gate_token"]}/{file_info.file_path}'
    filename = file_info.file_path.split('/')[-1]

    file_response = requests.get(file_url)
    if file_response.status_code == 200:
        logging.debug(f"The file was successfully downloaded from Telegram: {file_url}")
        slack_message_text = f"{sender_name} \n{telegram_username}"

        slack_response = slack_client.files_upload_v2(
            channel=project['slack_channel_id'],
            file=file_response.content,
            filename=filename,
            title=message.caption or "Media from Telegram",
            initial_comment=slack_message_text,
            thread_ts=thread_ts
        )

        if slack_response["ok"]:
            file_timestamp = slack_response['file']['id']
            logging.debug(f"Media file sent to Slack for the project {project['project_name']} with ts={file_timestamp}")
            save_thread_ts(message.message_id, file_timestamp, project['project_name'])
        else:
            logging.error(f"Error sending file to Slack: {slack_response['error']}")
    else:
        logging.error(f"Error downloading file from Telegram: status {file_response.status_code}, URL: {file_url}")

# Slack Events API handler
def process_slack_event(event):
    if 'type' in event and event['type'] == 'message':
        logging.debug(f"Handling a Slack event: event_type={event['type']}")
        handle_slack_message(event)
    else:
        logging.debug(f"Skipping an irrelevant Slack event: {event['type']}")

# Function for processing messages from Slack and forwarding them to Telegram
def handle_slack_message(event):
    logging.debug(f"Received message from Slack: channel_id={event['channel']}")
    project = find_project_by_slack_channel(event['channel'])

    update_slack_thread_ts_by_string(event, project)

    if project:
        slack_bot_member_id = project.get('slack_bot_member_id')
        if event.get('user') == slack_bot_member_id:
            logging.debug(f"Message sent by Slack bot {slack_bot_member_id}, skip forwarding.")
            return

    if project and project['active']:
        slack_token = project['slack_bot_token']
        slack_client = WebClient(token=slack_token)

        slack_user_id = event.get('user')
        slack_username = get_slack_username(slack_client, slack_user_id)
        slack_user_id_tag = f"<@{slack_user_id}>"
        
        try:
            reply_to_message_id, thread_ts = process_reply_to_message(event, project)

            if 'files' in event:
                for file in event['files']:
                    telegram_response = send_file_to_telegram(
                        file, slack_token, slack_username, slack_user_id_tag,
                        event, project, reply_to_message_id
                    )
                    if telegram_response:
                        save_thread_ts(telegram_response.message_id, thread_ts, project['project_name'])
            else:
                telegram_response = send_text_to_telegram(event, slack_username, slack_user_id_tag, project, reply_to_message_id)
                save_thread_ts(telegram_response.message_id, thread_ts, project['project_name'])
                return
          
        except Exception as e:
            logging.error(f"Error when sending a message to Telegram for a project {project['project_name']}: {str(e)}")
    else:
        logging.warning(f"Project not found or not active for Slack channel_id={event['channel']}")

# Processing messages in Telegram (text, photos, documents, audio, video, animations, voice messages)
@telegram_bot.message_handler(content_types=['text','photo', 'document', 'audio', 'video', 'animation', 'voice'])
def handle_media_message(message):
    logging.debug(f"Processing a message from Telegram: chat_id={message.chat.id}, message_id={message.message_id}, type: {message.content_type}")
    handle_telegram_message(message)

def handle_telegram_message(message):
    logging.debug(f"Received message from Telegram: chat_id={message.chat.id}, content_type={message.content_type}, message_id={message.message_id}")
    project = find_project_by_chat_id(message.chat.id)
    
    if project and project['active']:
        slack_token = project['slack_bot_token']
        slack_client = WebClient(token=slack_token)
        sender_name = message.from_user.full_name if message.from_user else "Unknown"
        telegram_username = f"@{message.from_user.username}" if message.from_user.username else ""

        try:
            if message.content_type == 'text':
                send_text_to_slack(message, slack_client, project, sender_name, telegram_username)
            elif message.content_type in ['photo', 'document', 'audio', 'video', 'animation', 'voice']:
                send_media_to_slack(message, slack_client, project, sender_name, telegram_username)
            else:
                logging.error(f"Content type error: {message.content_type}")
        except Exception as e:
            logging.error(f"Error when processing a message from Telegram: {str(e)}")

def send_file_to_telegram(file, slack_token, slack_username, slack_user_id_tag, event, project, reply_to_message_id):
    file_url = file['url_private']
    local_file = download_file_from_slack(file_url, slack_token)
    if local_file:
        telegram_message = f"{slack_username} \n{slack_user_id_tag}"
        if 'text' in event and event['text']:
            telegram_message += f"\n\n{event['text']}"
        
        try:
            with open(local_file, 'rb') as f:
                telegram_response = telegram_bot.send_document(
                    project['telegram_chat_id'],
                    f,
                    caption=telegram_message,
                    reply_to_message_id=reply_to_message_id
                )
            logging.debug(f"The file was sent to Telegram for the project {project['project_name']}, message_id={telegram_response.message_id}")
        except Exception as e:
            logging.error(f"Error when sending a file to Telegram: {str(e)}")
        finally:
            os.remove(local_file)  
        return telegram_response
    else:
        logging.error("The file was not uploaded from Slack.")
    return None

def send_text_to_telegram(event, slack_username, slack_user_id_tag, project, reply_to_message_id):
    telegram_message = f"{slack_username} \n{slack_user_id_tag}\n\n{event['text']}"
    telegram_response = telegram_bot.send_message(
        project['telegram_chat_id'],
        telegram_message,
        reply_to_message_id=reply_to_message_id
    )
    logging.debug(f"Message sent to Telegram for the project {project['project_name']}, message_id={telegram_response.message_id}")
    return telegram_response

# Telegram bot in a separate thread
def run_telegram_bot():
    try:
        logging.debug("Launching a Telegram bot survey")
        while not stop_event.is_set():
            telegram_bot.polling(none_stop=True, timeout=5) 
    except Exception as e:
        logging.critical(f"Critical error in Telegram bot: {str(e)}")
    finally:
        logging.info("Telegram bot stopped.")

# Function to terminate the program correctly
def signal_handler(sig, frame):
    logging.info('Received SIGINT, shutting down...')
    stop_event.set()  
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

app = Flask(__name__)
# Route to receive events from Slack
@app.route('/slack/events', methods=['POST'])
def slack_event_handler():
    data = request.json
    
    # Process challenge from Slack (during initial setup)
    if 'challenge' in data:
        return jsonify({'challenge': data['challenge']})
    
    # Immediately return the Slack response
    if 'event' in data:
        event_data = data['event']
        threading.Thread(target=process_slack_event, args=(event_data,)).start()

    return '', 200  

if __name__ == '__main__':
    # Event to stop threads
    stop_event = threading.Event()
    server_ip = get_server_ip()
    print(f'http://{server_ip}:5555/slack/events')
    logging.info(f"The server is running on {server_ip}. Use the URL for Slack Event Subscriptions: \nhttp://{server_ip}:5555/slack/events")

    # Run Gunicorn in a separate process
    gunicorn_command = ["gunicorn", "-w", "4", "-b", "0.0.0.0:5555", "main:app"]
    gunicorn_process = subprocess.Popen(gunicorn_command)

    # Run configuration monitoring in a separate thread
    monitor_thread = start_config_monitor(interval=60)

    # Run Telegram bot in a separate thread
    telegram_thread = threading.Thread(target=run_telegram_bot)
    telegram_thread.start()

    # Wait for all threads to complete
    telegram_thread.join()
    monitor_thread.join()

    # Terminate the Gunicorn process
    gunicorn_process.terminate()
    gunicorn_process.wait()