import os
import logging
import re
import random
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, request, render_template, Response
import requests
from scraper import VideoScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = os.urandom(24)

DEFAULT_PROXY = os.environ.get("PROXY_URL", "socks5h://127.0.0.1:40000")
scraper = VideoScraper(proxy_url=DEFAULT_PROXY)

def rewrite_playlist(playlist, base_url):
    def build_proxy_uri(raw_uri):
        clean_uri = raw_uri.strip().strip("'\"")
        if clean_uri.startswith("data:"):
            return clean_uri
        resolved = urljoin(base_url, clean_uri)
        return "/proxy?url=" + quote(resolved, safe="")

    out_lines = []
    for line in playlist.splitlines():
        trimmed = line.strip()
        if not trimmed:
            out_lines.append(trimmed)
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

    logging.info(f"Extracting metadata for: {url}")
    data = scraper.extract(url)

    if not data or data.get("status") == "error":
        return render_template("view.html", error=data.get("error", "Failed to extract media."))

    return render_template("player.html", data=data)

@app.route("/proxy")
def proxy_media():
    target = request.args.get("url")
    if not target:
        return "Missing URL", 400

    parsed_url = urlparse(target)

    if "phncdn" in parsed_url.netloc or "pornhub" in parsed_url.netloc:
        referer = "https://www.pornhub.com/"
    elif "xnxx" in parsed_url.netloc:
        referer = "https://www.xnxx.com/"
    elif "xvideos" in parsed_url.netloc:
        referer = "https://www.xvideos.com/"
    elif "googlevideo.com" in parsed_url.netloc or "youtube.com" in parsed_url.netloc:
        referer = "https://www.youtube.com/"
    else:
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": referer.rstrip('/')
    }

    range_header = request.headers.get("Range")
    if range_header:
        req_headers["Range"] = range_header

    active_proxies = scraper.get_request_proxies()

    try:
        try:
            upstream = requests.get(target, headers=req_headers, proxies=active_proxies, stream=True, timeout=20)
            upstream.raise_for_status()
        except requests.exceptions.RequestException:
            logging.warning(f"WARP tunnel bypassed for media segment: {target}")
            upstream = requests.get(target, headers=req_headers, proxies=None, stream=True, timeout=20)

        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        res_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded_headers}
        res_headers["Access-Control-Allow-Origin"] = "*"
        res_headers["Access-Control-Allow-Headers"] = "*"
        res_headers["Accept-Ranges"] = "bytes"

        if "Content-Length" in upstream.headers:
            res_headers["Content-Length"] = upstream.headers["Content-Length"]

        content_type = upstream.headers.get("content-type", "").lower()
        is_m3u8 = (
            "mpegurl" in content_type or 
            ".m3u8" in target or 
            "hls_playlist" in target or
            "#EXTM3U" in (upstream.text[:50] if upstream.text else "")
        )

        if is_m3u8:
            text = upstream.text
            final_base = upstream.url or target
            rewritten = rewrite_playlist(text, final_base)
            res_headers["Content-Type"] = "application/vnd.apple.mpegurl"
            res_headers["Content-Length"] = str(len(rewritten.encode('utf-8')))
            return Response(rewritten, status=upstream.status_code, headers=res_headers)

        def generate():
            for chunk in upstream.iter_content(chunk_size=262144):
                if chunk:
                    yield chunk

        return Response(generate(), status=upstream.status_code, headers=res_headers, direct_passthrough=True)

    except Exception as e:
        logging.error(f"Edge proxy failure for {target}: {e}")
        return "Edge proxy failure", 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)