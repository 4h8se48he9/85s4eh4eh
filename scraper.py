import json
import re
import os
import sys
import subprocess
from urllib.parse import urlparse, urljoin
import requests
from lxml import html

class VideoScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    def title_case(self, text):
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), text.strip().lower()) if text else ""

    def _extract_res(self, label):
        """Safely extract resolution integer for sorting."""
        m = re.search(r'(\d+)', str(label))
        return int(m.group(1)) if m else 0

    def parse_hls_qualities(self, master_m3u8_url):
        qualities = []
        try:
            resp = requests.get(master_m3u8_url, headers=self.headers, timeout=15)
            if resp.status_code != 200: return qualities
            lines = resp.text.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    height = res_match.group(1).split("x")[1] if res_match and "x" in res_match.group(1) else "0"
                    quality_label = f"{height}p" if height.isdigit() and int(height) > 0 else "auto"
                    if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                        stream_uri = lines[i + 1].strip()
                        stream_url = stream_uri if stream_uri.startswith('http') else urljoin(master_m3u8_url, stream_uri)
                        qualities.append({"quality": quality_label, "url": stream_url})
        except Exception: pass
        
        qualities.sort(key=lambda x: self._extract_res(x['quality']), reverse=True)
        return qualities

    def _extract_youtube_subprocess(self, url):
        base_cmd = [
            "/app/venv/bin/python", "-m", "yt_dlp",
            "-J",
            "--no-warnings",
            "--extractor-args", "youtube:client=ios,android,web",
            "--socket-timeout", "20",
            url
        ]

        try:
            process = subprocess.Popen(base_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=45)

            if process.returncode != 0:
                proxy_cmd = base_cmd[:3] + ["--proxy", "socks5h://127.0.0.1:40000"] + base_cmd[3:]
                process = subprocess.Popen(proxy_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
                stdout, stderr = process.communicate(timeout=45)

            if process.returncode != 0:
                return {"status": "error", "error": f"YouTube Extractor Failed: {stderr.strip()}"}

            data = json.loads(stdout.strip())
            
            result = {
                "status": "success",
                "extractor": "youtube",
                "title": data.get("title", "Unknown Title"),
                "uploader": data.get("uploader", ""),
                "duration": data.get("duration", 0),
                "thumbnail": data.get("thumbnail", ""),
                "tags": data.get("tags", []),
                "url": data.get("webpage_url", url),
                "streams": { "direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": [], "hls_master": None, "dash_manifest": None }
            }

            seen = set()
            for fmt in data.get("formats", []):
                f_url = fmt.get("url")
                if not f_url or f_url in seen: continue
                seen.add(f_url)

                ext = fmt.get("ext", "mp4")
                height = fmt.get("height")
                label = f"{height}p" if height else (fmt.get("format_id", "auto"))

                is_hls = '.m3u8' in f_url or 'manifest/hls' in f_url
                if is_hls:
                    if height:
                        result["streams"]["qualities"].append({
                            "quality": label,
                            "resolution": f"{fmt.get('width', 0)}x{height}",
                            "bandwidth": fmt.get("tbr") or fmt.get("vbr") or 0,
                            "type": "hls",
                            "url": f_url
                        })
                    if 'master.m3u8' in f_url or fmt.get("format_id") == "hls-meta":
                        if not result["streams"]["hls_master"]:
                            result["streams"]["hls_master"] = f_url
                    continue

                has_video = fmt.get("vcodec") and fmt.get("vcodec") != "none"
                has_audio = fmt.get("acodec") and fmt.get("acodec") != "none"

                if has_video and has_audio: result["streams"]["direct_mp4"][label] = f_url
                elif has_video and not has_audio: result["streams"]["video_only"][f"{label} - {ext}"] = f_url
                elif not has_video and has_audio: result["streams"]["audio_only"][f"{fmt.get('abr', 'auto')}kbps - {ext}"] = f_url
                elif ext == 'mp4' or fmt.get("protocol", "").startswith("http"): result["streams"]["direct_mp4"][label] = f_url

            if not result["streams"]["hls_master"] and data.get("manifest_url") and '.m3u8' in data.get("manifest_url"):
                result["streams"]["hls_master"] = data["manifest_url"]

            result["streams"]["qualities"].sort(key=lambda x: self._extract_res(x['quality']), reverse=True)
            return result

        except Exception as e:
            return {"status": "error", "error": f"JSON or Execution Error: {str(e)}"}

    def _scrape_pornhub(self, url):
        viewkey = None
        if 'viewkey=' in url: viewkey = url.split('viewkey=')[1].split('&')[0]
        elif 'embed/' in url: viewkey = url.split('embed/')[1].split('?')[0]
        if not viewkey: return {"status": "error", "error": "Invalid viewkey", "url": url}

        geo_url = url.replace("www.pornhub.com", "de.pornhub.com")
        title, poster, media_defs = "Unknown Title", "", []

        try:
            resp = requests.get(geo_url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                fv_match = re.search(r'flashvars(?:_\d+)?\s*=\s*(\{.*?\});', resp.text, re.DOTALL)
                if fv_match:
                    data = json.loads(fv_match.group(1))
                    media_defs = data.get('mediaDefinitions', [])
                    title = data.get('video_title') or title
                    poster = data.get('image_url') or data.get('thumb_url') or poster
        except Exception: pass

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        seen_q = set()
        
        for m in media_defs:
            if not isinstance(m, dict): continue
            v_url = m.get('videoUrl') or m.get('url')
            if not v_url or not isinstance(v_url, str): continue
            
            fmt = m.get('format', '')
            qual_raw = m.get('quality')
            
            if isinstance(qual_raw, list):
                qual = str(qual_raw[0]) if qual_raw else "auto"
            else:
                qual = str(qual_raw or "auto")
                
            if qual == "[]" or not qual: qual = "auto"
                
            if fmt == 'mp4' or 'mp4' in v_url:
                q_label = f"{qual}p" if qual.isdigit() else qual.upper()
                stream_data["direct_mp4"][q_label] = v_url
                
            if fmt == 'hls' or '.m3u8' in v_url:
                for pq in self.parse_hls_qualities(v_url):
                    if pq["quality"] not in seen_q:
                        seen_q.add(pq["quality"])
                        stream_data["qualities"].append(pq)
                        
        return {"status": "success", "title": title.strip(), "thumbnail": poster, "streams": stream_data, "url": url}

    def _scrape_xnxx_xvideos(self, url):
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200: return {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}
        except Exception as e: return {"status": "error", "error": str(e), "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)
        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"
        thumb_raw = tree.xpath('//meta[@property="og:image"]/@content')
        thumbnail = thumb_raw[0] if thumb_raw else ""

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        hls_match = re.search(r'(?:setVideoHLS|html5player\.setVideoHLS)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        high_match = re.search(r'(?:setVideoUrlHigh|html5player\.setVideoUrlHigh)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        low_match = re.search(r'(?:setVideoUrlLow|html5player\.setVideoUrlLow)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)

        if hls_match: stream_data["qualities"] = self.parse_hls_qualities(hls_match.group(1))
        if high_match: stream_data["direct_mp4"]["High"] = high_match.group(1)
        if low_match: stream_data["direct_mp4"]["Low"] = low_match.group(1)

        return {"status": "success", "title": title.strip(), "thumbnail": thumbnail, "streams": stream_data, "url": url}

    def extract(self, url):
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m: url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        if 'youtube.com' in url or 'youtu.be' in url or 'googlevideo.com' in url:
            return self._extract_youtube_subprocess(url)
        elif 'pornhub' in url:
            return self._scrape_pornhub(url)
        elif 'xnxx' in url or 'xvideos' in url:
            return self._scrape_xnxx_xvideos(url)
            
        return {"status": "error", "error": "Unsupported provider"}