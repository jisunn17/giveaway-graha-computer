#!/usr/bin/env python3
"""Flask backend for Graha Computer Giveaway — full version.

Instagram: Full access via instagrapi (no limit).
TikTok: Full access via Playwright + TikTok API (top-level + replies).
"""

import re
import time
import json
import asyncio
import threading
import requests as req_lib
from urllib.parse import unquote
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DEFAULT_SESSIONID = '76878440476%3AeHvDx6LZZCgrsp%3A7%3AAYje9qAI51eT1WTPQQ-FNhnoZwKzO7lmy6pJa58WbA'

# ── Instagram (instagrapi) ──
_ig_client = None
_ig_session_id = None

def get_ig_client(sessionid=None):
    global _ig_client, _ig_session_id
    from instagrapi import Client as InstaClient
    sid = unquote(sessionid or DEFAULT_SESSIONID)
    if _ig_client and _ig_session_id == sid:
        return _ig_client
    cl = InstaClient()
    cl.login_by_sessionid(sid)
    _ig_client = cl
    _ig_session_id = sid
    return cl

def scrape_instagram(shortcode, sessionid=None):
    try:
        cl = get_ig_client(sessionid)
        media_pk = cl.media_pk_from_code(shortcode)
        media_info = cl.media_info(media_pk)
        author = media_info.user.username if media_info.user else None
        comments_raw = cl.media_comments(media_pk, amount=0)
        comments = []
        seen = set()
        for c in comments_raw:
            username = c.user.username if c.user else ''
            if not username or username.lower() in seen:
                continue
            if author and username.lower() == author.lower():
                seen.add(username.lower())
                continue
            seen.add(username.lower())
            comments.append({'username': username, 'text': (c.text or '')[:200], 'likes': c.like_count or 0})
        return {'comments': comments, 'total': len(comments_raw), 'author': author, 'method': 'instagrapi'}
    except Exception as e:
        global _ig_client, _ig_session_id
        _ig_client = None
        _ig_session_id = None
        return {'error': f'instagrapi: {str(e)}'}


# ── TikTok (Playwright + API) ──
_tiktok_loop = None
_tiktok_page = None
_tiktok_ready = threading.Event()

async def _init_tiktok_browser():
    global _tiktok_page
    from TikTokApi import TikTokApi
    
    api = TikTokApi()
    await asyncio.wait_for(
        api.create_sessions(headless=True, num_sessions=1, timeout=45000),
        timeout=60
    )
    session = api.sessions[0]
    _tiktok_page = session.page
    await _tiktok_page.goto('https://www.tiktok.com/', timeout=30000, wait_until='domcontentloaded')
    await asyncio.sleep(3)
    print("[TIKTOK] Browser ready!")
    _tiktok_ready.set()
    while True:
        await asyncio.sleep(3600)

def _start_tiktok_loop():
    global _tiktok_loop
    _tiktok_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_tiktok_loop)
    _tiktok_loop.run_until_complete(_init_tiktok_browser())

def _run_tiktok_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, _tiktok_loop)
    return future.result(timeout=180)

async def _fetch_tiktok_comments(video_url):
    global _tiktok_page
    
    video_id = None
    m = re.search(r'/video/(\d+)', video_url)
    if m:
        video_id = m.group(1)
    
    if not video_id:
        m = re.search(r'tiktok\.com/(\w+)', video_url)
        if m:
            try:
                r = req_lib.head(f'https://vt.tiktok.com/{m.group(1)}/', allow_redirects=True, timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
                m2 = re.search(r'/video/(\d+)', r.url)
                if m2:
                    video_id = m2.group(1)
            except:
                pass
    
    if not video_id:
        return {'error': 'Could not extract TikTok video ID'}
    
    try:
        await _tiktok_page.goto(f'https://www.tiktok.com/@_/video/{video_id}', timeout=20000, wait_until='domcontentloaded')
        await asyncio.sleep(2)
    except:
        pass
    
    result = await _tiktok_page.evaluate('''(vid) => {
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
                        all.push({u: c.user ? c.user.unique_id : '', t: (c.text || '').substring(0, 200), cid: c.cid, rc: c.reply_comment_total || 0, likes: c.digg_count || 0});
                    }
                    hasMore = data.has_more === 1 || data.has_more === true;
                    cursor = data.cursor || (cursor + 50);
                    iter++;
                } catch(e) { break; }
            }
            
            let replyComments = [];
            let parents = all.filter(c => c.rc > 0);
            for (let parent of parents) {
                let rCursor = 0, rMore = true, rIter = 0;
                while (rMore && rIter < 10) {
                    try {
                        let rResp = await fetch(`/api/comment/list/reply/?aweme_id=${vid}&comment_id=${parent.cid}&cursor=${rCursor}&count=50&aid=1988`, {credentials:'include'});
                        let rData = await rResp.json();
                        if (rData.status_code !== 0 || !rData.comments || rData.comments.length === 0) break;
                        for (let rc of rData.comments) {
                            replyComments.push({u: rc.user ? rc.user.unique_id : '', t: (rc.text || '').substring(0, 200), likes: rc.digg_count || 0});
                        }
                        rMore = rData.has_more === 1 || rData.has_more === true;
                        rCursor = rData.cursor || (rCursor + 50);
                        rIter++;
                    } catch(e) { break; }
                }
            }
            
            let topLevel = all.map(c => ({u: c.u, t: c.t, likes: c.likes}));
            resolve({top_level: topLevel.length, replies: replyComments.length, total: topLevel.length + replyComments.length, comments: [...topLevel, ...replyComments]});
        });
    }''', video_id)
    
    seen = set()
    unique = []
    for c in result.get('comments', []):
        if c['u'] and c['u'].lower() not in seen:
            seen.add(c['u'].lower())
            unique.append({'username': c['u'], 'text': c['t'], 'likes': c['likes']})
    
    return {'comments': unique, 'total': result.get('total', 0), 'top_level': result.get('top_level', 0), 'replies': result.get('replies', 0), 'method': 'tiktok-api'}

def scrape_tiktok(video_url):
    if not _tiktok_ready.is_set():
        return {'error': 'TikTok browser not ready yet. Try again in a moment.'}
    try:
        return _run_tiktok_async(_fetch_tiktok_comments(video_url))
    except Exception as e:
        return {'error': f'TikTok error: {str(e)}'}


# ── Routes ──
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:filename>')
def static_files(filename):
    return send_from_directory('.', filename)

@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    data = request.json
    url = data.get('url', '')
    sessionid = data.get('sessionid', DEFAULT_SESSIONID)
    
    if not url:
        return jsonify({'error': 'URL diperlukan'}), 400
    
    try:
        if 'instagram.com' in url:
            match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
            if not match:
                return jsonify({'error': 'Link Instagram tidak valid'}), 400
            result = scrape_instagram(match.group(1), sessionid)
            if 'error' in result:
                return jsonify({'error': result['error']}), 500
            result['platform'] = 'instagram'
        
        elif 'tiktok.com' in url:
            result = scrape_tiktok(url)
            if 'error' in result:
                return jsonify({'error': result['error']}), 500
            result['platform'] = 'tiktok'
        
        else:
            return jsonify({'error': 'URL tidak didukung'}), 400
        
        seen = set()
        unique = []
        for c in result['comments']:
            if c['username'].lower() not in seen:
                seen.add(c['username'].lower())
                unique.append(c)
        
        return jsonify({
            'success': True,
            'platform': result.get('platform', ''),
            'total_raw': result['total'],
            'total_unique': len(unique),
            'author': result.get('author', ''),
            'method': result.get('method', ''),
            'comments': unique,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    
    # Start TikTok browser in background thread
    t = threading.Thread(target=_start_tiktok_loop, daemon=True)
    t.start()
    print("[MAIN] Waiting for TikTok browser...")
    _tiktok_ready.wait(timeout=90)
    
    app.run(host='0.0.0.0', port=8080, debug=False)
