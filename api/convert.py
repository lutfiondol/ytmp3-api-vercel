from http.server import BaseHTTPRequestHandler
import json
import subprocess
import tempfile
import os
import uuid
import urllib.parse
import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

class handler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        """Health check dan handle GET request dengan parameter URL"""
        from urllib.parse import parse_qs, urlparse
        
        # Parse path dan query
        parsed_path = urlparse(self.path)
        query = parse_qs(parsed_path.query)
        
        # Kalau cuma health check
        if parsed_path.path == '/api/convert' and not query:
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response = {
                'status': 'ok',
                'message': 'YTMP3 API is running',
                'usage': {
                    'post': 'POST /api/convert with JSON body {"url": "youtube_url"}',
                    'get': 'GET /api/convert?url=YOUTUBE_URL',
                    'health': 'GET /api/convert'
                }
            }
            
            self.wfile.write(json.dumps(response).encode())
            return
        
        # Handle GET dengan parameter url
        url = query.get('url', [None])[0]
        if not url:
            self.send_json(400, {'error': 'Parameter url diperlukan'})
            return
        
        # Proses konversi (panggil fungsi yang sama)
        self.handle_conversion(url)
    
    def do_POST(self):
        """Handle POST request dengan JSON body"""
        # Parse path
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path != '/api/convert':
            self.send_error(404, 'Not found')
            return
        
        # Cek method OPTIONS untuk CORS
        if self.headers.get('Access-Control-Request-Method'):
            self.do_OPTIONS()
            return
        
        # Read request body
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            # Parse JSON
            data = json.loads(post_data.decode('utf-8'))
            youtube_url = data.get('url')
            
            if not youtube_url:
                self.send_json(400, {'error': 'URL diperlukan'})
                return
            
            self.handle_conversion(youtube_url)
            
        except json.JSONDecodeError:
            self.send_json(400, {'error': 'Invalid JSON'})
        except Exception as e:
            self.send_json(500, {'error': str(e)})
    
    def do_OPTIONS(self):
        """Handle CORS preflight"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def handle_conversion(self, youtube_url):
        """Fungsi utama konversi YouTube ke MP3"""
        try:
            # Validasi URL YouTube
            if not ('youtube.com' in youtube_url or 'youtu.be' in youtube_url):
                self.send_json(400, {'error': 'Bukan URL YouTube yang valid'})
                return
            
            # Buat temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                # Generate random filename
                file_id = str(uuid.uuid4())[:8]
                output_template = os.path.join(temp_dir, f'%(title)s_{file_id}.%(ext)s')
                
                # Command yt-dlp untuk extract audio
                cmd = [
                    'yt-dlp',
                    '-f', 'bestaudio/best',
                    '--extract-audio',
                    '--audio-format', 'mp3',
                    '--audio-quality', '0',  # Best quality (0 = best)
                    '-o', output_template,
                    '--no-playlist',
                    '--quiet',
                    '--no-warnings',
                    '--progress',
                    '--print', 'after_move:filepath',  # Cetak path file setelah selesai
                    youtube_url
                ]
                
                # Jalankan yt-dlp dengan timeout 120 detik
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120  # Timeout 2 menit
                )
                
                if result.returncode != 0:
                    error_msg = result.stderr[:200] if result.stderr else 'Unknown error'
                    self.send_json(500, {'error': f'Gagal konversi: {error_msg}'})
                    return
                
                # Cari file MP3 yang dihasilkan
                mp3_files = [f for f in os.listdir(temp_dir) if f.endswith('.mp3')]
                if not mp3_files:
                    # Coba cek dari output
                    output_lines = result.stdout.split('\n')
                    for line in output_lines:
                        if line.endswith('.mp3') and os.path.exists(line):
                            mp3_files = [os.path.basename(line)]
                            break
                
                if not mp3_files:
                    self.send_json(500, {'error': 'File MP3 tidak ditemukan setelah konversi'})
                    return
                
                mp3_file = mp3_files[0]
                file_path = os.path.join(temp_dir, mp3_file)
                
                # Baca file sebagai binary
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                
                # Upload ke temporary hosting
                file_url = self.upload_to_temp_host(file_content, mp3_file)
                
                if not file_url:
                    self.send_json(500, {'error': 'Gagal upload file ke hosting'})
                    return
                
                # Extract title dari filename (hapus file_id)
                title = mp3_file.replace(f'_{file_id}.mp3', '').replace('_', ' ')
                
                # Kirim response dengan URL langsung
                self.send_json(200, {
                    'success': True,
                    'title': title,
                    'file_name': mp3_file,
                    'download_url': file_url,
                    'file_size': len(file_content),
                    'format': 'mp3'
                })
                
        except subprocess.TimeoutExpired:
            self.send_json(504, {'error': 'Waktu proses habis (video terlalu panjang)'})
        except Exception as e:
            self.send_json(500, {'error': f'Internal error: {str(e)}'})
    
    def upload_to_temp_host(self, file_content, filename):
        """Upload file ke temporary hosting dan return URL download"""
        
        # Daftar hosting yang dicoba (urut)
        hosts = [
            self.upload_to_tmp_ninja,
            self.upload_to_file_io,
            self.upload_to_gofile,
            self.upload_to_anonfiles
        ]
        
        for upload_func in hosts:
            try:
                url = upload_func(file_content, filename)
                if url:
                    return url
            except Exception as e:
                print(f"Upload ke {upload_func.__name__} gagal: {e}")
                continue
        
        return None
    
    def upload_to_tmp_ninja(self, file_content, filename):
        """Upload ke tmp.ninja (gratis, no limit)"""
        try:
            files = {
                'file': (filename, file_content, 'audio/mpeg')
            }
            
            response = requests.post(
                'https://tmp.ninja/upload.php',
                files=files,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('file'):
                    return f"https://tmp.ninja/{data['file']}"
        except:
            pass
        return None
    
    def upload_to_file_io(self, file_content, filename):
        """Upload ke file.io (gratis, 14 hari)"""
        try:
            files = {
                'file': (filename, file_content, 'audio/mpeg')
            }
            
            response = requests.post(
                'https://file.io',
                files=files,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('link'):
                    return data['link']
        except:
            pass
        return None
    
    def upload_to_gofile(self, file_content, filename):
        """Upload ke gofile.io (gratis, unlimited)"""
        try:
            # Dapatkan server
            server_resp = requests.get('https://api.gofile.io/servers', timeout=10)
            if server_resp.status_code == 200:
                server_data = server_resp.json()
                if server_data.get('status') == 'ok' and server_data['data']['servers']:
                    server = server_data['data']['servers'][0]['name']
                    
                    # Upload file
                    files = {
                        'file': (filename, file_content, 'audio/mpeg')
                    }
                    
                    upload_resp = requests.post(
                        f'https://{server}.gofile.io/uploadFile',
                        files=files,
                        timeout=60
                    )
                    
                    if upload_resp.status_code == 200:
                        upload_data = upload_resp.json()
                        if upload_data.get('status') == 'ok':
                            return upload_data['data']['downloadPage']
        except:
            pass
        return None
    
    def upload_to_anonfiles(self, file_content, filename):
        """Upload ke anonfiles.com"""
        try:
            files = {
                'file': (filename, file_content, 'audio/mpeg')
            }
            
            response = requests.post(
                'https://api.anonfiles.com/upload',
                files=files,
                timeout=60
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status'):
                    return data['data']['file']['url']['full']
        except:
            pass
        return None
    
    def send_json(self, status_code, data):
        """Helper buat send JSON response dengan CORS headers"""
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
