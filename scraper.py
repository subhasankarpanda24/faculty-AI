"""
scraper.py — NIST Faculty Scraper
=================================
Strategy:
  1. Start with a HARDCODED list of all 192 NIST faculty members.
  2. Attempt Selenium-based scraping of https://www.nist.edu/faculty
     to fill in missing fields (department, subjects, email, etc.).
  3. If Selenium fails, the hardcoded base records (with N/A defaults)
     are still valid and usable by the rest of the system.
  4. requests + BS4 are used ONLY for individual profile sub-pages.

Dependencies: selenium, webdriver-manager, requests, beautifulsoup4, tqdm
"""

import os
import re
import time
import traceback
from typing import List, Dict

try:
    from tqdm import tqdm
except ImportError:
    # Fallback if tqdm is not installed
    def tqdm(iterable, **kwargs):
        return iterable

try:
    import requests
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    print("[Scraper] WARNING: selenium/webdriver-manager not installed. Using hardcoded data only.")


# ═══════════════════════════════════════════════════════════════
# COMPLETE NIST FACULTY LIST — 192 members (hardcoded)
# ═══════════════════════════════════════════════════════════════

HARDCODED_FACULTY_NAMES = [
    # ─── DOCTORS (Dr.) ───────────────────────────────────────
    "Dr. Brojo Kishore Mishra", "Dr. Hemant Kumar Reddy", "Dr. Santosh Kumar Das",
    "Dr. Sudhir Ranjan Pattanaik", "Dr. Charulata Palai", "Dr. Susmita Mahato",
    "Dr. Umashankar Ghugar", "Dr. Binayak Panda", "Dr. Manjushree Nayak",
    "Dr. Santosh Kumar Kar", "Dr. Pradeep Kumar Jena", "Dr. Bhabani Sankar Gouda",
    "Dr. Roma Sahu", "Dr. Barada Prasad Sethy", "Dr. Bhanu Prasad Behera",
    "Dr. Pramath Nath Acharya", "Dr. Ratnakar Mishra", "Dr. Prabin Kumar Padhy",
    "Dr. Gayatri Panda", "Dr. Akankshya Patnaik", "Dr. Amarnath Padhi",
    "Dr. Tushar Ranjan Sahoo", "Dr. Susanta Kumar Patro", "Dr. Sabyasachi Rath",
    "Dr. Duryodhan Sahu", "Dr. Shrabani Mahata", "Dr. Manabendra Patra",
    "Dr. Sarita Sahu", "Dr. Prasanta Kumar Behera", "Dr. Debashis Panda",
    "Dr. Asish Kumar Mohapatra", "Dr. Ratikanta Nayak", "Dr. Simanchalo Panigrahi",
    "Dr. Namrata Pattanayak", "Dr. Bhaskar Bhaula", "Dr. Deepak Acharya",
    "Dr. Umakanta Mishra", "Dr. Bishnukar Nayak", "Dr. Sahadeb Kuila",
    "Dr. Lakshmi Kanta Raju", "Dr. Subrata Kumar Sahu", "Dr. Chinmay Kumar Giri",
    "Dr. Radhakrushna Sahoo", "Dr. Sasanka Sekhar Bishoyi", "Dr. Lopamudra Das",
    "Dr. Puspanjali Jena", "Dr. Aswini Kumar Khuntia", "Dr. Santosh Kumar Panda",
    "Dr. Souren Misra", "Dr. Prajapati Naik", "Dr. Sushanta Kumar Sahu",
    "Dr. Subrat Kumar Bhuyan", "Dr. Murthy Cherukuri", "Dr. Sasmita Padhy",
    "Dr. Kunjabihari Swain", "Dr. Basant Kumar Sahu", "Dr. Santanu Kumar Pradhan",
    "Dr. Mrutyunjaya Mangaraj", "Dr. Sachidananda Prasad", "Dr. Ashwini Kumar Nayak",
    "Dr. Preeti Ranjan Sahu", "Dr. Abhro Mukherjee", "Dr. Satya Sopan Mahato",
    "Dr. Asit Kumar Panda", "Dr. Swadhin Kumar Mishra", "Dr. Harikrushna Gantayat",
    "Dr. Prashant Kumar Singh", "Dr. Rajesh Kumar Patjoshi", "Dr. Shasanka Sekhar Rout",
    "Dr. Pradyumna Kumar Patra", "Dr. Sandipan Mallik", "Dr. Sudhakar Das",
    "Dr. P. Rajesh Kumar", "Dr. Manoj Kumar Pradhan", "Dr. Amruta Pattnaik",
    "Dr. Bibhudutta Mishra", "Dr. Amit Patnaik", "Dr. Abinash Dutta",
    "Dr. Anwesweta Panigrahi", "Dr. Priyadarsan Patra", "Dr. Ashwini Kumar Behera",
    "Dr. Biswajit Panda", "Dr. Deepak Kumar Swain", "Dr. Debashis Mohanty",
    "Dr. Runu Sahu", "Dr. Susanta Kumar Indrajitsingha", "Dr. Yerra Shankar Rao",
    "Dr. Arun Kumar Marandi", "Dr. Ashalata Panigrahi", "Dr. Manas Ranjan Patra",
    "Dr. Sambit Kumar Shukla", "Dr. Ayesha Tasnim",
    "Dr. Swagat Kumar Samantaray", "Dr. Rankanidhi Sahu", "Dr. Trinath Sahu",
    "Dr. Purna Chandra Biswal", "Dr. Jagannath Panda", "Dr. Debashish Ghose",
    "Dr. Vishwas Chavan", "Dr. Madhusudan Mishra", "Dr. Aashhis Mohanty",

    # ─── MISS ────────────────────────────────────────────────
    "Miss Pratyasha Pradhan", "Miss Akankshya", "Miss Malabika Pattnaik",
    "Miss Swetaleena Panda", "Miss Shubhasri Pradhan",

    # ─── MISTER (Mr.) ────────────────────────────────────────
    "Mr. Gandham Girish", "Mr. G. Vivekananda", "Mr. Asish Kumar Roy",
    "Mr. Prasant Kumar Mohanty", "Mr. Debananda Kanhar", "Mr. Jyoti Ranjan Sahoo",
    "Mr. Asit Kumar Das", "Mr. Anil Kumar Biswal", "Mr. Ashutosh Parida",
    "Mr. Laxminarayan Dash", "Mr. Debasish Padhy", "Mr. Ranjit Kumar Behera",
    "Mr. Kolla Lakshmi Narayana", "Mr. Ashish Kumar Dass", "Mr. Rahul Roy",
    "Mr. Rabindra Kumar Shial", "Mr. Amit Sahoo", "Mr. Surya Narayan Patra",
    "Mr. Sujit Kumar Rout", "Mr. Durgamadhab Padhy", "Mr. Nilamadhaba Panda",
    "Mr. Sumanta Kumar Patanaik", "Mr. M. Rajendra Kumar", "Mr. Sarbeswar Barik",
    "Mr. Kali Prasad Rath", "Mr. Achyutananda Parida", "Mr. Alok Patra",
    "Mr. Md. Riazuddin", "Mr. Suresh Kumar Mohanty", "Mr. Arabinda Panda",
    "Mr. Gubbala Kedarnath", "Mr. Subash Nayak", "Mr. Chittaranjan Biswal",
    "Mr. Praneeth Kumar Pedapati", "Mr. Rakesh Sahoo", "Mr. Nrusingha Prasad Tripathy",
    "Mr. Rakesh Roshan", "Mr. M. Suresh", "Mr. Arttatran Sahu", "Mr. Mitu Baral",
    "Mr. Durga Prasad Dash", "Mr. Purnendu Mishra", "Mr. Satyabrata Das",
    "Mr. Rajesh Kumar Dash", "Mr. Mukesh Kumar Sukla", "Mr. Prabhudatta Pradhan",
    "Mr. Bibhuti Bhusan Mishra", "Mr. Saroj Padhy", "Mr. Amaresh Kumar Mohanty",
    "Mr. B Ujalesh Subudhi Ujal", "Mr. Bandhan Panda", "Mr. Bhabani Prasad Mishra",
    "Mr. Debashis Biswal", "Mr. K Manoj Kumar", "Mr. Manoj Kumar Sahoo",
    "Mr. Prabin Kumar Panigrahi", "Mr. Saubhagya Ranjan Nath",
    "Mr. Santosh Kumar Acharya", "Mr. Manoj Kumar Senapati", "Mr. Somlin Pattanaik",
    "Mr. Sunil Kumar Nahak", "Mr. Rupak Kumar Swain", "Mr. Pitambar Sahu",
    "Mr. Shounak Biswas", "Mr. Ajay Kumar Kedia", "Mr. Jagan Mohan Mahapatro",
    "Mr. Abinash Panigrahy", "Mr. Debendra Maharana", "Mr. Aruna Kumar Samantaray",
    "Mr. Sanjit Kumar Acharya", "Mr. Soumya Ranjan Mohapatra",
    "Mr. N. Tayaad Kumar Reddy", "Mr. Y. Srinivasa Rao",

    # ─── MRS ─────────────────────────────────────────────────
    "Mrs. Manisha Patro", "Mrs. Nibedita Priyadarshini Mohapatra",
    "Mrs. Swetanjali Maharana", "Mrs. Pragnya Das", "Mrs. Kumari Manaswini Padhy",
    "Mrs. Ruchika Padhi", "Mrs. Deepika Rani Sahu", "Mrs. Padminee Samal",
    "Mrs. Pradeepta Biswal", "Mrs. Sangita Kumari Swain", "Mrs. Namita Jena",
    "Mrs. Debabandana Apta", "Mrs. Sukanti Pal", "Mrs. Sasmita Nayak",
    "Mrs. Minakhi Dash", "Mrs. Pragnya Paramita Samanta",
    "Mrs. Kshirabdhi Tanaya Nayak", "Mrs. Sobhana Behera",
    "Mrs. Chandrani Ray Chowdhury",
]

# ═══════════════════════════════════════════════════════════════
# KNOWN DESIGNATIONS & ROLES (hardcoded)
# ═══════════════════════════════════════════════════════════════

KNOWN_DESIGNATIONS = {
    "Dr. Priyadarsan Patra":       {"designation": "Vice Chancellor",                        "email": "vc@nist.edu",        "department": "Administration"},
    "Dr. P. Rajesh Kumar":         {"designation": "Dean of Academics",                      "email": "N/A",                "department": "Administration"},
    "Dr. Brojo Kishore Mishra":    {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Computer Science & Engineering"},
    "Dr. Souren Misra":            {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Mechanical Engineering"},
    "Dr. Barada Prasad Sethy":     {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Civil Engineering"},
    "Dr. Rajesh Kumar Patjoshi":   {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Electronics & Communication Engineering"},
    "Dr. Sasmita Padhy":           {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Electrical Engineering"},
    "Dr. Subrata Kumar Sahu":      {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Mathematics"},
    "Dr. Simanchalo Panigrahi":    {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Physics"},
    "Dr. Duryodhan Sahu":          {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Chemistry"},
    "Dr. Sabyasachi Rath":         {"designation": "Professor & HOD",                        "email": "N/A",                "department": "English"},
    "Dr. Pramath Nath Acharya":    {"designation": "Professor & HOD",                        "email": "N/A",                "department": "MBA / Management"},
    "Dr. Amit Patnaik":            {"designation": "Professor & HOD",                        "email": "N/A",                "department": "Biotechnology"},
}


# ═══════════════════════════════════════════════════════════════
# NAME PARSING
# ═══════════════════════════════════════════════════════════════

def parse_faculty_name(full_name: str) -> Dict:
    """
    Parse a full faculty name into title, first_name, last_name.
    Examples:
      "Dr. Brojo Kishore Mishra" → ("Dr.", "Brojo Kishore", "Mishra")
      "Mr. G. Vivekananda"       → ("Mr.", "G.", "Vivekananda")
      "Miss Akankshya"           → ("Miss", "Akankshya", "")
    """
    title = ""
    name_part = full_name.strip()

    # Extract title prefix
    for prefix in ["Dr.", "Mr.", "Mrs.", "Miss"]:
        if name_part.startswith(prefix):
            title = prefix
            name_part = name_part[len(prefix):].strip()
            break

    parts = name_part.split()
    if len(parts) == 0:
        return {"title": title, "first_name": "", "last_name": ""}
    elif len(parts) == 1:
        return {"title": title, "first_name": parts[0], "last_name": ""}
    else:
        return {"title": title, "first_name": " ".join(parts[:-1]), "last_name": parts[-1]}


def build_base_record(full_name: str) -> Dict:
    """
    Build a base faculty record from a hardcoded name.
    All scraped fields default to "N/A" and are filled in later.
    """
    parsed = parse_faculty_name(full_name)

    # Determine has_phd based on title
    has_phd = parsed["title"] == "Dr."

    # Default designation based on title
    if has_phd:
        default_designation = "Assistant Professor"
    elif parsed["title"] in ("Mr.", "Mrs."):
        default_designation = "Lecturer"
    elif parsed["title"] == "Miss":
        default_designation = "Lecturer"
    else:
        default_designation = "Faculty"

    # Check for known designation overrides
    known = KNOWN_DESIGNATIONS.get(full_name, {})

    record = {
        "name":               full_name,
        "title":              parsed["title"],
        "first_name":         parsed["first_name"],
        "last_name":          parsed["last_name"],
        "designation":        known.get("designation", default_designation),
        "department":         known.get("department", "N/A"),
        "subjects":           "N/A",
        "research_areas":     "N/A",
        "qualification":      "Ph.D." if has_phd else "N/A",
        "experience":         "N/A",
        "room_no":            "N/A",
        "available_days":     "N/A",
        "available_time":     "N/A",
        "consultation_mode":  "N/A",
        "email":              known.get("email", "N/A"),
        "phone":              "N/A",
        "profile_url":        "N/A",
        "photo_url":          "N/A",
        "bio":                "N/A",
        "has_phd":            has_phd,
    }

    # Calculate initial profile completeness
    record["profile_completeness"] = _calc_completeness(record)

    return record


def _calc_completeness(record: Dict) -> int:
    """Calculate profile completeness as a percentage."""
    fields_to_check = [
        "name", "designation", "department", "subjects", "research_areas",
        "qualification", "experience", "room_no", "available_days",
        "available_time", "consultation_mode", "email", "phone", "bio"
    ]
    total = len(fields_to_check)
    filled = sum(1 for f in fields_to_check if record.get(f, "N/A") != "N/A" and record.get(f, "") != "")
    return int((filled / total) * 100)


# ═══════════════════════════════════════════════════════════════
# SELENIUM SCRAPER
# ═══════════════════════════════════════════════════════════════

def _create_driver():
    """Create a headless Chrome driver using webdriver-manager."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        print(f"[Scraper] Failed to create Chrome driver: {e}")
        return None


def scrape_faculty_page(driver) -> List[Dict]:
    """
    Scrape the NIST faculty listing page using Selenium.
    Waits for AJAX content to load, then extracts profile data from DOM.
    Returns a list of dicts with whatever fields can be extracted.
    """
    url = "https://www.nist.edu/faculty"
    scraped = []

    try:
        print(f"[Scraper] Loading {url} ...")
        driver.get(url)

        # Wait for AJAX content — look for faculty cards/links
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='faculty'], .faculty-card, .team-member, .card"))
            )
            print("[Scraper] AJAX content loaded successfully.")
        except Exception:
            print("[Scraper] Timeout waiting for AJAX. Trying DOM anyway...")
            time.sleep(5)  # Give extra time

        # Try multiple selectors that NIST site might use
        faculty_elements = []
        selectors = [
            "a[href*='faculty']",
            ".faculty-card",
            ".team-member",
            ".card",
            ".faculty-item",
            ".member-info",
            "[class*='faculty']",
            "[class*='team']",
        ]

        for selector in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    faculty_elements = elements
                    print(f"[Scraper] Found {len(elements)} elements with selector: {selector}")
                    break
            except Exception:
                continue

        if not faculty_elements:
            # Try getting all links that might be profile pages
            all_links = driver.find_elements(By.TAG_NAME, "a")
            faculty_elements = [
                link for link in all_links
                if any(kw in (link.get_attribute("href") or "").lower()
                       for kw in ["faculty", "profile", "staff", "team"])
            ]
            print(f"[Scraper] Found {len(faculty_elements)} potential profile links.")

        # Extract data from each element
        for elem in tqdm(faculty_elements, desc="Parsing faculty elements"):
            try:
                record = {}

                # Try to get name
                try:
                    name_el = elem.find_element(By.CSS_SELECTOR, "h2, h3, h4, .name, .title, strong")
                    record["name"] = name_el.text.strip()
                except Exception:
                    record["name"] = elem.text.strip().split("\n")[0] if elem.text else ""

                # Try to get department
                try:
                    dept_el = elem.find_element(By.CSS_SELECTOR, ".department, .dept, .subtitle, small")
                    record["department"] = dept_el.text.strip()
                except Exception:
                    pass

                # Try to get profile URL
                try:
                    href = elem.get_attribute("href")
                    if href and "faculty" in href.lower():
                        record["profile_url"] = href
                    else:
                        link = elem.find_element(By.TAG_NAME, "a")
                        record["profile_url"] = link.get_attribute("href")
                except Exception:
                    pass

                # Try to get email
                try:
                    email_el = elem.find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
                    record["email"] = email_el.get_attribute("href").replace("mailto:", "")
                except Exception:
                    pass

                # Try to get designation
                try:
                    desig_el = elem.find_element(By.CSS_SELECTOR, ".designation, .position, .role")
                    record["designation"] = desig_el.text.strip()
                except Exception:
                    pass

                if record.get("name"):
                    scraped.append(record)

                time.sleep(0.3)  # Small delay between elements

            except Exception:
                continue

    except Exception as e:
        print(f"[Scraper] Error scraping faculty page: {e}")
        traceback.print_exc()

    return scraped


def scrape_profile_page(url: str) -> Dict:
    """
    Scrape an individual faculty profile page using requests + BS4.
    Falls back gracefully if page can't be loaded.
    """
    record = {}
    if not BS4_AVAILABLE or not url or url == "N/A":
        return record

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return record

        soup = BeautifulSoup(response.text, "html.parser")

        # Try extracting common profile fields
        try:
            # Email
            email_link = soup.find("a", href=re.compile(r"mailto:"))
            if email_link:
                record["email"] = email_link["href"].replace("mailto:", "").strip()
        except Exception:
            pass

        try:
            # Phone
            phone_link = soup.find("a", href=re.compile(r"tel:"))
            if phone_link:
                record["phone"] = phone_link["href"].replace("tel:", "").strip()
        except Exception:
            pass

        try:
            # Qualification
            for label in soup.find_all(["dt", "th", "strong", "b", "label"]):
                label_text = label.get_text(strip=True).lower()
                value_el = label.find_next_sibling() or label.find_next()
                if not value_el:
                    continue
                value_text = value_el.get_text(strip=True)

                if "qualification" in label_text or "education" in label_text:
                    record["qualification"] = value_text
                elif "experience" in label_text:
                    record["experience"] = value_text
                elif "research" in label_text:
                    record["research_areas"] = value_text
                elif "subject" in label_text or "course" in label_text:
                    record["subjects"] = value_text
                elif "department" in label_text or "dept" in label_text:
                    record["department"] = value_text
                elif "designation" in label_text or "position" in label_text:
                    record["designation"] = value_text
                elif "room" in label_text or "cabin" in label_text or "office" in label_text:
                    record["room_no"] = value_text
        except Exception:
            pass

        try:
            # Bio / About
            for heading in soup.find_all(["h2", "h3", "h4"]):
                if any(kw in heading.get_text(strip=True).lower() for kw in ["about", "bio", "profile", "summary"]):
                    next_p = heading.find_next_sibling("p")
                    if next_p:
                        record["bio"] = next_p.get_text(strip=True)
                    break
        except Exception:
            pass

        try:
            # Photo
            img = soup.find("img", {"class": re.compile(r"profile|photo|avatar|faculty", re.I)})
            if img and img.get("src"):
                record["photo_url"] = img["src"]
        except Exception:
            pass

    except Exception as e:
        print(f"[Scraper] Error scraping profile {url}: {e}")

    return record


# ═══════════════════════════════════════════════════════════════
# MERGE LOGIC — Combine hardcoded base with scraped data
# ═══════════════════════════════════════════════════════════════

def _normalize_name(name: str) -> str:
    """Normalize a name for matching: lowercase, remove titles, extra spaces."""
    name = name.lower().strip()
    for prefix in ["dr.", "mr.", "mrs.", "miss", "prof.", "professor"]:
        name = name.replace(prefix, "")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def merge_scraped_data(base_records: List[Dict], scraped_data: List[Dict]) -> List[Dict]:
    """
    Merge scraped data into the hardcoded base records.
    Match by normalized name. Only overwrite N/A fields.
    """
    # Build lookup by normalized name
    scraped_lookup = {}
    for item in scraped_data:
        name = _normalize_name(item.get("name", ""))
        if name:
            scraped_lookup[name] = item

    merged_count = 0
    for record in base_records:
        norm_name = _normalize_name(record["name"])

        # Try exact match first
        scraped = scraped_lookup.get(norm_name)

        # Try partial match (last name + first name substring)
        if not scraped:
            for sn, sv in scraped_lookup.items():
                if record["last_name"].lower() in sn and record["first_name"].lower().split()[0] in sn:
                    scraped = sv
                    break

        if scraped:
            merged_count += 1
            # Only fill in N/A fields — never overwrite a known value
            for field in ["department", "subjects", "research_areas", "qualification",
                          "experience", "room_no", "available_days", "available_time",
                          "consultation_mode", "email", "phone", "profile_url",
                          "photo_url", "bio", "designation"]:
                if record.get(field, "N/A") == "N/A" and scraped.get(field) and scraped[field] != "N/A":
                    record[field] = scraped[field]

            # Recalculate completeness
            record["profile_completeness"] = _calc_completeness(record)

    print(f"[Scraper] Merged scraped data for {merged_count}/{len(base_records)} faculty members.")
    return base_records


# ═══════════════════════════════════════════════════════════════
# MAIN SCRAPING FUNCTION
# ═══════════════════════════════════════════════════════════════

def scrape_nist_faculty() -> List[Dict]:
    """
    Main entry point: build base records from hardcoded list,
    attempt Selenium scraping, merge results, return enriched records.
    """
    # Step 1: Build base records from hardcoded names
    print(f"[Scraper] Building base records for {len(HARDCODED_FACULTY_NAMES)} faculty members...")
    base_records = []
    for name in tqdm(HARDCODED_FACULTY_NAMES, desc="Building base records"):
        base_records.append(build_base_record(name))

    # Step 2: Attempt Selenium scraping
    scraped_data = []
    if SELENIUM_AVAILABLE:
        print("[Scraper] Starting Selenium scraper...")
        driver = _create_driver()
        if driver:
            try:
                scraped_data = scrape_faculty_page(driver)
                print(f"[Scraper] Scraped {len(scraped_data)} faculty entries from web.")

                # Step 3: Scrape individual profile pages for richer data
                profile_urls = [s.get("profile_url") for s in scraped_data if s.get("profile_url")]
                if profile_urls and BS4_AVAILABLE:
                    print(f"[Scraper] Scraping {len(profile_urls)} individual profile pages...")
                    for i, url in enumerate(tqdm(profile_urls, desc="Scraping profiles")):
                        try:
                            profile_data = scrape_profile_page(url)
                            if profile_data:
                                # Find matching scraped record and merge
                                for s in scraped_data:
                                    if s.get("profile_url") == url:
                                        s.update({k: v for k, v in profile_data.items() if v and v != "N/A"})
                                        break
                            time.sleep(1)  # 1 second delay between requests
                        except Exception:
                            continue

            except Exception as e:
                print(f"[Scraper] Selenium scraping failed: {e}")
                traceback.print_exc()
            finally:
                try:
                    driver.quit()
                except Exception:
                    pass
        else:
            print("[Scraper] Could not create Chrome driver. Using hardcoded data only.")
    else:
        print("[Scraper] Selenium not available. Using hardcoded data only.")

    # Step 4: Merge scraped data with base records
    if scraped_data:
        base_records = merge_scraped_data(base_records, scraped_data)
    else:
        print("[Scraper] No scraped data to merge. Using hardcoded data as-is.")

    # Final summary
    total = len(base_records)
    complete = sum(1 for r in base_records if r["profile_completeness"] >= 70)
    partial = sum(1 for r in base_records if 30 <= r["profile_completeness"] < 70)
    minimal = total - complete - partial

    print(f"\n{'='*60}")
    print(f"[Scraper] SCRAPING COMPLETE")
    print(f"  Total faculty: {total}")
    print(f"  Complete profiles (≥70%): {complete}")
    print(f"  Partial profiles (30-69%): {partial}")
    print(f"  Minimal profiles (<30%): {minimal}")
    print(f"{'='*60}\n")

    return base_records


# ═══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    records = scrape_nist_faculty()
    print(f"\nTotal records: {len(records)}")
    for r in records[:5]:
        print(f"  {r['name']} | {r['designation']} | {r['department']} | Completeness: {r['profile_completeness']}%")
    print("  ...")
