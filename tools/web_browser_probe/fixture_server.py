"""Fixed endpoint only: no filesystem browsing, upload, or arbitrary paths."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BODY = (Path(__file__).parent / 'fixtures' / 'challenge.html').read_bytes()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/challenge':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(BODY)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *_):
        pass

if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
