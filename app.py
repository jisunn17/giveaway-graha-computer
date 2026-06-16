"""
Giveaway App - Graha Computer Purwokerto (FILTER EDITION)
Instagram + TikTok comment scraper with tag filtering
"""
import os
import re
import json
import subprocess
import time
from urllib.parse import unquote
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests as req

app = Flask(__name__, static_folder='.')
CORS(app)

# ============================================================
# FILTER HELPERS
# ============================================================
def count_mentions(text):
    """Count @mentions in comment text."""
    return len(re.findall(r'@[\w.]+', text or ''))

def has_store_tag(text, platform):
    """Check if comment mentions the store account."""
    if not text:
        return False
    text_lower = text.lower()
    if platform == 'instagram':
        return 'grahacomp.purwokerto' in text_lower or '@grahacomp.purwokerto' in text_lower
    elif platform == 'tiktok':
        return 'grahacomppurwokerto' in text_lower or '@grahacomppurwokerto' in text_lower
    return False

def apply_filters(comments, platform, filter_type='all', min_mentions=0):
    """Apply filters to comment list.
    filter_type: 'all', 'tag_store', 'tag_3people', 'tag_store_and_3people'
    """
    filtered = []
    for c in comments:
        text = c.get('text', '')
        
        # Filter: must tag store
        if filter_type in ('tag_store', 'tag_store_and_3people'):
            if not has_store_tag(text, platform):
                continue
        
        # Filter: must tag N people
        if filter_type in ('tag_3people', 'tag_store_and_3people'):
            if count_mentions(text) < min_mentions:
                continue
        
        filtered.append(c)
    
    return filtered


# ============================================================
# INSTAGRAM - Full scraping via instagrapi
# ============================================================
def scrape_instagram(post_url, limit=None, filter_type='all', min_mentions=3):
    from instagrapi import Client
    
    match = re.search(r'instagram\.com/(?:p|reel|tv)/([^/]+)', post_url)
    if not match:
        return {"error": "Invalid Instagram URL"}
    
    shortcode = match.group(1)
    cl = Client()
    session_id = unquote('76878440476%3AeHvDx6LZZCgrsp%3A7%3AAYje9qAI51eT1WTPQQ-FNhnoZwKzO7lmy6pJa58WbA')
    
    try:
        cl.login_by_sessionid(session_id)
    except Exception as e:
        return {"error": f"Instagram login failed: {str(e)}"}
    
    try:
        media_pk = cl.media_pk_from_code(shortcode)
        media_info = cl.media_info(media_pk)
        comments = cl.media_comments(media_pk, amount=0)
        
        all_comments = []
        seen = set()
        for c in comments:
            username = c.user.username
            if username not in seen:
                seen.add(username)
                all_comments.append({
                    "username": username,
                    "text": c.text[:200] if c.text else "",
                    "mentions": count_mentions(c.text),
                    "has_store_tag": has_store_tag(c.text, 'instagram'),
                })
        
        # Apply filters
        filtered = apply_filters(all_comments, 'instagram', filter_type, min_mentions)
        
        return {
            "platform": "instagram",
            "total_raw": media_info.comment_count or len(comments),
            "total_unique": len(all_comments),
            "total_filtered": len(filtered),
            "filter_type": filter_type,
            "comments": filtered
        }
    except Exception as e:
        return {"error": f"Failed to fetch comments: {str(e)}"}


# ============================================================
# TIKTOK - tikwm pagination + pikkik fallback
# ============================================================
def resolve_tiktok_url(video_url):
    match = re.search(r'/video/(\d+)', video_url)
    if match:
        return match.group(1)
    try:
        result = subprocess.run(
            f'curl -sI -L --max-time 10 "{video_url}"',
            shell=True, capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.split('\n'):
            if 'location:' in line.lower():
                loc = line.split(':', 1)[1].strip()
                m = re.search(r'/video/(\d+)', loc)
                if m:
                    return m.group(1)
    except:
        pass
    return None


def _tikwm_fetch(video_url, cursor=0, count=50):
    """Fetch one page of TikTok comments via tikwm.com using curl."""
    try:
        result = subprocess.run(
            f'curl -s -X POST "https://www.tikwm.com/api/comment/list" '
            f'-d "url={video_url}&count={count}&cursor={cursor}" --max-time 20',
            shell=True, capture_output=True, text=True, timeout=25
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except:
        pass
    return None


def scrape_tiktok(video_url, limit=None, filter_type='all', min_mentions=3):
    """Scrape TikTok comments — tikwm pagination (500+), fallback to pikkik."""
    limit = limit or 500
    video_id = resolve_tiktok_url(video_url)
    if not video_id:
        return {"error": "Could not extract TikTok video ID", "comments": [], "total_raw": 0, "total_unique": 0}
    
    tikwm_url = f"https://www.tiktok.com/video/{video_id}"
    
    # Method 1: tikwm.com with pagination via curl
    try:
        all_comments = []
        seen = set()
        cursor = 0
        empty_pages = 0
        
        while len(all_comments) < limit and empty_pages < 3:
            data = _tikwm_fetch(tikwm_url, cursor=cursor, count=50)
            if not data or data.get('code') != 0:
                break
            
            page_data = data.get('data', {})
            comments = page_data.get('comments', [])
            has_more = page_data.get('hasMore', False)
            next_cursor = page_data.get('cursor', 0)
            
            if not comments:
                empty_pages += 1
                cursor = next_cursor if next_cursor else cursor + 50
                time.sleep(0.3)
                continue
            
            empty_pages = 0
            for c in comments:
                user = c.get('user', {})
                username = user.get('unique_id', '')
                text = c.get('text', '')[:200]
                if not username or username.lower() in seen:
                    continue
                seen.add(username.lower())
                all_comments.append({
                    "username": username,
                    "text": text,
                    "likes": int(c.get('digg_count', 0)),
                    "mentions": count_mentions(text),
                    "has_store_tag": has_store_tag(text, 'tiktok'),
                })
            
            if not has_more:
                break
            cursor = next_cursor if next_cursor else cursor + len(comments)
            time.sleep(0.3)
        
        if all_comments:
            filtered = apply_filters(all_comments, 'tiktok', filter_type, min_mentions)
            return {
                "platform": "tiktok",
                "total_raw": len(all_comments),
                "total_unique": len(all_comments),
                "total_filtered": len(filtered),
                "filter_type": filter_type,
                "comments": filtered[:limit],
                "method": "tikwm"
            }
    except Exception:
        pass
    
    # Method 2: Pikkik API fallback
    try:
        req.get(f'https://pikkik.com/fetch/async/{video_id}', timeout=30)
        time.sleep(3)
        resp = req.get('https://pikkik.com/winners', params={
            'vid': video_id, 'nodupes': 'true', 'count': '9999',
            'mentions': '0', 'keywords': '', 'html': '0',
        }, timeout=30)
        if resp.status_code == 200:
            comments_data = resp.json()
            if comments_data:
                result = []
                seen = set()
                for c in comments_data:
                    username = c.get('unique_name', '')
                    text = c.get('comment_text', '')[:200]
                    if not username or username.lower() in seen:
                        continue
                    seen.add(username.lower())
                    result.append({
                        "username": username,
                        "text": text,
                        "likes": int(c.get('likes', 0)),
                        "mentions": count_mentions(text),
                        "has_store_tag": has_store_tag(text, 'tiktok'),
                    })
                filtered = apply_filters(result, 'tiktok', filter_type, min_mentions)
                return {
                    "platform": "tiktok",
                    "total_raw": len(comments_data),
                    "total_unique": len(result),
                    "total_filtered": len(filtered),
                    "filter_type": filter_type,
                    "comments": filtered[:limit],
                    "method": "pikkik"
                }
    except Exception:
        pass
    
    return {"error": "All TikTok methods failed", "comments": [], "total_raw": 0, "total_unique": 0}


# ============================================================
# API ROUTES
# ============================================================
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/api/scrape', methods=['POST'])
def scrape():
    data = request.json
    post_url = data.get('url', '')
    platform = data.get('platform', 'auto')
    filter_type = data.get('filter', 'all')  # 'all', 'tag_store', 'tag_3people', 'tag_store_and_3people'
    min_mentions = int(data.get('min_mentions', 3))
    
    if not post_url:
        return jsonify({"error": "URL is required"}), 400
    
    if platform == 'auto':
        if 'instagram' in post_url or 'instagr.am' in post_url:
            platform = 'instagram'
        elif 'tiktok' in post_url:
            platform = 'tiktok'
        else:
            return jsonify({"error": "Unknown platform"}), 400
    
    if platform == 'instagram':
        result = scrape_instagram(post_url, filter_type=filter_type, min_mentions=min_mentions)
    elif platform == 'tiktok':
        result = scrape_tiktok(post_url, filter_type=filter_type, min_mentions=min_mentions)
    else:
        return jsonify({"error": f"Unsupported platform: {platform}"}), 400
    
    return jsonify(result)

@app.route('/health')
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    app.run(host='0.0.0.0', port=port)
