import asyncio
import json
import csv
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
import re
from urllib.parse import urljoin, urlparse
import time

class GuardianCrawler:
    def __init__(self):
        self.base_url = "https://www.guardian.co.tt"
        self.posts = []
        self.visited_urls = set()
        self.start_date = datetime.now() - timedelta(days=15*365)  # 15 years ago
        self.end_date = datetime.now()
        
    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            
            # Start crawling from the main page
            await self.crawl_page(page, self.base_url)
            
            # Try to find archive or older posts
            await self.explore_archives(page)
            
            await browser.close()
            
        # Save results
        self.save_results()
        
    async def crawl_page(self, page, url):
        try:
            print(f"Crawling: {url}")
            await page.goto(url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(3000)
            
            # Handle infinite scroll if present
            await self.handle_infinite_scroll(page)
            
            # Check if this is an article page itself
            article_post = await self.extract_article_from_page(page, url)
            if article_post:
                if url not in self.visited_urls:
                    self.visited_urls.add(url)
                    if self.is_within_date_range(article_post.get('date')):
                        self.posts.append(article_post)
                        print(f"Added article: {article_post['title'][:50]}...")
                        return  # Don't look for more links on article pages
            
            # Extract posts from current page (if it's a listing page)
            posts = await self.extract_posts(page)
            
            posts_added = 0
            for post in posts:
                if post and post.get('url') and post['url'] not in self.visited_urls:
                    self.visited_urls.add(post['url'])
                    
                    # Check if post is within date range
                    if self.is_within_date_range(post.get('date')):
                        self.posts.append(post)
                        posts_added += 1
                        print(f"Added post: {post['title'][:50]}...")
            
            print(f"Found {len(posts)} posts, added {posts_added} new posts from this page")
            
            # Look for pagination links only on listing pages
            if posts_added > 0 or len(posts) > 0:
                await self.handle_pagination(page)
            
        except Exception as e:
            print(f"Error crawling {url}: {e}")
    
    async def handle_infinite_scroll(self, page):
        """Handle infinite scroll loading"""
        try:
            last_height = await page.evaluate("document.body.scrollHeight")
            scroll_attempts = 0
            max_scrolls = 10  # Limit scrolling to prevent infinite loops
            
            while scroll_attempts < max_scrolls:
                # Scroll to bottom
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                
                # Check if new content loaded
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break
                
                last_height = new_height
                scroll_attempts += 1
                print(f"Scrolled {scroll_attempts} times, page height: {new_height}")
                
        except Exception as e:
            print(f"Error handling infinite scroll: {e}")
            
    async def extract_posts(self, page):
        posts = []
        
        # Guardian-specific and general selectors for news articles
        selectors = [
            'a[href*="-6.2."]',  # Guardian article pattern (most reliable)
            'a[href*="/news/"]',
            'a[href*="/sports/"]',
            'a[href*="/entertainment/"]',
            'a[href*="/business/"]',
            'a[href*="/article/"]',
            'a[href*="/opinion/"]',
            'a[href*="/features/"]',
            'article',
            '.post',
            '.entry',
            '.news-item',
            '.article-item',
            '[class*="post"]',
            '[class*="article"]',
        ]
        
        for selector in selectors:
            elements = await page.query_selector_all(selector)
            
            for element in elements:
                try:
                    post = await self.extract_post_data(element, page)
                    if post and post not in posts:
                        posts.append(post)
                except Exception as e:
                    continue
                    
        return posts
    
    async def extract_post_data(self, element, page):
        try:
            # Extract title
            title_selectors = ['h1', 'h2', 'h3', '.title', '.headline', 'a']
            title = None
            for sel in title_selectors:
                title_el = await element.query_selector(sel)
                if title_el:
                    title = await title_el.inner_text()
                    if title and len(title.strip()) > 0:
                        break
            
            if not title:
                return None
                
            # Extract URL
            url = None
            link_el = await element.query_selector('a')
            if link_el:
                href = await link_el.get_attribute('href')
                if href:
                    url = urljoin(self.base_url, href)
            
            # Filter out invalid URLs
            if not self.is_valid_url(url):
                return None
            
            # Extract date - Guardian-specific and general approaches
            date = None
            
            # Try Guardian-specific metadata first
            try:
                # Check for property="dateModified" or similar metadata
                meta_selectors = [
                    '[property="dateModified"]',
                    '[property="datePublished"]', 
                    '[name="dateModified"]',
                    '[name="datePublished"]'
                ]
                
                for meta_sel in meta_selectors:
                    meta_el = await page.query_selector(meta_sel)
                    if meta_el:
                        content = await meta_el.get_attribute('content')
                        if content:
                            print(f"Found metadata date: {content}")
                            date = self.parse_date(content)
                            if date:
                                print(f"Parsed metadata date: {date}")
                                break
            except Exception as e:
                print(f"Error extracting metadata date: {e}")
                pass
            
            # Fallback to element-based date extraction
            if not date:
                date_selectors = ['[datetime]', '.date', '.published', '.time', '.post-date', 'time']
                for sel in date_selectors:
                    date_el = await element.query_selector(sel)
                    if date_el:
                        # Try datetime attribute first
                        datetime_attr = await date_el.get_attribute('datetime')
                        if datetime_attr:
                            date = self.parse_date(datetime_attr)
                            if date:
                                break
                        
                        # Fallback to text content
                        date_text = await date_el.inner_text()
                        if date_text:
                            date = self.parse_date(date_text)
                            if date:
                                break
            
            # Extract excerpt/content preview - avoid short snippets
            content_selectors = ['.excerpt', '.summary', '.content', 'p', '.lead']
            content = None
            for sel in content_selectors:
                content_el = await element.query_selector(sel)
                if content_el:
                    content = await content_el.inner_text()
                    if content and len(content.strip()) > 50 and not self.is_author_only(content.strip()):
                        break
            
            return {
                'title': title.strip() if title else '',
                'url': url,
                'date': date,
                'content': content.strip() if content else '',
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            return None
    
    async def extract_article_from_page(self, page, url):
        """Extract article data if this page IS an article (not a listing)"""
        try:
            # Check if this looks like an article page
            if not self.is_valid_url(url):
                return None
            
            # Look for article-specific elements
            title_selectors = ['h1', 'h1.headline', '[property="headline"]']
            title = None
            
            for sel in title_selectors:
                title_el = await page.query_selector(sel)
                if title_el:
                    title = await title_el.inner_text()
                    if title and len(title.strip()) > 10:
                        break
            
            if not title:
                return None
            
            # Extract date from meta tags (Guardian specific)
            date = None
            try:
                meta_selectors = [
                    '[property="dateModified"]',
                    '[property="datePublished"]',
                    '[name="dateModified"]',
                    '[name="datePublished"]'
                ]
                
                for meta_sel in meta_selectors:
                    meta_el = await page.query_selector(meta_sel)
                    if meta_el:
                        content = await meta_el.get_attribute('content')
                        if content:
                            print(f"Found article metadata date: {content}")
                            date = self.parse_date(content)
                            if date:
                                break
            except:
                pass
            
            # Extract article content
            content_selectors = ['article p', '.content p', '.entry-content p', 'p']
            content_paragraphs = []
            
            for sel in content_selectors:
                try:
                    paragraphs = await page.query_selector_all(sel)
                    for p in paragraphs[:3]:  # First 3 paragraphs
                        text = await p.inner_text()
                        if text and len(text.strip()) > 30:
                            content_paragraphs.append(text.strip())
                    if content_paragraphs:
                        break
                except:
                    continue
            
            content = ' '.join(content_paragraphs) if content_paragraphs else ''
            
            return {
                'title': title.strip(),
                'url': url,
                'date': date,
                'content': content[:500] + '...' if len(content) > 500 else content,
                'scraped_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            print(f"Error extracting article from {url}: {e}")
            return None
    
    def is_valid_url(self, url):
        if not url:
            return False
        
        # Parse URL
        parsed = urlparse(url)
        
        # Must be guardian.co.tt domain (reject any other domains)
        if parsed.netloc != 'www.guardian.co.tt' and parsed.netloc != 'guardian.co.tt':
            return False
        
        # Exclude subscription and share URLs
        excluded_patterns = [
            '/amember/',
            '/signup/',
            'facebook.com',
            'twitter.com',
            'mailto:',
            'javascript:',
            '/search?',
            '/tag/',
            '/category/',
            '/section-',
            '/live-stream/',
            '/traffic-cameras/',
            '/weather/',
            'undefined',
        ]
        
        for pattern in excluded_patterns:
            if pattern in url.lower():
                return False
        
        # Must contain actual content indicators
        valid_patterns = [
            '/news/',
            '/sports/',
            '/entertainment/',
            '/business/',
            '/article/',
            '/opinion/',
            '/features/',
            '-6.2.',  # Guardian article ID pattern (most reliable)
        ]
        
        return any(pattern in url.lower() for pattern in valid_patterns)
    
    def is_author_only(self, content):
        # Check if content is just an author name
        author_indicators = ['by ', 'author:', 'written by']
        content_lower = content.lower().strip()
        
        # Very short content that starts with author indicators
        if len(content) < 30 and any(indicator in content_lower for indicator in author_indicators):
            return True
        
        # Single word or very short phrases that look like names
        words = content.split()
        if len(words) <= 3 and all(word.istitle() for word in words if word.isalpha()):
            return True
            
        return False
    
    def parse_date(self, date_string):
        try:
            date_string = date_string.strip()
            
            # Handle ISO format first (common in datetime attributes)
            iso_patterns = [
                r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})',
                r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)',
                r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)',
                r'(\d{4}-\d{2}-\d{2})',
            ]
            
            for pattern in iso_patterns:
                match = re.search(pattern, date_string)
                if match:
                    try:
                        iso_date = match.group(1)
                        # Handle different ISO formats
                        if 'T' in iso_date:
                            if '.' in iso_date:
                                return datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
                            else:
                                return datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
                        else:
                            return datetime.strptime(iso_date, '%Y-%m-%d')
                    except:
                        continue
            
            # Common display date formats (including Guardian's format)
            formats = [
                '%a, %d %b %Y %H:%M:%S %z',  # Guardian format: "Fri, 25 Jul 2025 22:58:41 -0400"
                '%Y-%m-%d',
                '%d/%m/%Y',
                '%m/%d/%Y',
                '%B %d, %Y',
                '%d %B %Y',
                '%b %d, %Y',
                '%d %b %Y',
                '%Y-%m-%d %H:%M:%S',
                '%d/%m/%Y %H:%M',
                '%m/%d/%Y %H:%M',
                '%a, %d %b %Y %H:%M:%S',  # Without timezone
            ]
            
            for fmt in formats:
                try:
                    return datetime.strptime(date_string, fmt)
                except ValueError:
                    continue
                    
            # Try to extract date with regex patterns
            date_patterns = [
                (r'(\d{4}-\d{2}-\d{2})', '%Y-%m-%d'),
                (r'(\d{1,2}/\d{1,2}/\d{4})', '%d/%m/%Y'),
                (r'([A-Za-z]+ \d{1,2}, \d{4})', '%B %d, %Y'),
                (r'(\d{1,2} [A-Za-z]+ \d{4})', '%d %B %Y'),
            ]
            
            for pattern, fmt in date_patterns:
                match = re.search(pattern, date_string)
                if match:
                    try:
                        return datetime.strptime(match.group(1), fmt)
                    except:
                        continue
                        
        except Exception as e:
            pass
            
        return None
    
    def is_within_date_range(self, date):
        if not date:
            return True  # Include posts without dates
        return self.start_date <= date <= self.end_date
    
    async def handle_pagination(self, page):
        # Look for pagination links - comprehensive approach
        pagination_selectors = [
            'a[rel="next"]',
            '.next-page',
            '.pagination a',
            '[class*="next"]',
            'a[href*="page="]',
            'a[href*="/page/"]',
            '.nav-links a',
            '.page-numbers a',
            '.paging a',
            'a[title*="Next"]',
            'a[title*="next"]',
        ]
        
        # First try standard pagination
        for selector in pagination_selectors:
            try:
                next_links = await page.query_selector_all(selector)
                for next_link in next_links:
                    href = await next_link.get_attribute('href')
                    link_text = await next_link.inner_text()
                    
                    if href and ('next' in link_text.lower() or 'more' in link_text.lower() or href.isdigit()):
                        next_url = urljoin(self.base_url, href)
                        if next_url not in self.visited_urls:
                            print(f"Following pagination: {next_url}")
                            await self.crawl_page(page, next_url)
                            return True
            except Exception as e:
                continue
        
        # Try numbered pagination (page 2, 3, 4, etc.)
        try:
            page_links = await page.query_selector_all('a[href*="page"]')
            page_numbers = []
            
            for link in page_links:
                href = await link.get_attribute('href')
                if href:
                    # Extract page number from URL
                    import re
                    match = re.search(r'page[=/](\d+)', href)
                    if match:
                        page_num = int(match.group(1))
                        page_numbers.append((page_num, urljoin(self.base_url, href)))
            
            # Sort and visit pages in order
            page_numbers.sort()
            for page_num, url in page_numbers:
                if url not in self.visited_urls:
                    print(f"Following numbered page: {url}")
                    await self.crawl_page(page, url)
                    await page.wait_for_timeout(2000)  # Be respectful
        except Exception as e:
            pass
        
        return False
    
    async def explore_archives(self, page):
        # Focus on working URLs and discover pages through navigation
        print("Exploring categories and discovering article pages...")
        
        # Start with known working sections
        working_urls = [
            f"{self.base_url}/news",
            f"{self.base_url}/sports", 
            f"{self.base_url}/business",
            f"{self.base_url}/entertainment",
            f"{self.base_url}/opinion",
            f"{self.base_url}/features",
        ]
        
        discovered_urls = set()
        
        # Discover links from main page
        try:
            print("Discovering links from main page...")
            await page.goto(self.base_url, wait_until="load")
            await page.wait_for_timeout(3000)
            
            # Look for all article links on main page
            article_link_selectors = [
                'a[href*="-6.2."]',  # Guardian article pattern
                'a[href*="/news/"]',
                'a[href*="/sports/"]', 
                'a[href*="/business/"]',
                'a[href*="/entertainment/"]',
                'a[href*="/opinion/"]',
                'a[href*="/features/"]',
            ]
            
            for selector in article_link_selectors:
                try:
                    links = await page.query_selector_all(selector)
                    for link in links:
                        href = await link.get_attribute('href')
                        if href:
                            full_url = urljoin(self.base_url, href)
                            if self.is_valid_url(full_url):
                                discovered_urls.add(full_url)
                except:
                    continue
                    
            print(f"Discovered {len(discovered_urls)} article URLs from main page")
            
        except Exception as e:
            print(f"Error discovering links from main page: {e}")
        
        # Try the working section URLs
        for url in working_urls:
            if url not in self.visited_urls:
                try:
                    print(f"Exploring section: {url}")
                    await self.crawl_page(page, url)
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"Error accessing section {url}: {e}")
                    continue
        
        # Process discovered article URLs directly
        processed = 0
        for url in discovered_urls:
            if url not in self.visited_urls and processed < 50:  # Limit to avoid endless crawling
                try:
                    processed += 1
                    print(f"Crawling discovered article {processed}: {url}")
                    await self.crawl_page(page, url)
                    await page.wait_for_timeout(1000)
                except Exception as e:
                    print(f"Error accessing {url}: {e}")
                    continue
    
    def save_results(self):
        print(f"\n{'='*50}")
        print(f"CRAWLING COMPLETED")
        print(f"{'='*50}")
        print(f"Total posts collected: {len(self.posts)}")
        print(f"Total URLs visited: {len(self.visited_urls)}")
        print(f"Date range: {self.start_date.date()} to {self.end_date.date()}")
        
        if not self.posts:
            print("No posts were collected!")
            return
        
        # Sort posts by date (newest first)
        self.posts.sort(key=lambda x: x.get('date') or datetime.min, reverse=True)
        
        # Save as JSON
        with open('guardian_posts.json', 'w', encoding='utf-8') as f:
            json.dump(self.posts, f, indent=2, ensure_ascii=False, default=str)
        
        # Save as CSV
        with open('guardian_posts.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.posts[0].keys())
            writer.writeheader()
            writer.writerows(self.posts)
        
        # Print statistics
        posts_with_dates = [p for p in self.posts if p.get('date')]
        if posts_with_dates:
            oldest = min(posts_with_dates, key=lambda x: x['date'])
            newest = max(posts_with_dates, key=lambda x: x['date'])
            print(f"Oldest post: {oldest['date'].date()} - {oldest['title'][:50]}...")
            print(f"Newest post: {newest['date'].date()} - {newest['title'][:50]}...")
        
        print(f"\nResults saved to:")
        print(f"  - guardian_posts.json ({len(self.posts)} posts)")
        print(f"  - guardian_posts.csv ({len(self.posts)} posts)")
        print(f"{'='*50}")

async def main():
    crawler = GuardianCrawler()
    await crawler.run()

if __name__ == "__main__":
    asyncio.run(main())