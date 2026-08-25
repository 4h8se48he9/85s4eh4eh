import os
import logging
import re
import hashlib
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, request, render_template, Response
import requests
from scraper import VideoScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = os.urandom(24)

scraper = VideoScraper()

# In-memory cryptographic hash mapping store for secure routing
URL_HASH_STORE = {}

def generate_video_hash(target_url):
    """Generates a secure 16-character SHA-256 hash for masking source media URLs."""
    h = hashlib.sha256(target_url.encode('utf-8')).hexdigest()[:16]
    URL_HASH_STORE[h] = target_url
    return h

def rewrite_playlist(playlist, base_url, provider):
    def build_proxy_uri(raw_uri):
        clean_uri = raw_uri.strip().strip("'\"")
        if clean_uri.startswith("data:"):
            return clean_uri
        resolved = urljoin(base_url, clean_uri)
        # Map through hashed video routing instead of exposing parameters directly
        h_key = generate_video_hash(resolved)
        return f"/video?{h_key}&provider={provider}"

    out_lines = []
    for line in playlist.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if trimmed.startswith('#'):
            def repl(m):
                return f'URI="{build_proxy_uri(m.group(1))}"'
            out_lines.append(re.sub(r'URI="([^"]+)"', repl, trimmed))
        else:
            out_lines.append(build_proxy_uri(trimmed))
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

    # Hash and obscure all direct/HLS streams to match /video?[hashed url] routing format
    provider = data.get("provider", "ph")
    
    if "qualities" in data["streams"]:
        for q in data["streams"]["qualities"]:
            raw_u = q["url"]
            h_key = generate_video_hash(raw_u)
            q["url"] = f"/video?{h_key}&provider={provider}"

    if "direct_mp4" in data["streams"]:
        hashed_mp4 = {}
        for label, raw_u in data["streams"]["direct_mp4"].items():
            h_key = generate_video_hash(raw_u)
            hashed_mp4[label] = f"/video?{h_key}&provider={provider}"
        data["streams"]["direct_mp4"] = hashed_mp4

    return render_template("player.html", data=data)

@app.route("/video", methods=["GET"])
def serve_hashed_video():
    # Anti-YT-DLP & Automation Tool Blocking
    ua = request.headers.get("User-Agent", "").lower()
    blocked_clients = ["yt-dlp", "python-requests", "wget", "curl", "aria2", "ffmpeg", "libwww-perl"]
    if any(client in ua for client in blocked_clients):
        return "Access denied: automated extraction tools are strictly restricted.", 403

    # Extract hash identifier from query string parameters
    query_string = request.args.get("url") # Fallback lookups
    if not query_string:
        # Get raw query string key (e.g. /video?abcdef1234567890 -> abcdef1234567890)
        query_string = request.query_string.decode('utf-8').split('&')[0]

    target = URL_HASH_STORE.get(query_string)
    if not target:
        return "Invalid or expired session hash link.", 404

    provider = request.args.get("provider", "")
    
    # Strict block on direct full-file binary exfiltration via download flags
    if request.args.get("dl") == "1":
        return "Direct content downloads are restricted by security policy.", 403

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
            rewritten = rewrite_playlist(text, final_base, provider)
            res_headers["Content-Type"] = "application/vnd.apple.mpegurl"
            res_headers["Content-Length"] = str(len(rewritten.encode('utf-8')))
            return Response(rewritten, status=upstream.status_code, headers=res_headers)

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=524880): # Obfuscated chunk chunking size
                    if chunk:
                        yield chunk
            except Exception as stream_err:
                logging.warning(f"Stream dropped: {stream_err}")

        return Response(generate(), status=upstream.status_code, headers=res_headers, direct_passthrough=False)

    except Exception as e:
        logging.error(f"Proxy failure for target mapping: {e}")
        return f"Proxy stream failure: {str(e)}", 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)