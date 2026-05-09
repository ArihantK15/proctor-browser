"""Tiny static file server for previewing app/static/*.html in isolation.

Used by the Claude Code preview tooling — not part of the production
container.  Serves the dashboard / register / download HTML files so the
markup and CSS can be inspected without the FastAPI backend running.
"""
import http.server
import os
import socketserver

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # quieter logs
        pass

    def translate_path(self, path):
        # The dashboard references /static/theme.css (FastAPI mount path).
        # In standalone preview, files live directly in this directory, so
        # strip the /static/ prefix before resolving.
        if path.startswith("/static/"):
            path = path[len("/static"):]
        return super().translate_path(path)


if __name__ == "__main__":
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Static preview server on http://127.0.0.1:{PORT}")
        httpd.serve_forever()
