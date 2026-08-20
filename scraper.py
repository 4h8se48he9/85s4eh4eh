import json
import re
import os
import random
import tempfile
import requests
from lxml import html
import yt_dlp
import logging

class VideoScraper:
    def __init__(self, proxy_url=None):
        self.proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        
        # Load external proxy rotation pool if provided
        proxy_env = os.environ.get("PROXY_LIST", "")
        self.proxy_pool = [p.strip() for p in proxy_env.split(",") if p.strip()]
        
        # Load external session cookies if provided
        self.cookies_content = os.environ.get("YT_COOKIES", "")
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    def title_case(self, text):
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), text.strip().lower()) if text else ""

    def get_request_proxies(self):
        if self.proxy_pool:
            p = random.choice(self.proxy_pool)
            return {"http": p, "https": p}
        return self.proxies

    def extract(self, url):
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m:
            url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        if 'youtube.com' in url or 'youtu.be' in url:
            return self._extract_ytdlp(url)

        result = self._extract_ytdlp(url)
        
        if not result or not result.get("qualities"):
            if 'pornhub' in url:
                result = self._scrape_pornhub(url)
            elif 'xnxx' in url or 'xvideos' in url:
                result = self._scrape_xnxx_xvideos(url)
            else:
                result = {"status": "error", "error": "Unsupported provider or stream format", "url": url}

        return result

    def _extract_ytdlp(self, url):
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'user_agent': self.headers['User-Agent'],
            'extractor_args': {'youtube': {'player_client': ['web', 'mweb']}},
            'compat_opts': set(),
            'format': 'best',
            'hls_prefer_native': True
        }
        
        try:
            yt_dlp.utils.get_executable_path('deno')
        except Exception as e:
            logging.warning(f"Deno not found in PATH, signature extraction may fail: {e}")

        if self.proxy_pool:
            selected_proxy = random.choice(self.proxy_pool)
            ydl_opts['proxy'] = selected_proxy
            logging.info(f"Routing yt-dlp through residential proxy: {selected_proxy}")
        elif self.proxies and self.proxies.get("https"):
            ydl_opts['proxy'] = self.proxies["https"]

        if self.cookies_content:
            cookie_path = os.path.join(tempfile.gettempdir(), 'yt_cookies.txt')
            with open(cookie_path, 'w', encoding='utf-8') as f:
                f.write(self.cookies_content)
            ydl_opts['cookiefile'] = cookie_path
            logging.info("Injecting session cookies into extraction engine.")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info: return None

                qualities = []
                
                progressive = [f for f in info.get('formats', []) if f.get('acodec') != 'none' and f.get('vcodec') != 'none']
                
                if progressive:
                    for fmt in progressive:
                        height = fmt.get('height')
                        label = f"{height}p" if height else fmt.get('format_id', 'auto')
                        qualities.append({"label": label, "url": fmt.get('url')})
                else:
                    for fmt in info.get('formats', []):
                        f_url = fmt.get('url', '')
                        ext = fmt.get('ext', '')
                        proto = fmt.get('protocol', '')
                        height = fmt.get('height')
                        label = f"{height}p" if height else fmt.get('format_id', 'auto')

                        if ext == 'mp4' or proto.startswith('http') or 'm3u8' in f_url:
                            qualities.append({"label": label, "url": f_url})

                def extract_res(lbl):
                    m = re.search(r'(\d+)', lbl)
                    return int(m.group(1)) if m else 0
                
                qualities.sort(key=lambda x: extract_res(x['label']), reverse=True)

                seen = set()
                unique_qualities = []
                for q in qualities:
                    if q['label'] not in seen:
                        seen.add(q['label'])
                        unique_qualities.append(q)

                if not unique_qualities and info.get('url'):
                    unique_qualities.append({"label": "Default", "url": info.get('url')})

                return {
                    "status": "success",
                    "title": info.get('title', 'Unknown Title'),
                    "thumbnail": info.get('thumbnail', ''),
                    "qualities": unique_qualities,
                    "url": url
                }
        except Exception as e:
            return {"status": "error", "error": f"Extraction failed: {str(e)}", "url": url}

    def _scrape_pornhub(self, url):
        viewkey = None
        if 'viewkey=' in url: 
            viewkey = url.split('viewkey=')[1].split('&')[0]
        elif 'embed/' in url: 
            viewkey = url.split('embed/')[1].split('?')[0]

        if not viewkey:
            return {"status": "error", "error": "Invalid viewkey", "url": url}

        geo_url = url.replace("www.pornhub.com", "de.pornhub.com")
        title, poster, media_defs = "Unknown Title", "", []
        req_proxies = self.get_request_proxies()

        try:
            resp = requests.get(geo_url, headers=self.headers, proxies=req_proxies, timeout=15)
            if resp.status_code == 200:
                fv_match = re.search(r'flashvars(?:_\d+)?\s*=\s*(\{.*?\});', resp.text, re.DOTALL)
                if fv_match:
                    data = json.loads(fv_match.group(1))
                    media_defs = data.get('mediaDefinitions', [])
                    title = data.get('video_title') or title
                    poster = data.get('image_url') or data.get('thumb_url') or poster
        except Exception:
            pass

        qualities = []
        for m in media_defs:
            if not isinstance(m, dict): continue
            v_url = m.get('videoUrl') or m.get('url')
            if not v_url or not isinstance(v_url, str): continue
            
            fmt = m.get('format', '')
            qual = str(m.get('quality', 'auto'))
            q_label = f"{qual}p" if qual.isdigit() else qual.upper()
            qualities.append({"label": q_label, "url": v_url})

        qualities.sort(key=lambda x: int(re.search(r'\d+', x['label']).group()) if re.search(r'\d+', x['label']) else 0, reverse=True)

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": poster,
            "qualities": qualities,
            "url": url
        }

    def _scrape_xnxx_xvideos(self, url):
        req_proxies = self.get_request_proxies()
        try:
            resp = requests.get(url, headers=self.headers, proxies=req_proxies, timeout=15)
            if resp.status_code != 200:
                return {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}
        except Exception as e:
            return {"status": "error", "error": str(e), "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)
        
        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"
        
        thumb_raw = tree.xpath('//meta[@property="og:image"]/@content')
        thumbnail = thumb_raw[0] if thumb_raw else ""
        
        qualities = []
        
        hls_match = re.search(r'(?:setVideoHLS|html5player\.setVideoHLS)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        high_match = re.search(r'(?:setVideoUrlHigh|html5player\.setVideoUrlHigh)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        low_match = re.search(r'(?:setVideoUrlLow|html5player\.setVideoUrlLow)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        
        if hls_match: qualities.append({"label": "Auto (HLS)", "url": hls_match.group(1)})
        if high_match: qualities.append({"label": "High", "url": high_match.group(1)})
        if low_match: qualities.append({"label": "Low", "url": low_match.group(1)})

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": thumbnail,
            "qualities": qualities,
            "url": url
        }
