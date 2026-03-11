"""
run_scraper.py — Run the NIST faculty scraper and generate Excel output
=======================================================================
Usage: python run_scraper.py

Steps:
  1. Scrape NIST faculty (Selenium + hardcoded fallback)
  2. Write formatted Excel workbook
  3. Print summary report
"""

import sys
import os
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    """Run the full scraping → Excel pipeline."""
    print("=" * 65)
    print("  NIST Faculty Scraper & Excel Generator")
    print("=" * 65)
    print()

    start_time = time.time()

    # Step 1: Scrape faculty data
    print("[Step 1/2] Scraping faculty data...")
    print("-" * 40)

    try:
        from scraper import scrape_nist_faculty
        records = scrape_nist_faculty()
    except Exception as e:
        print(f"\n❌ Scraping failed: {e}")
        print("Please check your internet connection and try again.")
        sys.exit(1)

    if not records:
        print("\n❌ No faculty records were generated.")
        sys.exit(1)

    # Step 2: Write to Excel
    print()
    print("[Step 2/2] Writing Excel workbook...")
    print("-" * 40)

    try:
        from excel_writer import write_faculty_excel
        output_path = write_faculty_excel(records)
    except Exception as e:
        print(f"\n❌ Excel writing failed: {e}")
        sys.exit(1)

    # Step 3: Print summary report
    elapsed = time.time() - start_time

    total = len(records)
    complete = sum(1 for r in records if r.get("profile_completeness", 0) >= 70)
    partial = sum(1 for r in records if 30 <= r.get("profile_completeness", 0) < 70)
    minimal = total - complete - partial

    # Count by title
    dr_count = sum(1 for r in records if r.get("title") == "Dr.")
    mr_count = sum(1 for r in records if r.get("title") == "Mr.")
    mrs_count = sum(1 for r in records if r.get("title") == "Mrs.")
    miss_count = sum(1 for r in records if r.get("title") == "Miss")

    # Count departments
    depts = {}
    for r in records:
        dept = r.get("department", "N/A")
        depts[dept] = depts.get(dept, 0) + 1

    print()
    print("=" * 65)
    print("  📊 SUMMARY REPORT")
    print("=" * 65)
    print(f"  ✅ Total faculty scraped:      {total}")
    print(f"  📗 Complete profiles (≥70%):   {complete}")
    print(f"  📙 Partial profiles (30-69%):  {partial}")
    print(f"  📕 Minimal profiles (<30%):    {minimal}")
    print()
    print(f"  👨‍🏫 Doctors (Dr.):              {dr_count}")
    print(f"  👨 Misters (Mr.):              {mr_count}")
    print(f"  👩 Mrs.:                       {mrs_count}")
    print(f"  👩 Miss:                       {miss_count}")
    print()
    print("  🏢 Departments:")
    for dept, count in sorted(depts.items(), key=lambda x: x[1], reverse=True):
        print(f"     {dept}: {count}")
    print()
    print(f"  📁 Output: {output_path}")
    print(f"  ⏱  Time: {elapsed:.1f}s")
    print("=" * 65)
    print()
    print("  ✅ Done! You can now run: python app.py")
    print()


if __name__ == "__main__":
    main()
