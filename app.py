#!/usr/bin/env python3
"""
Giveaway App - Graha Computer Purwokerto
Instagram + TikTok comment scraper for giveaway picking
"""
import os
import json
import asyncio
import traceback
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

# ============================================================
# INSTAGRAM - Full scraping via instagrapi
# ============================================================
def scrape_instagram(post_url, limit=None):
    """Scrape ALL comments from Instagram post using instagrapi"""
    from instagrapi import Client
    
    # Extract shortcode from URL
    import re
    match = re.search(r'instagram\.com/p/([^/]+)', post_url)
    if not match:
        match = re.search(r'instagram\.com/reel/([^/]+)', post_url)
    if not match:
        return {"error": "Invalid Instagram URL"}
    
    shortcode = match.group(1)
    
    # Login with session
    from urllib.parse import unquote
    cl = Client()
    session_id = unquote('76878440476%3AeHvDx6LZZCgrsp%3A7%3AAYje9qAI51eT1WTPQQ-FNhnoZwKzO7lmy6pJa58WbA')
    
    try:
        cl.login_by_sessionid(session_id)
    except Exception as e:
        return {"error": f"Instagram login failed: {str(e)}"}
    
    try:
        media_pk = cl.media_pk_from_code(shortcode)
        comments = cl.media_comments(media_pk, amount=0)  # 0 = all
        
        result = []
        seen = set()
        for c in comments:
            username = c.user.username
            if username not in seen:
                seen.add(username)
                result.append({
                    "username": username,
                    "text": c.text[:200] if c.text else "",
                    "timestamp": str(c.created_at_utc) if c.created_at_utc else ""
                })
        
        return {
            "platform": "instagram",
            "post_url": post_url,
            "total_comments": len(comments),
            "unique_users": len(result),
            "comments": result
        }
    except Exception as e:
        return {"error": f"Failed to fetch comments: {str(e)}"}


# ============================================================
# TIKTOK - Full scraping via Playwright + X-Bogus
# ============================================================
_browser = None
_playwright = None

async def get_browser():
    global _browser, _playwright
    if _browser is None:
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
    return _browser

async def scrape_tiktok_async(video_url, limit=None):
    """Scrape TikTok comments via Playwright + X-Bogus signing"""
    import re
    
    # Extract video ID
    video_id = None
    match = re.search(r'/video/(\d+)', video_url)
    if match:
        video_id = match.group(1)
    else:
        # Try short URL - resolve it
        try:
            resp = requests.head(video_url, allow_redirects=True, timeout=10)
            match = re.search(r'/video/(\d+)', resp.url)
            if match:
                video_id = match.group(1)
        except:
            pass
    
    if not video_id:
        return {"error": "Could not extract TikTok video ID"}
    
    browser = await get_browser()
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 1.0; Win64; x64) AppleWebKit/537.36'
    )
    page = await context.new_page()
    
    try:
        # Navigate to TikTok to get cookies/tokens
        await page.goto('https://www.tiktok.com/@tiktok', wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)
        
        # Fetch comments via TikTok API with X-Bogus
        all_comments = []
        cursor = 0
        has_more = True
        
        while has_more and (limit is None or len(all_comments) < limit):
            api_url = f'https://www.tiktok.com/api/comment/list/?aweme_id={video_id}&cursor={cursor}&count=50'
            
            # Generate X-Bogus via page context
            try:
                signed_url = await page.evaluate(f'''
                    () => {{
                        try {{
                            return window._xbogus ? window._xbogus("{api_url}") : null;
                        }} catch(e) {{
                            return null;
                        }}
                    }}
                ''')
            except:
                signed_url = None
            
            fetch_url = signed_url if signed_url else api_url
            
            # Fetch via page context
            data = await page.evaluate(f'''
                async () => {{
                    try {{
                        const resp = await fetch("{fetch_url}", {{
                            headers: {{
                                'User-Agent': navigator.userAgent
                            }}
                        }});
                        return await resp.json();
                    }} catch(e) {{
                        return {{error: e.message}};
                    }}
                }}
            ''')
            
            if not data or data.get('error') or not data.get('comments'):
                break
            
            for c in data['comments']:
                comment = {
                    "username": c.get('user', {}).get('unique_id', 'unknown'),
                    "text": c.get('text', '')[:200],
                    "likes": c.get('digg_count', 0)
                }
                all_comments.append(comment)
                
                # Also fetch replies
                try:
                    reply_url = f'https://www.tiktok.com/api/comment/list/reply/?comment_id={c.get("cid","")}&cursor=0&count=50'
                    replies = await page.evaluate(f'''
                        async () => {{
                            try {{
                                const resp = await fetch("{reply_url}");
                                return await resp.json();
                            }} catch(e) {{
                                return null;
                            }}
                        }}
                    ''')
                    if replies and replies.get('comments'):
                        for r in replies['comments']:
                            all_comments.append({
                                "username": r.get('user', {}).get('unique_id', 'unknown'),
                                "text": r.get('text', '')[:200],
                                "likes": r.get('digg_count', 0)
                            })
                except:
                    pass
            
            has_more = data.get('has_more', 0) == 1
            cursor = data.get('cursor', cursor + 50)
        
        # Deduplicate
        seen = set()
        unique = []
        for c in all_comments:
            if c['username'] not in seen:
                seen.add(c['username'])
                unique.append(c)
        
        return {
            "platform": "tiktok",
            "video_url": video_url,
            "total_comments": len(all_comments),
            "unique_users": len(unique),
            "comments": unique
        }
    
    except Exception as e:
        return {"error": f"TikTok scraping failed: {str(e)}"}
    finally:
        await context.close()


def scrape_tiktok(video_url, limit=None):
    """Sync wrapper for TikTok scraping"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
        return asyncio.run(scrape_tiktok_async(video_url, limit))
    except Exception as e:
        return {"error": str(e)}


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
    limit = data.get('limit')
    
    if not post_url:
        return jsonify({"error": "URL is required"}), 400
    
    # Auto-detect platform
    if platform == 'auto':
        if 'instagram' in post_url or 'instagr.am' in post_url:
            platform = 'instagram'
        elif 'tiktok' in post_url:
            platform = 'tiktok'
        else:
            return jsonify({"error": "Unknown platform"}), 400
    
    if platform == 'instagram':
        result = scrape_instagram(post_url, limit)
    elif platform == 'tiktok':
        result = scrape_tiktok(post_url, limit)
    else:
        return jsonify({"error": f"Unsupported platform: {platform}"}), 400
    
    return jsonify(result)

@app.route('/health')
def health():
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
