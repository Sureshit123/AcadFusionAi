import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import base64
import logging
import random
import re
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Base URLs - VTU MJ26 CBCS exam result portal
VTU_BASE = "https://results.vtu.ac.in/MJ26cbcs"
VTU_INDEX = f"{VTU_BASE}/index.php"
VTU_RESULT = f"{VTU_BASE}/resultpage.php"
VTU_SITE_ROOT = "https://results.vtu.ac.in"

# Mock Mode Configuration
VTU_MOCK_MODE = os.environ.get('VTU_MOCK_MODE', 'false').lower() == 'true'

def _compute_js_token():
    """
    Replicates the JavaScript onsubmit token:
      js_token = btoa('student_access_' + new Date().getFullYear())
    """
    year = datetime.now().year
    raw = f"student_access_{year}"
    return base64.b64encode(raw.encode()).decode()

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

def get_mock_result(usn):
    """Generates a realistic mock result for testing."""
    subjects = {
        "21CS51": {"name": "Database Management System", "internal": random.randint(30, 50), "external": random.randint(18, 60)},
        "21CS52": {"name": "Computer Networks", "internal": random.randint(30, 50), "external": random.randint(18, 60)},
        "21CS53": {"name": "Full Stack Development", "internal": random.randint(45, 50), "external": random.randint(35, 60)},
        "21CS54": {"name": "Analysis & Design of Algorithms", "internal": random.randint(20, 50), "external": random.randint(0, 60)},
    }
    
    for code, s in subjects.items():
        s["total"] = s["internal"] + s["external"]
        # Use our custom logic
        if 50 < s["internal"] <= 100: s["result"] = "P"
        elif s["internal"] <= 50 and s["external"] < 18: s["result"] = "F"
        else: s["result"] = "P" if s["total"] >= 40 else "F"

    return {
        "usn": usn,
        "name": f"Mock Student {usn[-3:]}",
        "status": "Pass" if all(s["result"] == "P" for s in subjects.values()) else "Fail",
        "total_marks": sum(s["total"] for s in subjects.values()),
        "max_marks": len(subjects) * 100,
        "sgpa": round(random.uniform(6.0, 9.5), 2),
        "subjects": subjects
    }

def initialize_scrape(usn, retries=3, mock=None, session=None):
    """
    Step 1: Starts a session, fetches the index page, extracts the hidden token
    and captures the captcha image. Includes retries for VTU server instability.
    Returns: (session, base64_captcha, hidden_token, error_msg)
    """
    is_mock = mock if mock is not None else VTU_MOCK_MODE
    if is_mock:
        # A valid 1x1 transparent PNG base64 string
        mock_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        return (session if session else "MOCK_SESSION"), mock_b64, {"name": "MockToken", "value": "123"}, None

    import time
    if session is None:
        session = requests.Session()
        session.verify = False
        session.headers.update(get_headers())

        # Configure retries with exponential backoff for transient errors (including SSL/EOF)
        retry_strategy = Retry(
            total=5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
            backoff_factor=1,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    
    last_err = None
    for attempt in range(retries):
        try:
            # Timeout made configurable via environment var, default 30s
            default_timeout = int(os.environ.get('VTU_REQUEST_TIMEOUT', '30'))
            res = session.get(VTU_INDEX, timeout=default_timeout)
            res.raise_for_status()

            # Decode with utf-8-sig to strip the BOM character (\ufeff) VTU sends
            html_content = res.content.decode('utf-8-sig', errors='replace')
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 1. Find ALL Hidden Tokens from the form
            token_dict = {}
            hidden_inputs = soup.find_all('input', type='hidden')
            for inp in hidden_inputs:
                if inp.get('name'):
                    token_dict[inp.get('name')] = inp.get('value', '')

            # 2. Inject the JS-generated token (set via onsubmit in the browser):
            #    js_token = btoa('student_access_' + new Date().getFullYear())
            token_dict['js_token'] = _compute_js_token()
            logger.info(f"js_token computed: {token_dict['js_token']}")
            logger.info(f"Token dict keys: {list(token_dict.keys())}")

            # 3. Extract Captcha Image URL
            #    The captcha src starts with /captcha/... (absolute from site root)
            captcha_img = soup.find('img', src=lambda s: s and 'captcha' in s.lower())
            if not captcha_img:
                return session, None, None, "No captcha image found on VTU site. Site structure may have changed."

            from urllib.parse import urljoin
            raw_captcha_src = captcha_img['src']
            # If src starts with '/', resolve relative to site root, not subdirectory
            if raw_captcha_src.startswith('/'):
                captcha_url = VTU_SITE_ROOT + raw_captcha_src
            else:
                captcha_url = urljoin(VTU_INDEX, raw_captcha_src)
            logger.info(f"Captcha URL: {captcha_url}")
            
            # 4. Download Captcha Image (Increased timeout due to VTU server load)
            captcha_res = session.get(captcha_url, timeout=default_timeout)
            captcha_res.raise_for_status()
            
            b64_captcha = base64.b64encode(captcha_res.content).decode('utf-8')
            
            # Return the required state for Part 2
            return session, b64_captcha, token_dict, None
            
        except requests.exceptions.SSLError as e:
            last_err = str(e)
            logger.warning(f"SSL error on attempt {attempt+1} for {usn}: {e}")
            time.sleep(2 ** attempt)
            continue
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = str(e)
            logger.warning(f"Timeout/Connection error on attempt {attempt+1} for {usn}: {e}")
            time.sleep(2 ** attempt)
            continue
        except Exception as e:
            last_err = str(e)
            logger.warning(f"Error on attempt {attempt+1} for {usn}: {e}")
            time.sleep(2) # Wait before retry
            
    logger.error(f"Failed to initialize scrape for {usn} after {retries} attempts: {last_err}")
    return None, None, None, last_err


def complete_scrape(usn, session, token_dict, captcha_text, mock=None):
    """
    Step 2: Submits the form with the captcha code and parses the result.
    """
    is_mock = mock if mock is not None else VTU_MOCK_MODE
    if is_mock:
        return get_mock_result(usn)

    session.headers.update({
        'Referer': VTU_INDEX,
        'Origin': VTU_BASE
    })

    payload = {
        'lns': usn,
        'captchacode': captcha_text
    }
    
    # Add hidden token(s) if found
    if isinstance(token_dict, dict):
        payload.update(token_dict)
    elif isinstance(token_dict, tuple) or hasattr(token_dict, 'get'):
        if token_dict.get('name'):
            payload[token_dict['name']] = token_dict['value']
    
    # Always ensure js_token is present (computed same way as browser onsubmit)
    payload['js_token'] = _compute_js_token()
    
    logger.info(f"Submitting for {usn} with payload keys: {list(payload.keys())}")
    
    # Use same default timeout as initialize
    default_timeout = int(os.environ.get('VTU_REQUEST_TIMEOUT', '30'))
    last_err = None
    for attempt in range(3):
        try:
            res = session.post(VTU_RESULT, data=payload, timeout=default_timeout)
            
            # Decode with utf-8-sig to handle VTU BOM character
            res_text = res.content.decode('utf-8-sig', errors='replace')

            # Debug dump (always write for debugging)
            try:
                with open('latest_result.html', 'w', encoding='utf-8') as f:
                    f.write(res_text)
            except Exception:
                pass

            # If captcha is wrong, VTU usually returns to index with an alert
            if "Invalid captcha" in res_text or "Invalid Captch" in res_text:
                 return {"usn": usn, "status": "Invalid Captcha"}
                 
            if "Redirecting" in res_text:
                 return {"usn": usn, "status": "Busy/Redirect"}
                 
            if "Direct access" in res_text or "Direct API" in res_text:
                 logger.warning(f"Direct access blocked for {usn}. Response snippet: {res_text[:200]}")
                 return {"usn": usn, "status": "Direct Access Blocked"}
                 
            if "Invalid USN" in res_text or "not available" in res_text.lower():
                 return {"usn": usn, "status": "Invalid/No Res"}

            return parse_vtu_html(usn, res_text)

        except requests.exceptions.SSLError as e:
            last_err = str(e)
            logger.warning(f"SSL error on post attempt {attempt+1} for {usn}: {e}")
            time.sleep(2 ** attempt)
            continue
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = str(e)
            logger.warning(f"Timeout/Connection error on post attempt {attempt+1} for {usn}: {e}")
            time.sleep(2 ** attempt)
            continue
        except Exception as e:
            last_err = str(e)
            logger.error(f"Failed to complete scrape for {usn}: {e}")
            break

    return {"usn": usn, "status": "Network/Parse Error", "error": last_err}


def parse_vtu_html(usn, html_content):
    """
    Dynamically extracts student details and all subjects from VTU result HTML.
    Updated to support both table-based and div-based VTU result structures.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    result = {
        "usn": usn,
        "name": "Unknown",
        "total_marks": 0,
        "max_marks": 0,
        "sgpa": 0.0,
        "status": "No Res",
        "subjects": {}
    }
    
    try:
        # Extract Name - Support both <td> and <div class="divTableCell"> layouts
        name_found = False
        
        # Try td-based layout (old VTU format)
        tds = soup.find_all('td')
        for i, td in enumerate(tds):
            text = td.get_text(strip=True).upper()
            if "STUDENT NAME" in text and i + 1 < len(tds):
                raw_name = tds[i+1].get_text(strip=True).title()
                result["name"] = raw_name.lstrip(': ').strip()
                name_found = True
                break
        
        # Try div-based layout (new VTU format)
        if not name_found:
            divs = soup.find_all('div', class_='divTableCell')
            for i, div in enumerate(divs):
                text = div.get_text(strip=True).upper()
                if "STUDENT NAME" in text and i + 1 < len(divs):
                    raw_name = divs[i+1].get_text(strip=True)
                    result["name"] = raw_name.lstrip(': ').strip()
                    name_found = True
                    break

        # Extract Subjects dynamically
        rows = soup.find_all('tr') + soup.find_all('div', class_='divTableRow')
        for row in rows:
            if row.name == 'div':
                cols = row.find_all('div', class_='divTableCell')
            else:
                cols = row.find_all(['td', 'th'])
                
            col_texts = [c.get_text(strip=True) for c in cols]
            
            if len(col_texts) >= 5:
                sub_code = ""
                internal_str = "0"
                external_str = "0"
                total_str = "0"
                res_flag = ""
                
                # New VTU format: [Code, Name, Internal, External, Total, Result, ...]
                if len(col_texts[0]) >= 5 and any(c.isalpha() for c in col_texts[0]) and any(c.isdigit() for c in col_texts[0]) and 'SUBJECT' not in col_texts[0].upper():
                    sub_code = col_texts[0]
                    sub_name = col_texts[1] if len(col_texts) > 1 else ""
                    internal_str = col_texts[2] if len(col_texts) > 2 else "0"
                    external_str = col_texts[3] if len(col_texts) > 3 else "0"
                    total_str = col_texts[4] if len(col_texts) > 4 else "0"
                    res_flag = col_texts[5] if len(col_texts) > 5 else ""
                
                # Old VTU format: [Serial, Code, Name, ..., Total, Result]
                elif len(col_texts[1]) >= 5 and any(c.isalpha() for c in col_texts[1]) and any(c.isdigit() for c in col_texts[1]) and 'SUBJECT' not in col_texts[1].upper():
                    sub_code = col_texts[1]
                    sub_name = col_texts[2] if len(col_texts) > 2 else ""
                    if len(col_texts) >= 7:
                        internal_str = col_texts[-4]
                        external_str = col_texts[-3]
                    total_str = col_texts[-2]
                    res_flag = col_texts[-1]
                
                if sub_code:
                    try:
                        tot_val = int(total_str)
                        int_val = int(internal_str)
                        ext_val = int(external_str)
                    except ValueError:
                        tot_val = 0
                        int_val = 0
                        ext_val = 0
                        
                    # Custom Result Logic
                    final_res = res_flag.upper()
                    if 50 < int_val <= 100:
                        final_res = "P"
                    elif int_val <= 50 and ext_val < 18 and res_flag.upper() in ['P', 'PASS']:
                        final_res = "F"

                    result["subjects"][sub_code] = {
                        "name": sub_name,
                        "internal": int_val,
                        "external": ext_val,
                        "total": tot_val,
                        "result": final_res
                    }
             
        # Extract Semester Total/SGPA (if present on the page)
        sgpa_matches = re.findall(r'SGPA[\s:]*([0-9.]+)', html_content, re.IGNORECASE)
        if sgpa_matches:
             result["sgpa"] = float(sgpa_matches[0])
             
        # Calculate derived metrics
        if result["subjects"]:
            result["total_marks"] = sum(s["total"] for s in result["subjects"].values())
            result["max_marks"] = len(result["subjects"]) * 100
            
            if any(s["result"] in ['F', 'A', 'FAIL', 'ABSENT'] for s in result["subjects"].values()):
                result["status"] = "Fail"
            else:
                result["status"] = "Pass"
            
            if result["max_marks"] > 0:
                result["percentage"] = round((result["total_marks"] / result["max_marks"]) * 100, 2)
            else:
                result["percentage"] = 0
        else:
            result["status"] = "No Res"
                
    except Exception as e:
        logger.error(f"Error parsing HTML for {usn}: {e}")
        result["status"] = "Parse Error"
        
    return result
