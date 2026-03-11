import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
import os
import time

def crawl_faculty():
    base_url = "https://www.nist.edu/"
    ajax_url = base_url + "faculty/forms/getdptdata.php"
    
    # 1. Get all faculty links
    print("Fetching all faculty profiles...")
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "T": "1",
        "FacultyGroupID": "0"
    }
    
    r = requests.post(ajax_url, headers=headers, data=data)
    if r.status_code != 200:
        print(f"Failed to fetch faculty list. Status: {r.status_code}")
        return
        
    soup = BeautifulSoup(r.text, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        if 'faculty-profile.php' in a['href']:
            links.append(a['href'])
            
    # Deduplicate links
    unique_links = list(set(links))
    print(f"Found {len(unique_links)} faculty profiles to crawl.")
    
    faculty_data = []
    
    # 2. Crawl each profile
    for i, link in enumerate(unique_links):
        url = base_url + link
        print(f"Crawling {i+1}/{len(unique_links)}: {url}")
        
        try:
            r_prof = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r_prof.status_code != 200:
                print(f"  Failed with {r_prof.status_code}")
                continue
                
            prof_soup = BeautifulSoup(r_prof.text, 'html.parser')
            
            # Basic info
            name_el = prof_soup.select_one(".faculty-name")
            name = name_el.text.strip() if name_el else ""
            
            title_el = prof_soup.select_one(".faculty-title")
            title = title_el.text.strip() if title_el else ""
            
            dept_el = prof_soup.select_one(".faculty-department")
            dept = dept_el.text.strip() if dept_el else ""
            
            # Contact info
            email = ""
            phone = ""
            cabin = ""
            
            contact_items = prof_soup.select(".contact-item")
            for item in contact_items:
                i_class = item.find('i').get('class', []) if item.find('i') else []
                span = item.find('span')
                val = span.text.strip() if span else ""
                
                if val and val != "-":
                    if 'fa-envelope' in i_class:
                        email = val
                    elif 'fa-phone' in i_class:
                        phone = val
                    elif 'fa-map-marker-alt' in i_class:
                        cabin = val
                        
            # Education (latest degree)
            qualification = ""
            edu_items = prof_soup.select("#education .education-item")
            if edu_items:
                deg_el = edu_items[0].select_one(".degree")
                if deg_el:
                    qualification = deg_el.text.strip()
            
            # Experience
            experience = ""
            exp_items = prof_soup.select("#experience .education-item")
            exp_list = []
            for item in exp_items:
                deg_el = item.select_one(".degree")
                if deg_el:
                    exp_list.append(deg_el.text.strip())
            if exp_list:
                experience = ", ".join(exp_list)
            else:
                exp_p = prof_soup.select_one("#experience p")
                if exp_p and "No experience" not in exp_p.text:
                    experience = exp_p.text.strip()
            
            # Research Areas
            research = ""
            res_items = prof_soup.select("#research .interest-list li")
            res_list = []
            for item in res_items:
                val = item.text.strip()
                if "No research" not in val and val:
                    res_list.append(val)
            research = ", ".join(res_list) if res_list else ""
            
            faculty_data.append({
                "Name": name,
                "Designation": title,
                "Department": dept,
                "Qualification": qualification,
                "Experience": experience,
                "Core Subjects": "", # Not explicitly found in the HTML usually
                "Research Areas": research,
                "Available Time": "",
                "Available Days": "",
                "Cabin": cabin,
                "Email": email,
                "Phone": phone,
                "Consultation Modes": "Email, In-person", # default
                "Profile Summary": f"URL: {url}"
            })
            
            time.sleep(0.1) # Be polite
            
        except Exception as e:
            print(f"  Error crawling {link}: {e}")
            
    # 3. Save to Excel format
    df = pd.DataFrame(faculty_data)
    
    # Fill N/As
    df.fillna("N/A", inplace=True)
    df.replace("", "N/A", inplace=True)
    
    os.makedirs("output", exist_ok=True)
    excel_path = "output/NIST_Faculty_Directory.xlsx"
    df.to_excel(excel_path, sheet_name="All Faculty", index=False)
    print(f"\\nSuccessfully saved {len(faculty_data)} faculty records to {excel_path}")

if __name__ == "__main__":
    crawl_faculty()
