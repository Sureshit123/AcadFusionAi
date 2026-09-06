"""
Scraper module wrapper to support both 'scraper' and 'scrapper' imports seamlessly.
"""
from scrapper import (
    VTU_BASE,
    VTU_INDEX,
    VTU_RESULT,
    VTU_SITE_ROOT,
    VTU_MOCK_MODE,
    _compute_js_token,
    _resolve_vtu_urls,
    get_headers,
    get_mock_result,
    initialize_scrape,
    complete_scrape,
    parse_vtu_html,
    logger
)

__all__ = [
    'VTU_BASE',
    'VTU_INDEX',
    'VTU_RESULT',
    'VTU_SITE_ROOT',
    'VTU_MOCK_MODE',
    '_compute_js_token',
    '_resolve_vtu_urls',
    'get_headers',
    'get_mock_result',
    'initialize_scrape',
    'complete_scrape',
    'parse_vtu_html',
    'logger'
]
