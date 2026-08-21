import json
import re
import os
import random
import logging
from urllib.parse import urlparse
import requests
from lxml import html
import yt_dlp

class VideoScraper:
    def __init__(self, proxy_url=None):
        default_proxy = proxy_url or os.environ.get("PROXY_URL", "socks5h://127.0.0.1:40000")
        self.proxies = {"http": default_proxy, "https": default_proxy} if default_proxy else None
        
        proxy_env = os.environ.get("PROXY_LIST", "")
        self.proxy_pool = [p.strip() for p in proxy_env.split(",") if p.strip()]

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

    def safe_request(self, url, timeout=15):
        proxies = self.get_request_proxies()
        try:
            resp = requests.get(url, headers=self.headers, proxies=proxies, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logging.warning(f"WARP proxy refused connection for {url}. Routing directly. ({e})")
            resp = requests.get(url, headers=self.headers, proxies=None, timeout=timeout)
            if resp.status_code != 200:
                raise Exception(f"Direct route failed with status {resp.status_code}")
            return resp

    def parse_hls_qualities(self, master_m3u8_url):
        qualities = []
        if not master_m3u8_url:
            return qualities
        try:
            resp = self.safe_request(master_m3u8_url)
            lines = resp.text.splitlines()
            base_url = master_m3u8_url.rsplit('/', 1)[0] + '/'
            
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    res_match = re.search(r"RESOLUTION=(\d+x\d+)", line)
                    bw_match = re.search(r"BANDWIDTH=(\d+)", line)
                    resolution = res_match.group(1) if res_match else "0x0"
                    bandwidth = int(bw_match.group(1)) if bw_match else 0
                    height = resolution.split("x")[1] if "x" in resolution else "0"
                    quality_label = f"{height}p" if height.isdigit() and int(height) > 0 else "auto"
                    
                    if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                        stream_uri = lines[i + 1].strip()
                        if stream_uri.startswith('http'):
                            stream_url = stream_uri
                        elif stream_uri.startswith('/'):
                            parsed = urlparse(master_m3u8_url)
                            stream_url = f"{parsed.scheme}://{parsed.netloc}{stream_uri}"
                        else:
                            stream_url = base_url + stream_uri
                        
                        qualities.append({
                            "quality": quality_label,
                            "resolution": resolution,
                            "bandwidth": bandwidth,
                            "type": "hls",
                            "url": stream_url
                        })
        except Exception:
            pass
        qualities.sort(key=lambda x: x.get('bandwidth', 0), reverse=True)
        return qualities

    def extract(self, url):
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m:
            url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        result = self._extract_ytdlp(url)
        
        is_extracted = result and result.get("streams") and (
            result["streams"].get("qualities") or 
            result["streams"].get("direct_mp4") or
            result["streams"].get("video_only") or
            result["streams"].get("audio_only")
        )

        if not is_extracted:
            if 'pornhub' in url:
                result = self._scrape_pornhub(url)
            elif 'xnxx' in url or 'xvideos' in url:
                result = self._scrape_xnxx_xvideos(url)
            else:
                err_msg = result.get("error") if result else "Extraction failed or format unsupported."
                result = {"status": "error", "error": err_msg, "url": url}

        return result

    def _extract_ytdlp(self, url):
        active_proxies = self.get_request_proxies()
        proxy_str = active_proxies.get("https") if active_proxies else None

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'user_agent': self.headers['User-Agent'],
        }
        
        info = None
        try:
            opts = dict(ydl_opts)
            if proxy_str:
                opts['proxy'] = proxy_str
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            logging.warning(f"yt-dlp WARP proxy failed: {e}. Retrying directly.")
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl_direct:
                    info = ydl_direct.extract_info(url, download=False)
            except Exception as e2:
                return {"status": "error", "error": f"Extraction failed: {str(e2)}", "url": url}

        if not info:
            return None

        direct_mp4 = {}
        video_only = {}
        audio_only = {}
        hls_master = None
        dash_manifest = info.get('manifest_url') if info.get('manifest_url') and '.mpd' in info.get('manifest_url') else None
        qualities = []
        seen = set()

        for fmt in info.get('formats', []):
            f_url = fmt.get('url', '')
            if not f_url or f_url in seen:
                continue
            seen.add(f_url)

            ext = fmt.get('ext', '')
            proto = fmt.get('protocol', '')
            height = fmt.get('height')
            format_note = fmt.get('format_note', '')
            vcodec = fmt.get('vcodec', 'none')
            acodec = fmt.get('acodec', 'none')

            label = f"{height}p" if height else (format_note or fmt.get('format_id', 'auto'))

            if 'm3u8' in f_url and ('master.m3u8' in f_url or fmt.get('format_id') == 'hls-meta'):
                if not hls_master:
                    hls_master = f_url

            if 'm3u8' in proto or ext == 'm3u8' or 'm3u8' in f_url or 'manifest/hls_playlist' in f_url:
                if height:
                    qualities.append({
                        "quality": label,
                        "resolution": f"{fmt.get('width', 0)}x{height}",
                        "bandwidth": fmt.get('tbr') or fmt.get('vbr') or 0,
                        "type": "hls",
                        "url": f_url
                    })
            else:
                has_video = vcodec != 'none' and bool(vcodec)
                has_audio = acodec != 'none' and bool(acodec)

                if has_video and has_audio:
                    direct_mp4[label] = f_url
                elif has_video and not has_audio:
                    video_only[f"{label} - {ext}"] = f_url
                elif not has_video and has_audio:
                    audio_only[f"{fmt.get('abr', 'auto')}kbps - {ext}"] = f_url
                elif ext == 'mp4' or proto.startswith('http'):
                    direct_mp4[label] = f_url

        if not hls_master and info.get('manifest_url') and '.m3u8' in info.get('manifest_url'):
            hls_master = info.get('manifest_url')

        if hls_master and not qualities:
            qualities = self.parse_hls_qualities(hls_master)

        qualities.sort(key=lambda q: int(re.search(r'(\d+)', q.get('quality', '')).group(1)) if re.search(r'(\d+)', q.get('quality', '')) else 0, reverse=True)

        return {
            "status": "success",
            "title": info.get('title', 'Unknown Title'),
            "thumbnail": info.get('thumbnail', ''),
            "streams": {
                "direct_mp4": direct_mp4,
                "video_only": video_only,
                "audio_only": audio_only,
                "hls_master": hls_master,
                "dash_manifest": dash_manifest,
                "qualities": qualities
            },
            "url": info.get('webpage_url', url)
        }

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

        try:
            resp = self.safe_request(geo_url)
            if resp and resp.status_code == 200:
                fv_match = re.search(r'flashvars(?:_\d+)?\s*=\s*(\{.*?\});', resp.text, re.DOTALL)
                if fv_match:
                    data = json.loads(fv_match.group(1))
                    media_defs = data.get('mediaDefinitions', [])
                    title = data.get('video_title') or title
                    poster = data.get('image_url') or data.get('thumb_url') or poster
        except Exception:
            pass

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "hls_master": None, "dash_manifest": None, "qualities": []}
        seen_q = set()

        for m in media_defs:
            if not isinstance(m, dict): continue
            v_url = m.get('videoUrl') or m.get('url')
            if not v_url or not isinstance(v_url, str): continue

            fmt = m.get('format', '')
            qual = str(m.get('quality', 'auto'))
            
            if fmt == 'mp4' or 'mp4' in v_url:
                q_label = f"{qual}p" if qual.isdigit() else qual.upper()
                stream_data["direct_mp4"][q_label] = v_url
            
            if fmt == 'hls' or '.m3u8' in v_url:
                if not stream_data["hls_master"]:
                    stream_data["hls_master"] = v_url
                for pq in self.parse_hls_qualities(v_url):
                    if pq["quality"] not in seen_q:
                        seen_q.add(pq["quality"])
                        stream_data["qualities"].append(pq)

        if stream_data["qualities"]:
            stream_data["qualities"].sort(key=lambda x: int(re.search(r'\d+', x['quality']).group()) if re.search(r'\d+', x['quality']) else 0, reverse=True)

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": poster,
            "streams": stream_data,
            "url": url
        }

    def _scrape_xnxx_xvideos(self, url):
        try:
            resp = self.safe_request(url)
        except Exception as e:
            return {"status": "error", "error": f"Connection failed: {str(e)}", "url": url}

        page = resp.text
        tree = html.fromstring(resp.content)

        title_raw = tree.xpath('//meta[@property="og:title"]/@content')
        title = self.title_case(title_raw[0]) if title_raw else "Unknown Title"

        thumb_raw = tree.xpath('//meta[@property="og:image"]/@content')
        thumbnail = thumb_raw[0] if thumb_raw else ""

        stream_data = {"direct_mp4": {}, "video_only": {}, "audio_only": {}, "hls_master": None, "dash_manifest": None, "qualities": []}

        hls_match = re.search(r'(?:setVideoHLS|html5player\.setVideoHLS)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        high_match = re.search(r'(?:setVideoUrlHigh|html5player\.setVideoUrlHigh)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)
        low_match = re.search(r'(?:setVideoUrlLow|html5player\.setVideoUrlLow)\(\s*[\'"]([^\'"]+)[\'"]\s*\)', page)

        if hls_match:
            stream_data["hls_master"] = hls_match.group(1)
            stream_data["qualities"] = self.parse_hls_qualities(stream_data["hls_master"])
        if high_match: stream_data["direct_mp4"]["High"] = high_match.group(1)
        if low_match: stream_data["direct_mp4"]["Low"] = low_match.group(1)

        return {
            "status": "success",
            "title": title.strip(),
            "thumbnail": thumbnail,
            "streams": stream_data,
            "url": url
        }