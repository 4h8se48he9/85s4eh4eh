import json
import re
import os
import sys
from urllib.parse import urlparse, urljoin
import requests
from lxml import html

class VideoScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.google.com/'
        }
        self.proxies = {
            "http": "socks5h://127.0.0.1:40000",
            "https": "socks5h://127.0.0.1:40000"
        }

    def _fetch_page(self, url):
        try:
            resp = requests.get(url, headers=self.headers, timeout=8)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
        return requests.get(url, headers=self.headers, proxies=self.proxies, timeout=20)

    def title_case(self, text):
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), text.strip().lower()) if text else ""

    def clean_thumbnails(self, thumbs, base_url):
        clean = []
        seen = set()
        for t in thumbs:
            if not t or not isinstance(t, str):
                continue
            t = t.replace('\\/', '/').strip().strip('\'"')
            if t.startswith('//'):
                t = "https:" + t
            elif t.startswith('/'):
                t = urljoin(base_url, t)

            if t.startswith('http'):
                t_lower = t.lower()
                if any(bad in t_lower for bad in ['favicon', 'logo', 'icon', 'banner', 'avatar', 'blank', 'pixel', 'sprite', 'timeline', '.vtt', '.gif']):
                    continue
                if not any(ext in t_lower for ext in ['.jpg', '.jpeg', '.png', '.webp', 'preview', 'thumb', 'poster', 'screenshots']):
                    continue

                if t not in seen:
                    seen.add(t)
                    clean.append(t)
        return clean

    def parse_hls_qualities(self, master_m3u8_url):
        qualities = []
        try:
            resp = self._fetch_page(master_m3u8_url)
            if resp.status_code != 200:
                return qualities
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
        except Exception:
            pass
        qualities.sort(key=lambda x: int(re.search(r'(\d+)', x['quality']).group(1)) if re.search(r'(\d+)', x['quality']) else 0, reverse=True)
        return qualities

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
        raw_thumbs = set()

        try:
            resp = self._fetch_page(geo_url)
            if resp.status_code == 200:
                fv_match = re.search(r'flashvars(?:_\d+)?\s*=\s*(\{.*?\});', resp.text, re.DOTALL)
                if fv_match:
                    data = json.loads(fv_match.group(1))
                    media_defs = data.get('mediaDefinitions', [])
                    title = data.get('video_title') or title
                    poster = data.get('image_url') or data.get('thumb_url') or poster

                    if poster:
                        raw_thumbs.add(poster)
                    for k, v in data.items():
                        if isinstance(v, str) and v.startswith('http') and any(ext in v.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                            raw_thumbs.add(v)
                        elif isinstance(v, list):
                            for item in v:
                                if isinstance(item, str) and item.startswith('http') and any(ext in item.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                                    raw_thumbs.add(item)
                                elif isinstance(item, dict) and 'url' in item and isinstance(item['url'], str) and item['url'].startswith('http'):
                                    raw_thumbs.add(item['url'])
        except Exception:
            pass

        clean_thumbs = self.clean_thumbnails(raw_thumbs, url)
        if clean_thumbs and not poster:
            poster = clean_thumbs[0]

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        seen_q = set()
        for m in media_defs:
            if not isinstance(m, dict):
                continue
            v_url = m.get('videoUrl') or m.get('url')
            if not v_url or not isinstance(v_url, str):
                continue
            fmt = m.get('format', '')
            qual_raw = m.get('quality')
            qual = str(qual_raw[0]) if isinstance(qual_raw, list) and qual_raw else str(qual_raw or "auto")
            if qual == "[]" or not qual:
                qual = "auto"

            if fmt == 'mp4' or 'mp4' in v_url:
                q_label = f"{qual}p" if qual.isdigit() else qual.upper()
                stream_data["direct_mp4"][q_label] = v_url
            if fmt == 'hls' or '.m3u8' in v_url:
                for pq in self.parse_hls_qualities(v_url):
                    if pq["quality"] not in seen_q:
                        seen_q.add(pq["quality"])
                        stream_data["qualities"].append(pq)

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": poster,
            "thumbnails": clean_thumbs,
            "streams": stream_data,
            "url": url,
            "provider": "pornhub"
        }

    def _scrape_xnxx_xvideos(self, url):
        try:
            resp = self._fetch_page(url)
            if resp.status_code != 200:
                return {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}
        except Exception as e:
            return {"status": "error", "error": str(e), "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)
        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"
        thumb_raw = tree.xpath('//meta[@property="og:image"]/@content')

        raw_thumbs = set()
        if thumb_raw:
            raw_thumbs.add(thumb_raw[0])
        for m in re.finditer(r'setThumbUrl(?:169|Slide|)?\(\s*[\'"](https?://[^\'"]+)[\'"]\s*\)', page):
            raw_thumbs.add(m.group(1))

        clean_thumbs = self.clean_thumbnails(raw_thumbs, url)
        thumbnail = clean_thumbs[0] if clean_thumbs else ""

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        hls_match = re.search(r'(?:setVideoHLS|html5player\.setVideoHLS)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        high_match = re.search(r'(?:setVideoUrlHigh|html5player\.setVideoUrlHigh)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        low_match = re.search(r'(?:setVideoUrlLow|html5player\.setVideoUrlLow)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)

        if hls_match:
            stream_data["qualities"] = self.parse_hls_qualities(hls_match.group(1))
        if high_match:
            stream_data["direct_mp4"]["High Quality"] = high_match.group(1)
        if low_match:
            stream_data["direct_mp4"]["Low Quality"] = low_match.group(1)

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": thumbnail,
            "thumbnails": clean_thumbs,
            "streams": stream_data,
            "url": url,
            "provider": "xnxx"
        }

    def _scrape_3movs(self, url):
        try:
            resp = self._fetch_page(url)
            if resp.status_code != 200:
                return {"status": "error", "error": f"HTTP {resp.status_code}", "url": url}
        except Exception as e:
            return {"status": "error", "error": str(e), "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)

        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"
        if title == "Unknown Title":
            title_tag = tree.xpath('//title/text()')
            if title_tag:
                title = title_tag[0].split('|')[0].split('-')[0].strip()

        raw_thumbs = set()
        for meta in tree.xpath('//meta[@property="og:image"]/@content | //meta[@name="twitter:image"]/@content'):
            raw_thumbs.add(meta)
        for poster in tree.xpath('//video/@poster | //div[contains(@id, "player")]//img/@src'):
            raw_thumbs.add(poster)
        for m in re.finditer(r'poster(?:Image)?\s*[:=]\s*["\']([^"\']+)["\']', page, re.I):
            raw_thumbs.add(m.group(1))
        for img in tree.xpath('//div[contains(@class, "screenshots") or contains(@class, "gallery")]//img/@src | //div[contains(@class, "screenshots") or contains(@class, "gallery")]//img/@data-src'):
            raw_thumbs.add(img)

        clean_thumbs = self.clean_thumbnails(raw_thumbs, url)
        thumbnail = clean_thumbs[0] if clean_thumbs else ""

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "qualities": []}
        player_sources = []

        player_source_elements = tree.xpath('//div[contains(@id, "player") or contains(@class, "player")]//video//source | //video//source')
        for s in player_source_elements:
            src = s.get('src')
            title_attr = s.get('title') or s.get('label') or ''
            if src and '.mp4' in src.lower():
                player_sources.append({'url': src, 'label': title_attr})

        if not player_sources:
            for m in re.finditer(r'(?:video_url|video_alt_url|src|file)\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']', page):
                player_sources.append({'url': m.group(1), 'label': 'Standard Quality'})

        for idx, s in enumerate(player_sources):
            full_src = s['url'].replace('\\/', '/').strip()
            if full_src.startswith('//'):
                full_src = "https:" + full_src
            elif full_src.startswith('/'):
                full_src = urljoin(url, full_src)

            label = s['label'] if s['label'] else f"Quality {idx+1}"
            stream_data["direct_mp4"][label] = full_src

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": thumbnail,
            "thumbnails": clean_thumbs,
            "streams": stream_data,
            "url": url,
            "provider": "3movs"
        }

    def extract(self, url):
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m:
            url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        if 'pornhub' in url:
            return self._scrape_pornhub(url)
        elif 'xnxx' in url or 'xvideos' in url:
            return self._scrape_xnxx_xvideos(url)
        elif '3movs' in url:
            return self._scrape_3movs(url)
        return {"status": "error", "error": "Unsupported provider"}