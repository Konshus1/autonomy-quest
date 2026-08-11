from http.server import BaseHTTPRequestHandler,HTTPServer
import pathlib
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  pathlib.Path("/receipts/network.txt").write_text(self.path); self.send_response(200); self.end_headers(); self.wfile.write(b"NETWORK-OK")
 def log_message(self,*args): pass
HTTPServer(("0.0.0.0",8080),H).handle_request()
