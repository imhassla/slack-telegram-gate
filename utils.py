import os
import yaml
import time
import logging
import sqlite3
import requests
import threading
from queue import Queue
from threading import Event
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

query_queue = Queue()
queue_processed_event = Event()
queue_processed_event.set()  
config_last_loaded_time = 0  

def worker():
    global query_queue, queue_processed_event
    while True:
        time.sleep(3)  
        if not query_queue.empty():
            queue_processed_event.clear()  
            while not query_queue.empty():
                try:
                    query, params = query_queue.get(timeout=1)
                    cursor.execute(query, params)
                    conn.commit()
                    logging.debug(f"The request was completed with parameters: {params}")
                    query_queue.task_done()
                    
                except sqlite3.Error as e:
                    logging.error(f"Error executing request: {e}")
            queue_processed_event.set()  

threading.Thread(target=worker, daemon=True).start()
lock = threading.Lock()

conn = sqlite3.connect('messages.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS message_threads (
    telegram_message_id INTEGER,
    slack_thread_ts TEXT,
    project_name TEXT,
    PRIMARY KEY (telegram_message_id, project_name)
)
''')
conn.commit()
conn.close

# Reply to slack from telegram
def process_reply_message(message, project):
    thread_ts = None
    if message.reply_to_message:
        logging.debug(f"The message is a reply to message_id={message.reply_to_message.message_id}")
        thread_ts = get_thread_ts_from_slack(message.reply_to_message.message_id, project['project_name'])
        if thread_ts:
            logging.debug(f"thread_ts found for message_id={message.reply_to_message.message_id}: {thread_ts}")
        else:
            logging.warning(f"thread_ts not found for message with message_id={message.reply_to_message.message_id}")
    return thread_ts

# Search for a project by Slack channel
def find_project_by_slack_channel(channel_id):
    logging.debug(f"Search for a project for Slack channel_id={channel_id}")
    for project in current_config['channels']:
        if project['slack_channel_id'] == channel_id:
            logging.debug(f"Project found: {project['project_name']}")
            return project
    logging.warning(f"Project not found for Slack channel_id={channel_id}")
    return None

# Search for a project by Telegram chat_id
def find_project_by_chat_id(chat_id):
    logging.debug(f"Search for a project for chat_id={chat_id}")
    for project in current_config['channels']:
        if project['telegram_chat_id'] == str(chat_id):
            logging.debug(f"Project found: {project['project_name']}")
            return project
    logging.warning(f"Project not found for chat_id={chat_id}")
    return None

# Getting thread_ts from the database by message_id
def get_thread_ts_from_slack(telegram_message_id, project_name):
    cursor.execute('SELECT slack_thread_ts FROM message_threads WHERE telegram_message_id = ? AND project_name = ?', (telegram_message_id, project_name))
    result = cursor.fetchone()
    if result:
        return result[0]  
    return None

# Getting Slack bot member ID
def get_slack_bot_member_id(slack_bot_token):
    try:
        slack_client = WebClient(token=slack_bot_token)
        response = slack_client.auth_test()  
        slack_bot_member_id = response['user_id']
        return slack_bot_member_id
    except SlackApiError as e:
        logging.error(f"Error running auth.test: {e.response['error']}")
        return None

# Start a background thread to monitor configuration changes
def start_config_monitor(interval=60):
    def monitor():
        while True:
            check_and_reload_config()
            time.sleep(interval)
    monitor_thread = threading.Thread(target=monitor)
    monitor_thread.start()
    logging.debug(f"Config change monitoring started")
    return monitor_thread

def check_and_reload_config():
    global config_last_loaded_time
    config_path = 'config.yaml'
    try:
        last_modified_time = os.path.getmtime(config_path)
        # If the configuration has changed (by the time the file was changed)
        if last_modified_time > config_last_loaded_time:
            load_config()
            # Update last download time
            config_last_loaded_time = last_modified_time
    except Exception as e:
        logging.error(f"Error checking configuration: {str(e)}")

# Function for loading configuration
def load_config():
    global current_config, config_last_loaded_time
    try:
        with open('config.yaml', 'r') as f:
            new_config = yaml.safe_load(f)
            current_config = new_config
            config_last_loaded_time = time.time()
            logging.debug("Configuration file updated.")
            # Get and save slack_bot_member_id for each project
            for project in current_config['channels']:
                slack_token = project['slack_bot_token']
                project['slack_bot_member_id'] = get_slack_bot_member_id(slack_token)
            
            return current_config
    except Exception as e:
        logging.error(f"Error loading configuration: {str(e)}")

# Function to get external IP address
def get_server_ip():
    while True:
        try:
            ip_url = 'https://httpbin.org/ip'
            response = requests.get(ip_url)
            data = response.json()
            sip = data.get('origin')
            return sip
        except:
            time.sleep(5)

# Function for downloading files from Slack
def download_file_from_slack(file_url, slack_token):
    headers = {
        'Authorization': f'Bearer {slack_token}'
    }   
    
    try:
        file_response = requests.get(file_url, headers=headers, stream=True, timeout=60)
        
        if file_response.status_code == 200:
            local_filename = file_url.split("/")[-1]
            local_filepath = f'/tmp/{local_filename}'

            logging.debug(f"Uploading a file from Slack. Expected content type: {file_response.headers.get('Content-Type')}")

            with open(local_filepath, 'wb') as f:
                for chunk in file_response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)

            actual_file_size = os.path.getsize(local_filepath)
            logging.debug(f"File {local_filename} successfully downloaded from Slack. Path: {local_filepath}, actual size: {actual_file_size}.")

            return local_filepath
        else:
            logging.error(f"Error uploading file from Slack: {file_url}, status: {file_response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error uploading file from Slack: {str(e)}")
        return None

def update_slack_thread_ts_by_string(event, project):
    logging.debug(f"Processing message for project_name={project['project_name']}, event_ts={event.get('event_ts')}, slack_thread_ts={event.get('slack_thread_ts')}")
    time.sleep(4)
    queue_processed_event.wait() 
    try:
        with lock:
            if 'files' in event and len(event['files']) > 0:
                file_info = event['files'][0]
                timestamp = str(file_info.get('id', ''))
            else:
                logging.debug("Could not find file or timestamp in this 'event''")
                return

            event_ts = str(event.get('event_ts', ''))
            if not event_ts:
                logging.debug("Could not find event_ts in event")
                return

            project_name = project['project_name']

            logging.debug(f"timestamp: {type(timestamp)}: {timestamp}")
            logging.debug(f"event_ts: {type(event_ts)}: {event_ts}")
            logging.debug(f"project_name: {type(project_name)}: {project_name}")

            # Wait for all requests in the queue to be processed
            queue_processed_event.wait() 
            
            # Search for an entry with timestamp and project_name in the database
            cursor.execute('''
            SELECT telegram_message_id FROM message_threads 
            WHERE slack_thread_ts = ? AND project_name = ?
            ''', (timestamp, project_name))
            
            result = cursor.fetchone()

            if result:
                telegram_message_id = result[0]
                logging.debug(f"Found telegram_message_id: {telegram_message_id}")
                save_thread_ts(telegram_message_id, event_ts, project_name)
            else:
                logging.debug(f"Entry with slack_thread_ts={timestamp} for project {project_name} was not found in the database.")
                       
    except Exception as e:
        logging.error(f"Error updating message with slack_thread_ts={event.get('slack_thread_ts')}: {str(e)}")

def save_thread_ts(telegram_message_id, slack_thread_ts, project_name):
    try:
        # Add an update request to the queue
        query_queue.put((
            '''
            INSERT OR REPLACE INTO message_threads (telegram_message_id, slack_thread_ts, project_name)
            VALUES (?, ?, ?)
            ''', (telegram_message_id, slack_thread_ts, project_name)
        ))
    except ValueError as ve:
        logging.error(f"Error while converting data: {ve}")

def get_slack_username(slack_client, slack_user_id):
    try:
        # Get user information via Slack API
        user_info = slack_client.users_info(user=slack_user_id)
        # Username or real name
        slack_username = user_info['user'].get('real_name') or user_info['user'].get('name')
        return slack_username
    except SlackApiError as e:
        logging.error(f"Error getting Slack user information: {e.response['error']}")
        return "Unknown User"
    
# Function for getting message_id from the database by thread_ts and project_name
def get_telegram_message_id_by_thread_ts(thread_ts, project_name):
    cursor.execute('SELECT telegram_message_id FROM message_threads WHERE slack_thread_ts = ? AND project_name = ?', (thread_ts, project_name))
    result = cursor.fetchone()
    if result:
        return result[0]
    return None

def process_reply_to_message(event, project):
    thread_ts = event.get('thread_ts') or event.get('ts')
    reply_to_message_id = None
    if 'thread_ts' in event:
        reply_to_message_id = get_telegram_message_id_by_thread_ts(event['thread_ts'], project['project_name'])
    return reply_to_message_id, thread_ts
