import feedparser
import logging
from typing import List, Optional
from urllib.parse import urlparse
import requests
from datetime import datetime

class BloggerFeedParser:
    def __init__(self, feed_url: str):
        """Initialize the feed parser."""
        self.feed_url = feed_url
        self.logger = logging.getLogger(__name__)

    def get_all_post_urls(self, max_results: int = 500) -> List[str]:
        """
        Get all post URLs from the Blogger feed.
        
        Args:
            max_results: Maximum number of results to fetch
            
        Returns:
            List of post URLs
        """
        urls = []
        try:
            # Parse the feed
            feed = feedparser.parse(self.feed_url)
            
            if feed.bozo:
                self.logger.error(f"Error parsing feed: {feed.bozo_exception}")
                return urls
                
            # Extract URLs from entries
            for entry in feed.entries:
                if hasattr(entry, 'link'):
                    url = entry.link
                    urls.append(url)
        except Exception as e:
            self.logger.error(f"Error extracting URLs: {e}")
        return urls
