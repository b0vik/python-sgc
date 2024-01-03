import sys
import requests
import yt_dlp
import argparse
import subprocess
import re
import scrapetube
import os
import configparser
from pathlib import Path
from datetime import datetime
import base64
from tqdm import tqdm
import time

pbar = None

def get_job_status(job_id):
    headers = {'Authorization': f'Bearer {get_api_key()}'}
    response = requests.post('http://localhost:8080/getJobStatus', json={'jobIdentifier': job_id}, headers=headers)
    return response.json()

def display_progress_bar(job_id):
    global pbar
    while True:
        data = get_job_status(job_id)

        if data.get('progress') is not None and data.get('video_length') is not None:
            video_length = round(data.get('video_length'), 2)
            progress = round(data.get('progress') * video_length, 2)
            if pbar is None:
                pbar = tqdm(total=video_length, bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}{postfix}]")
            pbar.update(progress - pbar.n)
            if pbar.format_dict['rate'] is not None:
                pbar.set_postfix_str(f"{pbar.format_dict['rate']:.2f}x realtime")
        if data.get('jobStatus') == "completed":
            print(f"Job {job_id} completed")
            return

        time.sleep(1)


def get_transcription(media_url, output_filename, get_best_model, get_latest, output_format):
    transcriptions = list_transcriptions(media_url)

    if get_best_model:
        transcription = transcriptions[0]
    elif get_latest:
        transcription = max(transcriptions, key=lambda x: x['completedTime'])
    else:
        transcription = transcriptions[0]  # Default to the first transcription if no option is specified

    decoded_transcription = base64.b64decode(transcription['transcript'], validate=False).decode('utf-8')

    if output_filename == '-':
        print(decoded_transcription)
    else:
        with open(output_filename, 'w') as f:
            f.write(decoded_transcription)

def find_youtube_urls(text):
    url_pattern = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')
    urls = re.findall(url_pattern, text)
    youtube_urls = ['https://www.youtube.com/watch?v=' + url[5] for url in urls]
    return youtube_urls

def get_config_path():
    # Get the user's config directory
    config_dir = os.path.join(Path.home(), '.config', 'sgc')
    os.makedirs(config_dir, exist_ok=True)

    # Return the path to config.yml
    return os.path.join(config_dir, 'config.yml')

def get_api_key():
    config = configparser.ConfigParser()
    try:
        config.read(get_config_path()) # TODO: error handling
        return config['DEFAULT']['api_key']
    except KeyError:
        print("Error: API key not found in config.yml\nhave you created an account with 'sgc account create'?")
        exit(1)
    

def create_account(username):
   url = "http://localhost:8080/createAccount"
   data = {
       'username': username
   }
   response = requests.post(url, json=data)
   response_data = response.json()

   if response.status_code != 200:
       print(f"Error: {response_data['message']}")
       return

   # Save the username and api key to config.yml
   config = configparser.ConfigParser()
   config['DEFAULT'] = {'username': username, 'api_key': response_data['api_key']}
   with open(get_config_path(), 'w') as configfile:
       config.write(configfile)

   print(response_data)

def resolve_url(url):
    print(f"Resolving non-canonical url {url}:")
    ydl_opts = {
        'simulate': True,
        'quiet': True,
        'extract_flat': True,
        'dump_single_json': True,
        'playlist_items': '1',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        print(info_dict['channel_url'])
        return info_dict['channel_url']

def request_transcription(video_url, model, save_filename=None):
    url = "http://localhost:8080/requestUrlTranscription"
    data = {
        'requestedModel': model,
        'jobType': 'public-url',
        'audioUrl': video_url
    }
    headers = {'Authorization': f'Bearer {get_api_key()}'}

    response = requests.post(url, json=data, headers=headers)
    response_data = response.json()

    job_id = response_data.get('job_id')
    previous_job_status = ""
    if job_id:
        while True:
            data = get_job_status(job_id)
            if data.get('jobStatus') != previous_job_status:
                previous_job_status = data.get('jobStatus')
                match data.get('jobStatus'):
                    case 'requested':
                        print(f"Successfully queued under job id {job_id}")
                    case 'assigned':
                        print("Job assigned to worker node #TODO") # TODO: update API server to send worker name back as JSON
                    case 'transcribing':
                        print("Beginning transcript generation")
                        display_progress_bar(job_id)
                        break
            time.sleep(1)
            
    # Retrieve the completed transcription
    response = requests.post('http://localhost:8080/retrieveTranscriptByJobId', json={'jobId': job_id}, headers=headers)
    transcript_data = response.json()
    transcript = base64.b64decode(transcript_data['transcript'], validate=False).decode('utf-8')

    # Save the transcript to a file if the --save option was used
    if save_filename and transcript:
        with open(save_filename, 'w') as f:
            f.write(transcript)

    return transcript

def process_file(file_path, skip_prompt, model): # TODO: clean up this function
    video_urls = []
    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            if line:
                if line.find("youtube.com/@"): # new-style URL; resolve
                    line = resolve_url(line)
                if line.find("/channel/"):
                    channel_id = line.split("/channel/")[1]
                    videos = scrapetube.get_channel(channel_id)
                    for video in videos:
                        individual_video_link = f"https://www.youtube.com/watch?v={video['videoId']}"
                        video_urls.append(individual_video_link)
                        print(individual_video_link)
                elif line.find("youtube.com/watch?v="):
                    print(line)
                    video_urls.append(line)
                elif line.find("youtube.com/playlist?list="):
                    playlist_id = line.split("youtube.com/playlist?list=")[1]
                    videos = scrapetube.get_playlist(playlist_id)
                    for video in videos:
                        individual_video_link = f"https://www.youtube.com/watch?v={video['videoId']}"
                        video_urls.append(individual_video_link)
                        print(individual_video_link)

    if not skip_prompt:
        prompt = input(f"Do you want to transcribe {len(video_urls)} videos for -1 kudos? [Y/n] ")
        if prompt.lower() != 'y':
            return
    for video_url in video_urls:
        request_transcription(video_url, model)

def convert_and_request_transcription(file_path, model, save_filename=None):
    output_file = file_path.rsplit('.', 1)[0] + '.wav'
    subprocess.run(['ffmpeg', '-i', file_path, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-loglevel', 'quiet', output_file]) #  TODO: use pyav rather than shelling out to ffmpeg; this is sketchy cross-platform
    url = "http://localhost:8080/requestFileTranscription"
    with open(output_file, 'rb') as f:
        files = {'file': f}
        data = {
            'requestedModel': model,
            'jobType': 'file'
        }
        headers = {'Authorization': f'Bearer {get_api_key()}'}
        response = requests.post(url, files=files, data=data, headers=headers)

    # Extract the job_id from the response
    response_data = response.json()
    job_id = response_data.get('job_id')
    sha512 = response_data.get('sha512')
    print(response_data)
    # Wait for the job to complete
    previous_job_status = ""
    if job_id:
        while True:
            data = get_job_status(job_id)
            if data.get('jobStatus') != previous_job_status:
                previous_job_status = data.get('jobStatus')
                match data.get('jobStatus'):
                    case 'requested':
                        print(f"Successfully queued under job id {job_id}")
                    case 'assigned':
                        print("Job assigned to worker node #TODO")
                    case 'transcribing':
                        print("Beginning transcript generation")
                        display_progress_bar(job_id)
                        break
            time.sleep(1)

    # Retrieve the completed transcription
    response = requests.post('http://localhost:8080/retrieveTranscriptByJobId', json={'jobId': job_id}, headers=headers)
    transcript_data = response.json()
    transcript = base64.b64decode(transcript_data['transcript'], validate=False).decode('utf-8')

    # Save the transcript to a file if the --save option was used
    if save_filename and transcript:
        with open(save_filename, 'w') as f:
            f.write(transcript)

    return transcript

        
def list_transcriptions(url): #TODO: separate this
    api_url = "http://localhost:8080/retrieveCompletedTranscripts"
    data = {
        'transcriptType': 'public-url',
        'audioUrl': url
    }
    headers = {'Authorization': f'Bearer {get_api_key()}'}
    response = requests.post(api_url, json=data, headers=headers)
    transcriptions = response.json()

    # Sort the transcriptions by model quality
    model_order = ['large-v3', 'large-v2', 'large', 'medium', 'medium.en', 'small', 'small.en', 'base', 'base.en', 'tiny', 'tiny.en']
    transcriptions.sort(key=lambda x: model_order.index(x['requestedModel']))
        
    return transcriptions
        
def print_transcriptions(url):
    transcriptions = list_transcriptions(url)
    
    for transcription in transcriptions:
        print(f"Date: {datetime.fromtimestamp(transcription['completedTime'])}, Model: {transcription['requestedModel']}")
    
        

def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    transcribe_parser = subparsers.add_parser('transcribe', description='Requests transcriptions from the SGC cluster.')
    transcribe_subparsers = transcribe_parser.add_subparsers()

    channel_list_parser = transcribe_subparsers.add_parser('list', description='Transcribe a list of YouTube URLs.\nchannel_list should be text file containing the list of URLs you want to transcribe, separated by newlines.')
    channel_list_parser.add_argument('channel_list', type=str)
    channel_list_parser.add_argument('--skip-prompt', action='store_true')
    channel_list_parser.set_defaults(func=lambda args: process_file(args.channel_list, args.skip_prompt))

    url_parser = transcribe_subparsers.add_parser('url', description='Transcribes audio from a specific URL supported by yt-dlp.')
    url_parser.add_argument('video_url', type=str)
    url_parser.set_defaults(func=lambda args: request_transcription(args.video_url))
    url_parser.add_argument('--save', type=str, help='Save the completed transcript to a file')


    file_parser = transcribe_subparsers.add_parser('file', description='Send a local file to the SGC cluster for transcription.')
    file_parser.add_argument('file_path', type=str)
    file_parser.set_defaults(func=lambda args: convert_and_request_transcription(args.file_path))
    file_parser.add_argument('--save', type=str, help='Save the completed transcript to a file')

    
    channel_list_parser.add_argument('--model', type=str, default='small')
    channel_list_parser.add_argument('--save', type=str, help='Save the completed transcript to a file')
    url_parser.add_argument('--model', type=str, default='small')
    file_parser.add_argument('--model', type=str, default='small')
    
    list_parser = subparsers.add_parser('list', description='List existing transcriptions for a URL.')
    list_subparsers = list_parser.add_subparsers()

    url_list_parser = list_subparsers.add_parser('url', description='List existing transcriptions for a specific YouTube URL.')
    url_list_parser.add_argument('url', type=str)
    url_list_parser.set_defaults(func=lambda args: print_transcriptions(args.url))

    channel_list_parser.set_defaults(func=lambda args: process_file(args.channel_list, args.skip_prompt, args.model))
    url_parser.set_defaults(func=lambda args: request_transcription(args.video_url, args.model))
    file_parser.set_defaults(func=lambda args: convert_and_request_transcription(args.file_path, args.model))

    account_parser = subparsers.add_parser('account', description='Account creation and deletion tools')
    account_subparsers = account_parser.add_subparsers()

    create_parser = account_subparsers.add_parser('create', description='Creates an account.')
    create_parser.add_argument('username', type=str)
    create_parser.set_defaults(func=lambda args: create_account(args.username))
    
        # Add 'get' subcommand
    get_parser = subparsers.add_parser('get', description='Retrieves generated transcriptions from the SGC cluster.')
    get_subparsers = get_parser.add_subparsers()
    
    url_parser.set_defaults(func=lambda args: request_transcription(args.video_url, args.model, args.save))
    file_parser.set_defaults(func=lambda args: convert_and_request_transcription(args.file_path, args.model, args.save))
    channel_list_parser.set_defaults(func=lambda args: process_file(args.channel_list, args.skip_prompt, args.model, args.save))

    # Add 'url' subcommand under 'get'
    url_get_parser = get_subparsers.add_parser('url', description='Gets subtitles for a public video or audio URL.')
    url_get_parser.add_argument('output_filename', type=str)
    url_get_parser.add_argument('media_url', type=str)
    url_get_parser.add_argument('--get-best-model', action='store_true')
    url_get_parser.add_argument('--get-latest', action='store_true')
    url_get_parser.add_argument('--output-format', type=str, choices=['vtt'], default='vtt')
    url_get_parser.set_defaults(func=lambda args: get_transcription(args.media_url, args.output_filename, args.get_best_model, args.get_latest, args.output_format))

    transcribe_subparsers.required = True

    args = parser.parse_args()
    
    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(0)
        
    args.func(args)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Sending job cancellation request") # TODO, big TODO