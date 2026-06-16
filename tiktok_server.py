#!/usr/bin/env python3
"""TikTok Comment Fetcher — standalone HTTP server.

Uses Playwright (TikTokApi) to get proper cookies/context,
then fetches ALL comments (top-level + replies) from TikTok API.

Run: python3 tiktok_server.py
Endpoint: POST http://localhost:8090/fetch  {"video_id": "..."}
"""

import asyncio
import json
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Global state
_loop = None
_page = None
_ready = threading.Event()


async def init_browser():
    """Initialize persistent Playwright browser session."""
    global _page
    from TikTokApi import TikTokApi
    
    api = TikTokApi()
    await asyncio.wait_for(
        api.create_sessions(headless=True, num_sessions=1, timeout=45000),
        timeout=60
    )
    session = api.sessions[0]
    _page = session.page
    
    await _page.goto('https://www.tiktok.com/', timeout=30000, wait_until='domcontentloaded')
    await asyncio.sleep(3)
    print("[INIT] Browser ready!")
    _ready.set()
    
    # Keep loop alive
    while True:
        await asyncio.sleep(3600)


def run_async(coro):
    """Run async coroutine from sync context."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=180)


async def _fetch_comments(video_url_or_id):
    """Fetch ALL TikTok comments (top-level + replies)."""
    global _page
    
    # Extract video ID
    video_id = None
    m = re.search(r'/video/(\d+)', video_url_or_id)
    if m:
        video_id = m.group(1)
    
    if not video_id:
        m = re.search(r'tiktok\.com/(\w+)', video_url_or_id)
        if m:
            import requests as req
            try:
                r = req.head(f'https://vt.tiktok.com/{m.group(1)}/', allow_redirects=True, timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                m2 = re.search(r'/video/(\d+)', r.url)
                if m2:
                    video_id = m2.group(1)
            except:
                pass
    
    if not video_id:
        return {'error': 'Could not extract video ID'}
    
    # Navigate to video
    try:
        await _page.goto(f'https://www.tiktok.com/@_/video/{video_id}', timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(2)
    except:
        pass
    
    # Fetch comments via page.evaluate
    result = await _page.evaluate('''(vid) => {
        return new Promise(async (resolve) => {
            let all = [];
            let cursor = 0;
            let hasMore = true;
            let iter = 0;
            
            while (hasMore && iter < 20) {
                try {
                    let resp = await fetch(`/api/comment/list/?aweme_id=${vid}&cursor=${cursor}&count=50&aid=1988`, {credentials:'include'});
                    let data = await resp.json();
                    if (data.status_code !== 0 || !data.comments || data.comments.length === 0) break;
                    
                    for (let c of data.comments) {
                        all.push({
                            u: c.user ? c.user.unique_id : '',
                            t: (c.text || '').substring(0, 200),
                            cid: c.cid,
                            rc: c.reply_comment_total || 0,
                            likes: c.digg_count || 0
                        });
                    }
                    
                    hasMore = data.has_more === 1 || data.has_more === true;
                    cursor = data.cursor || (cursor + 50);
                    iter++;
                } catch(e) { break; }
            }
            
            let replyComments = [];
            let parents = all.filter(c => c.rc > 0);
            
            for (let parent of parents) {
                let rCursor = 0;
                let rMore = true;
                let rIter = 0;
                
                while (rMore && rIter < 10) {
                    try {
                        let rResp = await fetch(`/api/comment/list/reply/?aweme_id=${vid}&comment_id=${parent.cid}&cursor=${rCursor}&count=50&aid=1988`, {credentials:'include'});
                        let rData = await rResp.json();
                        if (rData.status_code !== 0 || !rData.comments || rData.comments.length === 0) break;
                        
                        for (let rc of rData.comments) {
                            replyComments.push({
                                u: rc.user ? rc.user.unique_id : '',
                                t: (rc.text || '').substring(0, 200),
                                likes: rc.digg_count || 0
                            });
                        }
                        
                        rMore = rData.has_more === 1 || rData.has_more === true;
                        rCursor = rData.cursor || (rCursor + 50);
                        rIter++;
                    } catch(e) { break; }
                }
            }
            
            let topLevel = all.map(c => ({u: c.u, t: c.t, likes: c.likes}));
            resolve({
                top_level: topLevel.length,
                replies: replyComments.length,
                total: topLevel.length + replyComments.length,
                comments: [...topLevel, ...replyComments]
            });
        });
    }''', video_id)
    
    seen = set()
    unique = []
    for c in result.get('comments', []):
        if c['u'] and c['u'].lower() not in seen:
            seen.add(c['u'].lower())
            unique.append({'username': c['u'], 'text': c['t'], 'likes': c['likes']})
    
    return {
        'comments': unique,
        'total': result.get('total', 0),
        'top_level': result.get('top_level', 0),
        'replies': result.get('replies', 0),
        'method': 'tiktok-api',
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}
        
        video_url = data.get('video_id', '') or data.get('url', '')
        if not video_url:
            self._respond(400, {'error': 'video_id or url required'})
            return
        
        try:
            result = run_async(_fetch_comments(video_url))
            code = 500 if 'error' in result else 200
            self._respond(code, result)
        except Exception as e:
            self._respond(500, {'error': str(e)})
    
    def do_GET(self):
        if self.path == '/health':
            self._respond(200, {'status': 'ok', 'browser': _ready.is_set()})
        else:
            self._respond(405, {'error': 'Use POST /fetch'})
    
    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        print(f"[HTTP] {format % args}")


if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    
    # Run browser init in background thread with its own event loop
    def start_loop():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(init_browser())
    
    t = threading.Thread(target=start_loop, daemon=True)
    t.start()
    
    print("[MAIN] Waiting for browser...")
    _ready.wait(timeout=90)
    print("[MAIN] Starting HTTP server on :8090")
    
    server = HTTPServer(('0.0.0.0', 8090), Handler)
    server.serve_forever()
