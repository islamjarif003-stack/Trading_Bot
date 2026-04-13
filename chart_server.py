#!/usr/bin/env python3
"""
Trade Chart Server — Serves the Trade Chart Viewer + live_state JSON files.
Runs on port 8085 alongside the existing dashboard.
"""
import os
import json
import http.server
import socketserver

PORT = 8085
BOT_DIR = os.path.dirname(os.path.abspath(__file__))

class ChartHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BOT_DIR, **kwargs)
    
    def do_GET(self):
        # API endpoint: /api/state/<SYMBOL>
        if self.path.startswith('/api/state/'):
            symbol = self.path.split('/')[-1]
            state_file = os.path.join(BOT_DIR, f'live_state_{symbol}.json')
            if os.path.exists(state_file):
                try:
                    with open(state_file, 'r') as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    self.wfile.write(data.encode())
                except Exception as e:
                    self.send_error(500, str(e))
            else:
                self.send_error(404, f'State file not found for {symbol}')
            return
        
        # API endpoint: /api/symbols — list all symbols with state files
        if self.path == '/api/symbols':
            symbols = []
            for f in os.listdir(BOT_DIR):
                if f.startswith('live_state_') and f.endswith('.json'):
                    sym = f.replace('live_state_', '').replace('.json', '')
                    state_file = os.path.join(BOT_DIR, f)
                    try:
                        with open(state_file) as sf:
                            d = json.load(sf)
                            has_pos = d.get('position', {}).get('active', False)
                            symbols.append({'symbol': sym, 'has_position': has_pos})
                    except:
                        symbols.append({'symbol': sym, 'has_position': False})
            
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(symbols).encode())
            return
        
        # Serve trade_chart.html as default page
        if self.path == '/' or self.path == '/index.html':
            self.path = '/trade_chart.html'
        
        # Add CORS headers
        super().do_GET()
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logs

if __name__ == '__main__':
    with socketserver.TCPServer(("0.0.0.0", PORT), ChartHandler) as httpd:
        print(f"📊 Trade Chart Server running on http://0.0.0.0:{PORT}")
        print(f"   Open http://YOUR_VPS_IP:{PORT} in browser")
        httpd.serve_forever()
