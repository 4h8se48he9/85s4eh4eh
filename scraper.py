import json
import re
import os
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
        self.proxies = {"http": "socks5h://127.0.0.1:40000", "https": "socks5h://127.0.0.1:40000"}
        self._write_deno_script()

    def _write_deno_script(self):
        """Generates the Deno script that uses CURL to bypass the Python PySocks bug."""
        deno_code = """
        const targetUrl = Deno.args[0];
        
        const cmd = new Deno.Command("curl", {
            args: [
                "-sSL",
                "-x", "socks5h://127.0.0.1:40000",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-H", "Accept-Language: en-US,en;q=0.9",
                targetUrl
            ]
        });
        
        const { stdout, stderr, code } = cmd.outputSync();
        if (code !== 0) {
            console.error("Curl WARP execution failed: " + new TextDecoder().decode(stderr));
            Deno.exit(1);
        }

        const html = new TextDecoder().decode(stdout);
        const match = html.match(/ytInitialPlayerResponse\\s*=\\s*(\\{.+?\\});/);

        if (!match) {
            console.error("YouTube blocked the request or URL is invalid.");
            Deno.exit(1);
        }

        try {
            const data = JSON.parse(match[1]);
            const details = data.videoDetails || {};
            const strData = data.streamingData || {};

            const result = {
                status: "success",
                extractor: "youtube",
                title: details.title || "Unknown Title",
                uploader: details.author || "",
                duration: parseInt(details.lengthSeconds || "0"),
                thumbnail: (details.thumbnail && details.thumbnail.thumbnails && details.thumbnail.thumbnails.length > 0) ? details.thumbnail.thumbnails[0].url : "",
                tags: details.keywords || [],
                url: targetUrl,
                streams: { direct_mp4: {}, video_only: {}, audio_only: {}, qualities: [], hls_master: strData.hlsManifestUrl || null, dash_manifest: strData.dashManifestUrl || null }
            };

            const formats = [...(strData.formats || []), ...(strData.adaptiveFormats || [])];

            for (const fmt of formats) {
                if (!fmt.url) continue;

                const hasVideo = !!fmt.width;
                const hasAudio = !!fmt.audioSampleRate;
                const ext = fmt.mimeType ? (fmt.mimeType.includes("mp4") ? "mp4" : "webm") : "mp4";
                const label = fmt.height ? `${fmt.height}p` : "auto";

                if (hasVideo && hasAudio) result.streams.direct_mp4[label] = fmt.url;
                else if (hasVideo && !hasAudio) result.streams.video_only[`${label} - ${ext}`] = fmt.url;
                else if (!hasVideo && hasAudio) result.streams.audio_only[`${fmt.bitrate || 'auto'}bps - ${ext}`] = fmt.url;
            }

            console.log(JSON.stringify(result));
        } catch (e) {
            console.error("JSON Parse Error: " + e.message);
            Deno.exit(1);
        }
        """
        with open("yt_scraper.ts", "w", encoding="utf-8") as f:
            f.write(deno_code)

    def safe_request(self, url):
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            resp.raise_for_status()
            return resp
        except:
            return requests.get(url, headers=self.headers, proxies=self.proxies, timeout=15)

    def parse_hls_qualities(self, master_m3u8_url):
        qualities = []
        try:
            resp = self.safe_request(master_m3u8_url)
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

    def title_case(self, text):
        return re.sub(r"\b\w", lambda m: m.group(0).upper(), text.strip().lower()) if text else ""

    def extract(self, url):
        m = re.search(r"\[([a-z0-9]{6,8})\]\.[^.]+$", url, re.I) or re.match(r"^([a-z0-9]{6,8})_.+", url, re.I)
        if m: url = f"https://www.xnxx.com/video-{m.group(1)}/x"

        if 'youtube.com' in url or 'youtu.be' in url or 'googlevideo.com' in url:
            return self._extract_youtube_deno(url)
        elif 'pornhub' in url:
            return self._scrape_pornhub(url)
        elif 'xnxx' in url or 'xvideos' in url:
            return self._scrape_xnxx_xvideos(url)
        return {"status": "error", "error": "Unsupported provider"}

    def _extract_youtube_deno(self, url):
        try:
            result = subprocess.run(["deno", "run", "-A", "yt_scraper.ts", url], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                data = json.loads(result.stdout.strip())
                
                hls_master = data.get("streams", {}).get("hls_master")
                if hls_master:
                    data["streams"]["qualities"] = self.parse_hls_qualities(hls_master)
                    
                return data
            
            error_details = result.stderr.strip() or "Region blocked or WARP tunnel dead."
            return {"status": "error", "error": f"YouTube WARP Extractor Failed: {error_details}"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

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
            
            if isinstance(qual_raw, list): qual = str(qual_raw[0]) if qual_raw else "auto"
            else: qual = str(qual_raw or "auto")
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