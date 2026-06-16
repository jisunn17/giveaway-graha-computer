"""Vercel serverless function — Instagram + TikTok comment scraper.

Instagram: Full access via instagrapi (no limit).
TikTok: Uses pikkik.com API to fetch comments (free tier: ~30 comments).
"""

import re
import json
import time
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler

try:
    from instagrapi import Client as InstaClient
    HAS_INSTAGRAPI = True
except ImportError:
    HAS_INSTAGRAPI = False

import requests

DEFAULT_SESSIONID = '76878440476%3AeHvDx6LZZCgrsp%3A7%3AAYje9qAI51eT1WTPQQ-FNhnoZwKzO7lmy6pJa58WbA'

_ig_client = None
_ig_session_id = None


def get_ig_client(sessionid=None):
    global _ig_client, _ig_session_id
    sid = unquote(sessionid or DEFAULT_SESSIONID)
    if _ig_client and _ig_session_id == sid:
        return _ig_client
    if not HAS_INSTAGRAPI:
        return None
    cl = InstaClient()
    cl.login_by_sessionid(sid)
    _ig_client = cl
    _ig_session_id = sid
    return cl


def scrape_instagram(shortcode, sessionid=None):
    try:
        cl = get_ig_client(sessionid)
        if not cl:
            return {'error': 'instagrapi not available'}
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


def scrape_tiktok(video_url):
    """Scrape TikTok comments using pikkik.com's API (free tier: ~30 comments)."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Referer': 'https://pikkik.com/',
        'Origin': 'https://pikkik.com',
    })
    
    # Extract video ID
    video_id = None
    m = re.search(r'/video/(\d+)', video_url)
    if m:
        video_id = m.group(1)
    
    # Short URL — resolve via pikkik
    if not video_id:
        m = re.search(r'(?:tiktok\.com|vm\.tiktok\.com)/(\w+)', video_url)
        if m:
            try:
                r = session.get(f'https://pikkik.com/convert/{m.group(1)}', timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    m2 = re.search(r'/video/(\d+)', data.get('url', ''))
                    if m2:
                        video_id = m2.group(1)
            except:
                pass
    
    # Try vt.tiktok.com short URL pattern
    if not video_id:
        try:
            r = requests.head(video_url, allow_redirects=True, timeout=10,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            m3 = re.search(r'/video/(\d+)', r.url)
            if m3:
                video_id = m3.group(1)
        except:
            pass
    
    if not video_id:
        return {'error': 'Could not extract TikTok video ID'}
    
    # Trigger comment fetching on pikkik
    try:
        r = session.get(f'https://pikkik.com/fetch/async/{video_id}', timeout=30)
        if r.status_code != 200:
            return {'error': f'Pikkik fetch failed: HTTP {r.status_code}'}
    except Exception as e:
        return {'error': f'Pikkik fetch error: {str(e)}'}
    
    # Get comments
    try:
        r2 = session.get('https://pikkik.com/winners', params={
            'vid': video_id,
            'nodupes': 'true',
            'count': '9999',
            'mentions': '0',
            'keywords': '',
            'html': '0',
        }, timeout=30)
        if r2.status_code != 200:
            return {'error': f'Pikkik winners failed: HTTP {r2.status_code}'}
        comments_data = r2.json()
    except Exception as e:
        return {'error': f'Pikkik data error: {str(e)}'}
    
    # Extract usernames
    comments = []
    seen = set()
    for c in comments_data:
        username = c.get('unique_name', '')
        if not username or username.lower() in seen:
            continue
        seen.add(username.lower())
        comments.append({
            'username': username,
            'text': c.get('comment_text', '')[:200],
            'likes': int(c.get('likes', 0)),
        })
    
    return {
        'comments': comments,
        'total': len(comments_data),
        'method': 'pikkik',
    }


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}
        url = data.get('url', '')
        sessionid = data.get('sessionid', DEFAULT_SESSIONID)
        
        if not url:
            self._respond(400, {'error': 'URL diperlukan'})
            return
        
        if 'instagram.com' in url:
            match = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
            if not match:
                self._respond(400, {'error': 'Link Instagram tidak valid'})
                return
            result = scrape_instagram(match.group(1), sessionid)
            if 'error' in result:
                self._respond(500, {'error': result['error']})
                return
            result['platform'] = 'instagram'
        
        elif 'tiktok.com' in url:
            result = scrape_tiktok(url)
            if 'error' in result:
                self._respond(500, {'error': result['error']})
                return
            result['platform'] = 'tiktok'
        
        else:
            self._respond(400, {'error': 'URL tidak didukung. Gunakan link Instagram atau TikTok.'})
            return
        
        seen = set()
        unique = []
        for c in result['comments']:
            if c['username'].lower() not in seen:
                seen.add(c['username'].lower())
                unique.append(c)
        
        self._respond(200, {
            'success': True,
            'platform': result.get('platform', ''),
            'total_raw': result['total'],
            'total_unique': len(unique),
            'author': result.get('author', ''),
            'method': result.get('method', ''),
            'comments': unique,
        })
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        self._respond(405, {'error': 'Use POST'})
    
    def _respond(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
