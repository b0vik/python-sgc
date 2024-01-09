import requests
import base64
from enum import Enum
import hashlib
import subprocess

class TranscriptionSortType(Enum):
    BY_MODEL_QUALITY = 1

class SGClient:
    def __init__(self, api_key=None, base_url='http://localhost:8080'):
        self.base_url = base_url
        if api_key:
            self.api_key = api_key
            self.set_headers_by_api_key()

    def set_headers_by_api_key(self):
        self.headers = {'Authorization': f'Bearer {self.api_key}'}

    def get_job_status(self, job_id):
        response = requests.post(f'{self.base_url}/getJobStatus', json={'jobIdentifier': job_id}, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def request_transcription(self, video_url=None, file=None, model="small"):
        data = None
        if video_url:
            data = {
                'requestedModel': model,
                'jobType': 'public-url',
                'audioUrl': video_url
            }
            response = requests.post(f'{self.base_url}/requestUrlTranscription', json=data, headers=self.headers)
        elif file:
            output_file = file.rsplit('.', 1)[0] + '.wav'
            subprocess.run(['ffmpeg', '-i', file, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-loglevel', 'quiet', output_file])
            with open(output_file, 'rb') as f:
                files = {'file': f}
                data = {
                    'requestedModel': model,
                    'jobType': 'file'
                }
                response = requests.post(f'{self.base_url}/requestUrlTranscription', json=data, headers=self.headers, files=files)
        response.raise_for_status()
        return response.json()

        

    def retrieve_transcript_by_job_id(self, job_id):
        response = requests.post(f'{self.base_url}/retrieveTranscriptByJobId', json={'jobId': job_id}, headers=self.headers)
        response.raise_for_status()
        transcript_data = response.json()
        # transcript = base64.b64decode(transcript_data['transcript'], validate=False).decode('utf-8')
        
        return transcript

    def list_transcriptions(self, url=None, sha512=None, file=None, sort_by=TranscriptionSortType.BY_MODEL_QUALITY):
        data = None
        if url:
            data = {
                'transcriptType': 'public-url',
                'audioUrl': url
            }
        elif sha512:
            data = {
                'transcriptType': 'file',
                'sha512': sha512
            }
        elif file:
            with open(file, 'rb') as f:
                file_hash = hashlib.sha512(f.read()).hexdigest()
            data = {
                'transcriptType': 'file',
                'sha512': file_hash
            }
        else:
            return
        
        response = requests.post(f'{self.base_url}/retrieveCompletedTranscripts', json=data, headers=self.headers)
        response.raise_for_status()
        transcriptions = response.json()
        # Sort the transcriptions by model quality
        # TODO: factor in requested sort type
        model_order = ['large-v3', 'large-v2', 'large', 'medium', 'medium.en', 'small', 'small.en', 'base', 'base.en', 'tiny', 'tiny.en']
        transcriptions.sort(key=lambda x: model_order.index(x['requestedModel']))
        return transcriptions
            
    def create_account(self, username):
        data = {'username': username}
        response = requests.post(f'{self.base_url}/createAccount', json=data)
        response.raise_for_status()
        self.api_key = response.json()['api_key']
        self.set_headers_by_api_key()
        return {'username': username, 'api_key': self.api_key}