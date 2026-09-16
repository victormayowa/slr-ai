"""A local webhook receiver for development: prints each delivery and checks its signature.

    uv run python -m scripts.dev_webhook_receiver --secret <the secret shown when the webhook was created>

Then, with ALLOW_PRIVATE_WEBHOOK_URLS=true in backend/.env (development only), add a webhook for
http://127.0.0.1:9000/hook under Export & Integrations and do something the webhook listens for (for example add a
task). Deliveries are sent by the worker every minute: run `uv run arq workers.ai_worker.WorkerSettings`.
"""

import argparse
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def make_handler(secret: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - the name http.server looks for
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            signature = self.headers.get("X-OmniReview-Signature", "")
            expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else ""
            verdict = "signature OK" if secret and hmac.compare_digest(expected, signature) else "signature NOT checked"
            if secret and verdict != "signature OK":
                verdict = "signature WRONG"
            event = self.headers.get("X-OmniReview-Event", "?")
            print(f"\n{event} (delivery {self.headers.get('X-OmniReview-Delivery', '?')}): {verdict}")
            try:
                print(json.dumps(json.loads(body), indent=2))
            except ValueError:
                print(body[:2000])
            self.send_response(200 if verdict != "signature WRONG" else 401)
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002 - signature defined by http.server
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--secret", default="", help="the webhook's signing secret, to verify deliveries")
    args = parser.parse_args()
    print(f"Listening on http://127.0.0.1:{args.port}/hook (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", args.port), make_handler(args.secret)).serve_forever()


if __name__ == "__main__":
    main()
