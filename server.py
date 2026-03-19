#!/usr/bin/env python3.11
"""
Tile szerver: http://localhost:8765/{z}/{x}/{y}.png
Statikus fájlok: http://localhost:8765/
"""
import http.server
import os

PORT = 8765
BASE = os.path.dirname(os.path.abspath(__file__))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def do_GET(self):
        # /z/x/y.png -> tiles/z/x/y.png
        parts = self.path.lstrip('/').split('/')
        if len(parts) == 3 and parts[2].endswith('.png'):
            tile_path = os.path.join(BASE, 'tiles', *parts)
            if os.path.exists(tile_path):
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                with open(tile_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404)
                return
        super().do_GET()

    def log_message(self, fmt, *args):
        pass  # csendes log

if __name__ == '__main__':
    with http.server.HTTPServer(('', PORT), Handler) as srv:
        print(f'Szerver fut: http://localhost:{PORT}/')
        print(f'Nyisd meg: http://localhost:{PORT}/crowd_map.html')
        print('Leállítás: Ctrl+C')
        srv.serve_forever()
