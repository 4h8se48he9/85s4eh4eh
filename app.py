import os
import logging
import re
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, request, render_template, Response
import requests
from scraper import VideoScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = os.urandom(24)

scraper = VideoScraper()

def rewrite_playlist(playlist, base_url):
    def build_proxy_uri(raw_uri):
        clean_uri = raw_uri.strip().strip("'\"")
        if clean_uri.startswith("data:"): return clean_uri
        resolved = urljoin(base_url, clean_uri)
        return "/proxy?url=" + quote(resolved, safe="")

    out_lines = []
    for line in playlist.splitlines():
        trimmed = line.strip()
        if not trimmed: continue
        if trimmed.startswith('#'):
            def repl(m): return f'URI="{build_proxy_uri(m.group(1))}"'
            out_lines.append(re.sub(r'URI="([^"]+)"', repl, trimmed))
        else:
            out_lines.append(build_proxy_uri(trimmed))
    return '\n'.join(out_lines)

@app.route("/", methods=["GET"])
def index(): return render_template("home.html")

@app.route("/abilities", methods=["GET"])
def abilities(): return render_template("abilities.html")

@app.route("/view", methods=["GET"])
def view_page(): return render_template("view.html")

@app.route("/extract", methods=["POST"])
def extract():
    url = request.form.get("url")
    if not url: return render_template("view.html", error="Target URL is required.")

    logging.info(f"Extracting: {url}")
    data = scraper.extract(url)

    if not data or data.get("status") == "error":
        return render_template("view.html", error=data.get("error", "Extraction failed."))

    # Proxy thumbnails to avoid CDN region blocks
    if data.get("thumbnail"):
        data["thumbnail"] = f"/proxy?url={quote(data['thumbnail'], safe='')}"

    return render_template("player.html", data=data)

@app.route("/proxy", methods=["GET", "OPTIONS"])
def proxy_media():
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Accept, Origin, Referer, User-Agent",
            "Access-Control-Max-Age": "86400"
        }
        return Response(status=204, headers=headers)

    target = request.args.get("url")
    if not target: return "Missing URL", 400

    parsed_url = urlparse(target)

    if "phncdn" in parsed_url.netloc or "pornhub" in parsed_url.netloc: referer = "https://www.pornhub.com/"
    elif "xnxx" in parsed_url.netloc: referer = "https://www.xnxx.com/"
    elif "xvideos" in parsed_url.netloc: referer = "https://www.xvideos.com/"
    elif "googlevideo" in parsed_url.netloc or "youtube" in parsed_url.netloc: referer = "https://www.youtube.com/"
    else: referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": referer,
        "Origin": referer.rstrip('/'),
        "Accept-Encoding": "identity"
    }

    range_header = request.headers.get("Range")
    if range_header: req_headers["Range"] = range_header

    try:
        try:
            upstream = requests.get(target, headers=req_headers, stream=True, timeout=15)
            upstream.raise_for_status()
        except requests.exceptions.RequestException as direct_err:
            logging.warning(f"Direct connection failed, attempting WARP SOCKS5: {direct_err}")
            try:
                active_proxies = {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"}
                upstream = requests.get(target, headers=req_headers, proxies=active_proxies, stream=True, timeout=20)
                upstream.raise_for_status()
            except requests.exceptions.RequestException as proxy_err:
                logging.error(f"WARP SOCKS5 also failed: {proxy_err}")
                return Response(f"Upstream connection failed: {proxy_err}", status=502)

        excluded_headers = ['transfer-encoding', 'connection']
        res_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded_headers}
        res_headers["Access-Control-Allow-Origin"] = "*"
        res_headers["Accept-Ranges"] = "bytes"

        content_type = upstream.headers.get("content-type", "").lower()
        is_m3u8 = "mpegurl" in content_type or "hls" in content_type or ".m3u8" in target or "manifest/hls" in target

        if is_m3u8:
            text = upstream.text
            final_base = upstream.url or target
            rewritten = rewrite_playlist(text, final_base)
            
            res_headers.pop('content-encoding', None)
            res_headers.pop('content-length', None)
            
            res_headers["Content-Type"] = "application/vnd.apple.mpegurl"
            res_headers["Content-Length"] = str(len(rewritten.encode('utf-8')))
            
            upstream.close()
            return Response(rewritten, status=upstream.status_code, headers=res_headers)

        def generate():
            try:
                for chunk in upstream.raw.stream(131072, decode_content=False):
                    if chunk: yield chunk
            except Exception as stream_err:
                logging.warning(f"Stream suppressed EOF drop: {stream_err}")
                pass
            finally:
                upstream.close()

        return Response(generate(), status=upstream.status_code, headers=res_headers, direct_passthrough=True)

    except Exception as e:
        logging.error(f"Proxy failure for {target}: {e}")
        return Response(f"Proxy stream failure: {e}", status=502)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)