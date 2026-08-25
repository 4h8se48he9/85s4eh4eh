import os
import logging
import re
import subprocess
from urllib.parse import urlparse, urljoin, quote
from flask import Flask, request, render_template, Response
import requests
from scraper import VideoScraper

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
app.secret_key = os.urandom(24)

scraper = VideoScraper()

def rewrite_playlist(playlist, base_url, provider):
    def build_proxy_uri(raw_uri):
        clean_uri = raw_uri.strip().strip("'\"")
        if clean_uri.startswith("data:"):
            return clean_uri
        resolved = urljoin(base_url, clean_uri)
        return f"/proxy?url={quote(resolved, safe='')}&provider={provider}"

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

    return render_template("player.html", data=data)

@app.route("/proxy")
def proxy_media():
    target = request.args.get("url")
    provider = request.args.get("provider", "")
    force_download = request.args.get("dl") == "1"
    
    if not target:
        return "Missing URL", 400

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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": referer,
        "Origin": referer.rstrip('/'),
        "Accept": "*/*",
        "Connection": "keep-alive"
    }

    range_header = request.headers.get("Range")
    if range_header and not force_download:
        req_headers["Range"] = range_header

    is_m3u8_url = ".m3u8" in target or "hls_playlist" in target

    # ==========================================
    # HLS DOWNLOADING LOGIC (FFMPEG MUXING)
    # ==========================================
    if force_download and is_m3u8_url:
        def generate_ffmpeg_stream():
            env = os.environ.copy()
            # Force FFmpeg to route through your local WARP connection to bypass filters
            env["HTTP_PROXY"] = "socks5h://127.0.0.1:40000"
            env["HTTPS_PROXY"] = "socks5h://127.0.0.1:40000"
            
            cmd = [
                'ffmpeg',
                '-hide_banner', '-loglevel', 'error',
                '-headers', f'Referer: {referer}\r\nOrigin: {referer.rstrip("/")}\r\nUser-Agent: Mozilla/5.0\r\n',
                '-i', target,
                '-c', 'copy',
                '-f', 'mp4',
                '-movflags', 'frag_keyframe+empty_moov',
                'pipe:1'
            ]
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                while True:
                    chunk = process.stdout.read(1024 * 1024) # 1MB stream chunks
                    if not chunk:
                        break
                    yield chunk
            except Exception as e:
                logging.error(f"FFmpeg stream error: {e}")
            finally:
                process.terminate()
                process.wait()

        dl_headers = {
            "Content-Disposition": 'attachment; filename="vexostream_hls.mp4"',
            "Content-Type": "video/mp4"
        }
        return Response(generate_ffmpeg_stream(), headers=dl_headers, direct_passthrough=False)


    # ==========================================
    # STANDARD PROXY LOGIC (Direct MP4 / M3U8 Player)
    # ==========================================
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

        is_m3u8 = "mpegurl" in content_type or is_m3u8_url

        if force_download:
            ext = "mp4" if ".mp4" in target.lower() or "video" in content_type else "bin"
            if "image" in content_type: ext = "jpg"
            res_headers["Content-Disposition"] = f'attachment; filename="vexostream_media.{ext}"'

        # Rewrite M3U8 specifically for the web player
        if is_m3u8 and not force_download:
            text = upstream.text
            final_base = upstream.url or target
            rewritten = rewrite_playlist(text, final_base, provider)
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
        logging.error(f"Proxy failure for {target}: {e}")
        return f"Proxy stream failure: {str(e)}", 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)