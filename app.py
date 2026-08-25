import os
import logging
import re
import hashlib
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, request, render_template, Response, abort
import requests
from scraper import VideoScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = os.urandom(24)

scraper = VideoScraper()

# Secure In-Memory Token Store for Hashing Stream URLs
STREAM_TOKENS = {}

def create_secure_token(target_url, provider):
    token = hashlib.sha256(f"{target_url}_{os.urandom(16).hex()}".encode()).hexdigest()[:32]
    STREAM_TOKENS[token] = {"url": target_url, "provider": provider}
    # Keep memory footprint safe
    if len(STREAM_TOKENS) > 2000:
        STREAM_TOKENS.pop(next(iter(STREAM_TOKENS)))
    return token

# ==========================================
# ANTI-YT-DLP & AUTOMATED SCRAPER FIREWALL
# ==========================================
@app.before_request
def anti_bot_guard():
    # Allow normal document/asset pages, inspect stream proxy requests
    if request.path.startswith('/stream') or request.path.startswith('/proxy'):
        ua = request.headers.get("User-Agent", "").lower()
        blocked_bots = ["yt-dlp", "youtube-dl", "python-requests", "curl", "wget", "libwww-perl", "axios", "postman", "bot", "crawler"]
        if any(bot in ua for bot in blocked_bots):
            abort(403, description="Automated scraper access prohibited.")

def tokenized_rewrite_playlist(playlist, base_url, provider):
    def build_token_uri(raw_uri):
        clean_uri = raw_uri.strip().strip("'\"")
        if clean_uri.startswith("data:"):
            return clean_uri
        resolved = urljoin(base_url, clean_uri)
        token = create_secure_token(resolved, provider)
        return f"/stream/{token}"

    out_lines = []
    for line in playlist.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith('#'):
            def repl(m):
                return f'URI="{build_token_uri(m.group(1))}"'
            out_lines.append(re.sub(r'URI="([^"]+)"', repl, trimmed))
        else:
            out_lines.append(build_token_uri(trimmed))
    return '\n'.join(out_lines)

@app.route("/", methods=["GET"])
def index():
    return render_template("home.html")

@app.route("/abilities", methods=["GET"])
def abilities():
    return render_template("abilities.html")

@app.route("/view", methods=["GET"])
def view_page():
    return render_template("view.html")

@app.route("/extract", methods=["POST"])
def extract():
    url = request.form.get("url")
    if not url:
        return render_template("view.html", error="Target URL is required.")

    logging.info(f"Extracting: {url}")
    data = scraper.extract(url)

    if not data or data.get("status") == "error":
        return render_template("view.html", error=data.get("error", "Extraction failed."))

    provider = data.get("provider", "pornhub")

    # TOKENIZE ALL STREAM URLS (Hiding raw CDN links entirely)
    if "streams" in data:
        if "qualities" in data["streams"]:
            for q in data["streams"]["qualities"]:
                token = create_secure_token(q["url"], provider)
                q["secure_token"] = token
                q["url"] = f"/stream/{token}" # Overwrite cleartext URL with secure hash path

        if "direct_mp4" in data["streams"]:
            secure_mp4 = {}
            for label, u in data["streams"]["direct_mp4"].items():
                token = create_secure_token(u, provider)
                secure_mp4[label] = f"/stream/{token}"
            data["streams"]["direct_mp4"] = secure_mp4

        if "video_only" in data["streams"]:
            secure_vo = {}
            for label, u in data["streams"]["video_only"].items():
                token = create_secure_token(u, provider)
                secure_vo[label] = f"/stream/{token}"
            data["streams"]["video_only"] = secure_vo

    return render_template("player.html", data=data)

# ==========================================
# SECURE HASHED STREAM ENDPOINT (/stream/[token])
# ==========================================
@app.route("/stream/<token>")
def secure_stream(token):
    stream_info = STREAM_TOKENS.get(token)
    if not stream_info:
        return "Invalid or expired stream token.", 404

    target = stream_info["url"]
    provider = stream_info["provider"]

    parsed_url = urlparse(target)
    netloc = parsed_url.netloc.lower()

    if "pornhub" in provider or "phncdn" in netloc or "pornhub" in netloc:
        referer = "https://www.pornhub.com/"
    elif "xnxx" in provider or "xnxx" in netloc:
        referer = "https://www.xnxx.com/"
    elif "xvideos" in provider or "xvideos" in netloc:
        referer = "https://www.xvideos.com/"
    elif "3movs" in provider or "3movs" in netloc:
        referer = "https://www.3movs.com/"
    else:
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": referer.rstrip('/'),
        "Accept": "*/*",
        "Connection": "keep-alive"
    }

    range_header = request.headers.get("Range")
    if range_header:
        req_headers["Range"] = range_header

    try:
        try:
            upstream = requests.get(target, headers=req_headers, stream=True, allow_redirects=True, timeout=15)
            upstream.raise_for_status()
        except Exception as direct_err:
            logging.warning(f"Direct connection failed, switching to WARP SOCKS5: {direct_err}")
            active_proxies = {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"}
            upstream = requests.get(target, headers=req_headers, proxies=active_proxies, stream=True, allow_redirects=True, timeout=25)
            upstream.raise_for_status()

        if upstream.status_code not in [200, 206]:
            return f"Upstream block: {upstream.status_code}", upstream.status_code

        excluded_headers = ['content-encoding', 'transfer-encoding', 'connection', 'keep-alive']
        res_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded_headers}
        res_headers["Access-Control-Allow-Origin"] = "*"
        res_headers["Access-Control-Allow-Headers"] = "*"
        res_headers["Accept-Ranges"] = "bytes"

        content_type = upstream.headers.get("content-type", "").lower()
        res_headers["Content-Type"] = content_type

        if "Content-Length" in upstream.headers:
            res_headers["Content-Length"] = upstream.headers["Content-Length"]

        is_m3u8 = "mpegurl" in content_type or ".m3u8" in target or "hls_playlist" in target

        if is_m3u8:
            text = upstream.text
            final_base = upstream.url or target
            rewritten = tokenized_rewrite_playlist(text, final_base, provider)
            res_headers["Content-Type"] = "application/vnd.apple.mpegurl"
            res_headers["Content-Length"] = str(len(rewritten.encode('utf-8')))
            return Response(rewritten, status=upstream.status_code, headers=res_headers)

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=1048576):
                    if chunk:
                        yield chunk
            except Exception as stream_err:
                logging.warning(f"Stream dropped: {stream_err}")

        return Response(generate(), status=upstream.status_code, headers=res_headers, direct_passthrough=False)

    except Exception as e:
        logging.error(f"Stream failure for {target}: {e}")
        return f"Stream tunnel failure: {str(e)}", 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)