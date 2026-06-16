#!/usr/bin/env python3
"""
TikTok Comment Scraper — Standalone Playwright script with stealth.
Called via subprocess from Flask to avoid asyncio event loop issues.
Usage: python3 tiktok_playwright.py <video_url> [limit]
Output: JSON to stdout
"""
import sys, json, re, subprocess, os

os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/root/.cache/ms-playwright'

def resolve_url(url):
    """Resolve short TikTok URLs to get video ID."""
    match = re.search(r'/video/(\d+)', url)
    if match:
        return url, match.group(1)
    
    try:
        result = subprocess.run(
            f'curl -sI -L --max-time 10 "{url}"',
            shell=True, capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.split('\n'):
            if 'location:' in line.lower():
                loc = line.split(':', 1)[1].strip()
                m = re.search(r'/video/(\d+)', loc)
                if m:
                    return loc, m.group(1)
    except:
        pass
    return None, None


def scrape_comments(video_url, limit=200):
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth
    stealth = Stealth()
    
    resolved_url, video_id = resolve_url(video_url)
    if not video_id:
        return {"error": "Could not extract TikTok video ID", "comments": [], "total_raw": 0, "total_unique": 0}
    
    if not resolved_url or '@/video/' in resolved_url:
        resolved_url = f"https://www.tiktok.com/video/{video_id}"
    
    comments = []
    seen = set()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='en-US',
            timezone_id='America/New_York',
        )
        page = ctx.new_page()
        stealth.apply_stealth_sync(page)
        
        try:
            page.goto(resolved_url, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(3000)
            
            # Check if video loaded
            title = page.title()
            if 'Make Your Day' in title and page.locator('[data-e2e="browse-video"]').count() == 0:
                # Page didn't load video content — might be blocked
                pass
            
            # Try clicking comment area to open comments
            try:
                comment_btn = page.locator('[data-e2e="comment-icon"]').first
                if comment_btn.is_visible(timeout=5000):
                    comment_btn.click()
                    page.wait_for_timeout(2000)
            except:
                pass
            
            # Also try clicking the comment count text
            try:
                comment_count = page.locator('[data-e2e="comment-count"]').first
                if comment_count.is_visible(timeout=3000):
                    comment_count.click()
                    page.wait_for_timeout(2000)
            except:
                pass
            
            # Scroll to load comments
            scroll_attempts = 0
            max_scrolls = 20
            stale_count = 0
            
            while scroll_attempts < max_scrolls and len(comments) < limit:
                # Try multiple selectors for comments
                for selector in [
                    '[data-e2e="comment-level-1"]',
                    '[data-e2e="comment-item"]',
                    '.comment-item',
                    '[class*="CommentItem"]',
                    '[class*="comment-item"]',
                ]:
                    comment_elements = page.locator(selector).all()
                    if comment_elements:
                        break
                
                for el in comment_elements:
                    try:
                        # Try multiple username selectors
                        username = None
                        for u_sel in [
                            '[data-e2e="comment-username-1"]',
                            '[data-e2e="comment-username"]',
                            'a[href*="/@"]',
                            '[class*="username"]',
                            '[class*="UserName"]',
                        ]:
                            try:
                                u_el = el.locator(u_sel).first
                                username = u_el.inner_text(timeout=1000).strip().lstrip('@')
                                if username:
                                    break
                            except:
                                continue
                        
                        if not username:
                            continue
                        
                        # Get comment text
                        text = ""
                        for t_sel in [
                            '[data-e2e="comment-level-1"]',
                            '[data-e2e="comment-text"]',
                            '[class*="comment-text"]',
                            '[class*="CommentText"]',
                            'p',
                            'span',
                        ]:
                            try:
                                t_el = el.locator(t_sel).first
                                text = t_el.inner_text(timeout=1000).strip()[:200]
                                if text:
                                    break
                            except:
                                continue
                        
                        if username and username.lower() not in seen:
                            seen.add(username.lower())
                            comments.append({"username": username, "text": text})
                    except:
                        continue
                
                if len(comments) == stale_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                stale_count = len(comments)
                
                # Scroll within comment panel
                try:
                    comment_panel = page.locator('[data-e2e="comment-list"]').first
                    if comment_panel.is_visible(timeout=1000):
                        comment_panel.evaluate('el => el.scrollTop += 500')
                    else:
                        page.evaluate('window.scrollBy(0, 500)')
                except:
                    page.evaluate('window.scrollBy(0, 500)')
                
                page.wait_for_timeout(1500)
                
                # Try "View more" / "Load more"
                try:
                    for btn_text in ['View more', 'Load more', 'Lihat lainnya', 'View replies']:
                        more_btn = page.locator(f'text=/{btn_text}/i').first
                        if more_btn.is_visible(timeout=500):
                            more_btn.click()
                            page.wait_for_timeout(1500)
                except:
                    pass
                    
        except Exception as e:
            pass
        finally:
            browser.close()
    
    return {
        "comments": comments[:limit],
        "total_raw": len(comments),
        "total_unique": len(seen),
        "platform": "tiktok",
        "video_id": video_id,
        "method": "playwright"
    }

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    
    if not url:
        print(json.dumps({"error": "No URL provided"}))
        sys.exit(1)
    
    result = scrape_comments(url, limit)
    print(json.dumps(result))
