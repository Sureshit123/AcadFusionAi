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
import time
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

def _resolve_vtu_urls(custom_url=None):
    """
    Parses a user-provided custom VTU result link or falls back to default URLs.
    Returns: (index_url, result_url, site_root)
    """
    if not custom_url or not str(custom_url).strip():
        return VTU_INDEX, VTU_RESULT, VTU_SITE_ROOT

    url = str(custom_url).strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    from urllib.parse import urlparse
    parsed = urlparse(url)
    site_root = f"{parsed.scheme}://{parsed.netloc}"

    path = parsed.path
    if path.endswith('.php'):
        base_dir = path.rsplit('/', 1)[0]
    else:
        base_dir = path.rstrip('/')

    index_url = f"{site_root}{base_dir}/index.php" if not path.endswith('index.php') else url
    result_url = f"{site_root}{base_dir}/resultpage.php"

    return index_url, result_url, site_root

def get_headers(referer=None, origin=None):
    from urllib.parse import urlparse
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': referer or VTU_INDEX,
    }
    parsed = urlparse(origin or VTU_BASE)
    headers['Origin'] = f"{parsed.scheme}://{parsed.netloc}"
    return headers

def get_mock_result(usn, mock_subjects=None):
    """Generates a realistic mock result for testing."""
    if mock_subjects:
        subjects = {}
        for code, name in mock_subjects.items():
            _name_upper = name.upper()
            _is_internal = any(kw in _name_upper for kw in [
                'YOGA', 'ENVIRONMENTAL', 'INTERNSHIP', 'MINI PROJECT',
                'CONSTITUTION', 'SCIENCE AND SOCIETY'
            ])
            if _is_internal:
                subjects[code] = {
                    "name": name,
                    "internal": random.randint(40, 100),
                    "external": 0,
                    "is_internal_only": True
                }
            else:
                subjects[code] = {
                    "name": name,
                    "internal": random.randint(25, 50),
                    "external": random.randint(15, 60),
                    "is_internal_only": False
                }
        for code, s in subjects.items():
            is_internal = s.get('is_internal_only', False)
            if is_internal:
                s["total"] = s["internal"]
                s["result"] = "P" if s["total"] >= 40 else "F"
            else:
                s["total"] = s["internal"] + s["external"]
                if 50 < s["internal"] <= 100:
                    s["result"] = "P"
                elif s["internal"] <= 50 and s["external"] < 18:
                    s["result"] = "F"
                else:
                    s["result"] = "P" if s["total"] >= 40 else "F"
    else:
        subjects = {
            "21CS51": {"name": "Database Management System", "internal": random.randint(30, 50), "external": random.randint(18, 60)},
            "21CS52": {"name": "Computer Networks", "internal": random.randint(30, 50), "external": random.randint(18, 60)},
            "21CS53": {"name": "Full Stack Development", "internal": random.randint(45, 50), "external": random.randint(35, 60)},
            "21CS54": {"name": "Analysis & Design of Algorithms", "internal": random.randint(20, 50), "external": random.randint(0, 60)},
        }
        for code, s in subjects.items():
            s["total"] = s["internal"] + s["external"]
            if 50 < s["internal"] <= 100:
                s["result"] = "P"
            elif s["internal"] <= 50 and s["external"] < 18:
                s["result"] = "F"
            else:
                s["result"] = "P" if s["total"] >= 40 else "F"

    return {
        "usn": usn,
        "name": f"Mock Student {usn[-3:]}",
        "status": "Pass" if all(s["result"] == "P" for s in subjects.values()) else "Fail",
        "total_marks": sum(s["total"] for s in subjects.values()),
        "max_marks": len(subjects) * 100,
        "sgpa": round(random.uniform(6.0, 9.5), 2),
        "subjects": subjects
    }

def initialize_scrape(usn, retries=3, mock=None, session=None, vtu_url=None):
    """
    Step 1: Starts a session, fetches the index page, extracts hidden tokens,
    and captures the captcha image. Supports custom VTU result links.
    Returns: (session, base64_captcha, hidden_token, error_msg)
    """
    is_mock = mock if mock is not None else VTU_MOCK_MODE
    if is_mock:
        mock_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        return (session if session else "MOCK_SESSION"), mock_b64, {"name": "MockToken", "value": "123"}, None

    vtu_index, vtu_result, vtu_site_root = _resolve_vtu_urls(vtu_url)

    if session is None:
        session = requests.Session()
        session.verify = False
        session.headers.update(get_headers(referer=vtu_index, origin=vtu_index))

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
            default_timeout = int(os.environ.get('VTU_REQUEST_TIMEOUT', '30'))
            res = session.get(vtu_index, timeout=default_timeout)
            res.raise_for_status()

            html_content = res.content.decode('utf-8-sig', errors='replace')
            soup = BeautifulSoup(html_content, 'html.parser')

            # 1. Find ALL Hidden Tokens from the form
            token_dict = {}
            hidden_inputs = soup.find_all('input', type='hidden')
            for inp in hidden_inputs:
                if inp.get('name'):
                    token_dict[inp.get('name')] = inp.get('value', '')

            # 2. Inject computed tokens & URLs
            token_dict['js_token'] = _compute_js_token()
            token_dict['_vtu_result_url'] = vtu_result
            token_dict['_vtu_index_url'] = vtu_index
            logger.info(f"js_token computed: {token_dict['js_token']}")

            # 3. Extract Captcha Image URL
            captcha_img = soup.find('img', src=lambda s: s and 'captcha' in s.lower())
            if not captcha_img:
                return session, None, None, "No captcha image found on VTU site. Site structure may have changed."

            from urllib.parse import urljoin
            raw_captcha_src = captcha_img['src']
            if raw_captcha_src.startswith('/'):
                captcha_url = vtu_site_root + raw_captcha_src
            else:
                captcha_url = urljoin(vtu_index, raw_captcha_src)
            logger.info(f"Captcha URL: {captcha_url}")

            # 4. Download Captcha Image
            captcha_res = session.get(captcha_url, timeout=default_timeout)
            captcha_res.raise_for_status()

            b64_captcha = base64.b64encode(captcha_res.content).decode('utf-8')

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
            time.sleep(2)

    logger.error(f"Failed to initialize scrape for {usn} after {retries} attempts: {last_err}")
    return None, None, None, last_err


def complete_scrape(usn, session, token_dict, captcha_text, mock=None, mock_subjects=None, result_type='regular', vtu_url=None):
    """
    Step 2: Submits the form with the captcha code and parses the result.
    Applies Result Type Scraping rules (Regular, Re-evaluation, Make-up).
    """
    is_mock = mock if mock is not None else VTU_MOCK_MODE
    if is_mock:
        return get_mock_result(usn, mock_subjects)

    vtu_index, vtu_result, vtu_site_root = _resolve_vtu_urls(vtu_url)
    if isinstance(token_dict, dict) and token_dict.get('_vtu_result_url'):
        vtu_result = token_dict['_vtu_result_url']
    if isinstance(token_dict, dict) and token_dict.get('_vtu_index_url'):
        vtu_index = token_dict['_vtu_index_url']

    if hasattr(session, 'headers'):
        session.headers.update({
            'Referer': vtu_index,
            'Origin': vtu_site_root
        })

    payload = {
        'lns': usn,
        'captchacode': captcha_text
    }

    if isinstance(token_dict, dict):
        for k, v in token_dict.items():
            if not k.startswith('_'):
                payload[k] = v
    elif isinstance(token_dict, (tuple, list)) or hasattr(token_dict, 'get'):
        if hasattr(token_dict, 'get') and token_dict.get('name'):
            payload[token_dict['name']] = token_dict.get('value', '')

    payload['js_token'] = _compute_js_token()

    logger.info(f"Submitting for {usn} (type={result_type}) with payload keys: {list(payload.keys())}")

    default_timeout = int(os.environ.get('VTU_REQUEST_TIMEOUT', '30'))
    last_err = None
    for attempt in range(3):
        try:
            res = session.post(vtu_result, data=payload, timeout=default_timeout)
            res_text = res.content.decode('utf-8-sig', errors='replace')

            try:
                with open('latest_result.html', 'w', encoding='utf-8') as f:
                    f.write(res_text)
            except Exception:
                pass

            if "Invalid captcha" in res_text or "Invalid Captch" in res_text:
                return {"usn": usn, "status": "Invalid Captcha"}

            if "Redirecting" in res_text:
                return {"usn": usn, "status": "Busy/Redirect"}

            if "Direct access" in res_text or "Direct API" in res_text:
                logger.warning(f"Direct access blocked for {usn}. Response snippet: {res_text[:200]}")
                return {"usn": usn, "status": "Direct Access Blocked"}

            if "Invalid USN" in res_text or "not available" in res_text.lower():
                return {"usn": usn, "status": "Invalid/No Res"}

            return parse_vtu_html(usn, res_text, result_type=result_type)

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


def _extract_subject_rows_from_container(container):
    """
    Extracts subject dictionary from a container element (such as a table or div section).
    """
    subjects = {}
    rows = container.find_all('tr') + container.find_all('div', class_=lambda c: c and 'divTableRow' in c)
    if not rows and container.name in ['tr', 'div']:
        rows = [container]

    for row in rows:
        if row.name == 'div' or 'divTableRow' in str(row.get('class', '')):
            cols = row.find_all('div', class_=lambda c: c and 'divTableCell' in c)
        else:
            cols = row.find_all(['td', 'th'])

        col_texts = [c.get_text(strip=True) for c in cols]

        if len(col_texts) >= 5:
            sub_code = ""
            sub_name = ""
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
            elif len(col_texts) > 1 and len(col_texts[1]) >= 5 and any(c.isalpha() for c in col_texts[1]) and any(c.isdigit() for c in col_texts[1]) and 'SUBJECT' not in col_texts[1].upper():
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

                final_res = res_flag.upper()
                is_internal_only = (ext_val == 0 and int_val > 0 and final_res in ['P', 'PASS'])

                if 50 < int_val <= 100:
                    final_res = "P"
                elif int_val <= 50 and ext_val < 18 and res_flag.upper() in ['P', 'PASS'] and not is_internal_only:
                    final_res = "F"

                subjects[sub_code] = {
                    "name": sub_name,
                    "internal": int_val,
                    "external": ext_val,
                    "total": tot_val,
                    "result": final_res,
                    "is_internal_only": is_internal_only
                }

    return subjects


def parse_vtu_html(usn, html_content, result_type='regular'):
    """
    Dynamically extracts student details and subject tables from VTU result HTML.
    
    Result Type Rules:
    - Regular: Scrapes ONLY the first/main semester result table on the marks card.
      All additional backlog/previous semester tables are ignored.
    - Re-evaluation & Make-up: Scrapes ALL available semester tables on the marks card.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    res_type = str(result_type).strip().lower()
    is_regular = res_type in ['regular', 'reg']

    result = {
        "usn": usn,
        "name": "Unknown",
        "total_marks": 0,
        "max_marks": 0,
        "sgpa": 0.0,
        "status": "No Res",
        "subjects": {},
        "semester_tables": []
    }

    try:
        # 1. Extract Student Name - Support both <td> and <div class="divTableCell"> layouts
        name_found = False
        cells = soup.find_all(class_=lambda c: c and 'divTableCell' in c) or soup.find_all('td')
        for i, cell in enumerate(cells):
            text = cell.get_text(strip=True).upper()
            if text in ["STUDENT NAME", "STUDENT NAME:", "STUDENT NAME :"] or (text.startswith("STUDENT NAME") and len(text) < 35):
                if i + 1 < len(cells):
                    raw_val = cells[i+1].get_text(strip=True).lstrip(': ').strip()
                    if raw_val and raw_val.upper() not in ["UNIVERSITY SEAT NUMBER", "STUDENT NAME"]:
                        result["name"] = raw_val.title()
                        name_found = True
                        break

        if not name_found:
            for el in soup.find_all(['td', 'div', 'span', 'b', 'strong']):
                if len(el.find_all()) <= 1:
                    text = el.get_text(strip=True).upper()
                    if "STUDENT NAME" in text and len(text) < 35:
                        next_el = el.find_next_sibling() or (el.parent and el.parent.find_next_sibling())
                        if next_el:
                            val = next_el.get_text(strip=True).lstrip(': ').strip()
                            if val and val.upper() not in ["UNIVERSITY SEAT NUMBER", "STUDENT NAME"]:
                                result["name"] = val.title()
                                name_found = True
                                break

        # 2. Dynamic Semester Table Detection
        parsed_semesters = []

        table_elements = soup.find_all('div', class_=lambda c: c and 'divTable' in c)
        if not table_elements:
            table_elements = soup.find_all('table')

        for idx, tbl in enumerate(table_elements):
            subs = _extract_subject_rows_from_container(tbl)
            if not subs:
                continue

            # Detect associated Semester heading
            sem_num = None
            prev_text = ""
            curr = tbl.previous_element
            for _ in range(30):
                if curr is None:
                    break
                if isinstance(curr, str):
                    prev_text = curr.strip() + " " + prev_text
                    sem_match = re.search(r'Semester\s*:\s*(\d+)', prev_text, re.IGNORECASE)
                    if sem_match:
                        sem_num = int(sem_match.group(1))
                        break
                curr = curr.previous_element

            if not sem_num:
                parent_text = tbl.parent.get_text() if tbl.parent else ""
                sem_match = re.search(r'Semester\s*:\s*(\d+)', parent_text, re.IGNORECASE)
                if sem_match:
                    sem_num = int(sem_match.group(1))

            if not sem_num:
                sem_num = idx + 1

            parsed_semesters.append({
                "semester": sem_num,
                "subjects": subs,
                "table_index": idx
            })

        # Fallback if no specific table tags matched
        if not parsed_semesters:
            all_subs = _extract_subject_rows_from_container(soup)
            if all_subs:
                parsed_semesters.append({
                    "semester": 1,
                    "subjects": all_subs,
                    "table_index": 0
                })

        # 3. Apply Result Type Scraping Rule:
        # - Regular: Scrape ONLY the first/main semester table appearing on the marks card.
        # - Re-evaluation / Make-up: Scrape ALL available semester result tables.
        if is_regular:
            selected_tables = parsed_semesters[:1] if parsed_semesters else []
        else:
            selected_tables = parsed_semesters

        result["semester_tables"] = selected_tables

        # Combine subjects from selected tables
        combined_subjects = {}
        for st in selected_tables:
            for scode, sdata in st["subjects"].items():
                combined_subjects[scode] = sdata

        result["subjects"] = combined_subjects

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
