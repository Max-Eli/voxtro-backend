"""Website crawling service"""
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


async def crawl_and_extract(url: str, max_pages: int = 10) -> Dict:
    """
    Crawl website and extract content

    Args:
        url: Starting URL
        max_pages: Maximum pages to crawl

    Returns:
        Dict with pages_crawled and content_length
    """
    try:
        visited = set()
        to_visit = [url]
        content = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            while to_visit and len(visited) < max_pages:
                current_url = to_visit.pop(0)

                if current_url in visited:
                    continue

                try:
                    response = await client.get(current_url)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')

                        # Extract text content
                        text = soup.get_text(separator=' ', strip=True)
                        content.append(text)

                        visited.add(current_url)

                        # Find more links (simplified)
                        # In production, you'd want proper URL normalization

                except Exception as e:
                    logger.error(f"Error crawling {current_url}: {e}")
                    continue

        total_content = ' '.join(content)

        return {
            "pages_crawled": len(visited),
            "content_length": len(total_content),
            "content": total_content
        }

    except Exception as e:
        logger.error(f"Crawl error: {e}")
        raise
