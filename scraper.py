import json
import re
import os
import sys
import subprocess
from urllib.parse import urlparse
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

    def parse_hls_qualities(self, master_m3u8_url):
        qualities = []
        try:
            resp = requests.get(master_m3u8_url, headers=self.headers, timeout=15)
            if resp.status_code != 200: return qualities
            lines = resp.text.splitlines()
            base_url = master_m3u8_url.rsplit('/', 1)[0] + '/'
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    height = res_match.group(1).split("x")[1] if res_match and "x" in res_match.group(1) else "0"
                    quality_label = f"{height}p" if height.isdigit() and int(height) > 0 else "auto"
                    if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                        stream_uri = lines[i + 1].strip()
                        stream_url = stream_uri if stream_uri.startswith('http') else base_url + stream_uri
                        qualities.append({"quality": quality_label, "url": stream_url})
        except Exception: pass
        qualities.sort(key=lambda x: int(re.search(r'(\d+)', x['quality']).group(1)) if re.search(r'(\d+)', x['quality']) else 0, reverse=True)
        return qualities

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

    def _scrape_3movs(self, url):
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            if resp.status_code != 200: 
                return {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}
        except Exception as e: 
            return {"status": "error", "error": str(e), "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)
        
        # 1. Extract Title
        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"
        if title == "Unknown Title":
            title_tag = tree.xpath('//title/text()')
            if title_tag:
                title = title_tag[0].split('|')[0].split('-')[0].strip()

        # 2. Extract Thumbnail
        thumb_raw = tree.xpath('//meta[@property="og:image"]/@content')
        thumbnail = thumb_raw[0] if thumb_raw else ""
        if not thumbnail:
            poster_match = re.search(r'poster\s*[:=]\s*["\']([^"\']+)["\']', page)
            if poster_match:
                thumbnail = poster_match.group(1).replace('\\/', '/')

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        sources = []
        
        # Method A: Look for <source> elements in the HTML natively used by Fluid Player
        for src in tree.xpath('//source/@src'):
            sources.append(src)
            
        # Method B: Parse configuration dictionaries / FlashVars using Regex
        for match in re.finditer(r'(?:video_url|video_alt_url\d*|src|file)\s*[:=]\s*["\'](https?://[^"\']+\.(?:mp4|m3u8)[^"\']*)["\']', page):
            sources.append(match.group(1).replace('\\/', '/'))
            
        # Method C: Aggressive fallback catching raw URLs pointing to media files in the page source
        for match in re.finditer(r'["\'](https?://[^"\']+\.(?:mp4|m3u8)[^"\']*)["\']', page):
            sources.append(match.group(1).replace('\\/', '/'))

        seen_q = set()
        seen_urls = set()
        
        for src in sources:
            if not src or src in seen_urls: continue
            
            # Filter out ads, thumbnails, preview chunks, and sprite sheets
            if any(x in src.lower() for x in ['ad.', 'ads.', 'banner', 'sprite', 'thumb', 'preview', 'timeline']): 
                continue
                
            seen_urls.add(src)
            
            # Sort as HLS/M3U8
            if '.m3u8' in src:
                for pq in self.parse_hls_qualities(src):
                    if pq["quality"] not in seen_q:
                        seen_q.add(pq["quality"])
                        stream_data["qualities"].append(pq)
                        
            # Sort as MP4
            elif '.mp4' in src:
                qual = "auto"
                q_match = re.search(r'(\d{3,4})p', src, re.I)
                
                if q_match:
                    qual = f"{q_match.group(1)}p"
                else:
                    q_match2 = re.search(r'_(high|low|medium|hq|lq)_', src, re.I)
                    if q_match2:
                        qual = q_match2.group(1).capitalize()
                
                # Assign to MP4 streams if it hasn't been added yet
                if qual != "auto" or "auto" not in stream_data["direct_mp4"]:
                    stream_data["direct_mp4"][qual] = src

        return {"status": "success", "title": title.strip(), "thumbnail": thumbnail, "streams": stream_data, "url": url}

    def extract(self, url):
        # Optional: Normalize shortened tags to XNXX URLs
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m: 
            url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        # Routing Logic
        if 'pornhub' in url:
            return self._scrape_pornhub(url)
        elif 'xnxx' in url or 'xvideos' in url:
            return self._scrape_xnxx_xvideos(url)
        elif '3movs' in url:
            return self._scrape_3movs(url)
            
        return {"status": "error", "error": "Unsupported provider"}