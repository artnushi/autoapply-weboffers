import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
COUNTRIES = [
    ("CH", "Switzerland", ["cleaning company", "dentist", "plumber", "landscaping"]),
    ("DE", "Germany", ["cleaning service", "dentist", "plumber", "roofing company"]),
    ("AT", "Austria", ["cleaning company", "dentist", "electrician", "landscaping"]),
    ("NL", "Netherlands", ["cleaning company", "plumber"]),
    ("IE", "Ireland", ["cleaning company", "landscaping"]),
]
DIRECTORY_HINTS = (
    "local.ch", "search.ch", "gelbeseiten.de", "11880.com", "herold.at",
    "dasoertliche.de", "yelp.", "facebook.com", "instagram.com", "linkedin.com",
    "firmenabc.at", "goldenpages.ie", "yell.com", "pagesjaunes.fr"
)
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "AutoApplyWebOffers/0.1 (manual-review lead research)"})


def env_required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError("Missing required environment variable: " + name)
    return value


def search(api_key, query, count=10):
    response = HTTP.get(
        BRAVE_ENDPOINT,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": count, "search_lang": "en"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("web", {}).get("results", [])


def is_directory(url):
    host = (urlparse(url).hostname or "").lower()
    return any(hint in host for hint in DIRECTORY_HINTS)


def inspect_site(url):
    try:
        response = HTTP.get(url, timeout=10, allow_redirects=True)
        if "text/html" not in response.headers.get("content-type", ""):
            return {"url": response.url, "status_code": response.status_code, "reachable": response.status_code < 400}
        html = response.text[:500000]
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title_text = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
        old_copyright = bool(re.search(r"copyright.{0,30}(?:201[0-8]|2019)", html, re.I))
        has_viewport = bool(re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I))
        return {
            "url": response.url,
            "status_code": response.status_code,
            "title": title_text[:180],
            "old_copyright_signal": old_copyright,
            "viewport_signal": has_viewport,
            "reachable": response.status_code < 400,
        }
    except requests.RequestException as exc:
        return {"url": url, "reachable": False, "error": str(exc)[:180]}


def supabase(method, base_url, key, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.update({
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    return HTTP.request(method, base_url, headers=headers, timeout=20, **kwargs)


def main():
    brave_key = env_required("BRAVE_SEARCH_API_KEY")
    supabase_url = env_required("SUPABASE_URL").rstrip("/")
    supabase_key = env_required("SUPABASE_SERVICE_ROLE_KEY")
    daily_limit = min(5, max(1, int(os.getenv("DAILY_LIMIT", "5"))))
    leads_url = supabase_url + "/rest/v1/web_offer_leads"
    runs_url = supabase_url + "/rest/v1/web_offer_daily_runs"
    today = datetime.now(timezone.utc).date().isoformat()
    report = []
    saved = 0

    run_response = supabase(
        "POST", runs_url, supabase_key,
        params={"on_conflict": "run_date"},
        headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        json={"run_date": today, "status": "started", "leads_found": 0},
    )
    run_response.raise_for_status()
    rows = run_response.json()
    run_id = rows[0]["id"] if rows else None

    try:
        seen_domains = set()
        for code, country, industries in COUNTRIES:
            for industry in industries:
                if saved >= daily_limit:
                    break
                query = f"{industry} {country} contact local business directory"
                try:
                    results = search(brave_key, query, count=10)
                except Exception as exc:
                    report.append(f"- Search failed for {query}: {exc}")
                    continue
                for result in results:
                    if saved >= daily_limit:
                        break
                    result_url = result.get("url", "")
                    title = re.sub(r"\s+", " ", result.get("title", "")).strip()
                    description = re.sub(r"\s+", " ", result.get("description", "")).strip()
                    if not result_url or not title or not description or not is_directory(result_url):
                        continue
                    host = (urlparse(result_url).hostname or "").lower()
                    domain = re.sub(r"^www\.", "", host)
                    if not domain or domain in seen_domains:
                        continue
                    seen_domains.add(domain)
                    business_name = re.split(r"\s+[|–-]\s+", title)[0].strip()[:180]

                    direct_results = search(brave_key, f'"{business_name}" official website {country}', count=5)
                    websites = [
                        item for item in direct_results
                        if item.get("url") and not is_directory(item["url"])
                    ]
                    site_url = websites[0]["url"] if websites else None
                    site_check = inspect_site(site_url) if site_url else None
                    if site_check and site_check.get("reachable") and not (
                        site_check.get("old_copyright_signal") or not site_check.get("viewport_signal")
                    ):
                        continue

                    evidence = [
                        "Directory result title: " + title,
                        "Directory result description: " + description,
                        "Automated assessment is heuristic; manually verify before outreach.",
                    ]
                    if site_check:
                        evidence.append("Website check: " + str(site_check))
                    else:
                        evidence.append("No non-directory official website surfaced; this does not prove none exists.")

                    payload = {
                        "business_name": business_name,
                        "country_code": code,
                        "industry": industry,
                        "website_url": site_url,
                        "domain": domain,
                        "website_status": (
                            "broken" if site_check and not site_check.get("reachable")
                            else "outdated" if site_check and site_check.get("reachable")
                            else "missing" if not site_url
                            else "unknown"
                        ),
                        "evidence": "\n".join(evidence),
                        "source_urls": [result_url] + ([site_url] if site_url else []),
                        "contact_method": "unknown",
                        "outreach_eligibility": "manual_review",
                        "status": "new",
                        "notes": "Discovery only. Verify business identity, website quality, and lawful contact basis manually.",
                    }
                    try:
                        inserted = supabase(
                            "POST", leads_url, supabase_key,
                            params={"on_conflict": "domain"},
                            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
                            json=payload,
                        )
                        inserted.raise_for_status()
                        if not inserted.json():
                            continue
                        saved += 1
                        line = "- **" + business_name + "** (" + country + ", " + industry + ") — " + payload["website_status"]
                        line += " — [source](" + result_url + ")"
                        if site_url:
                            line += " — [website](" + site_url + ")"
                        report.append(line)
                    except Exception as exc:
                        report.append("- Could not save candidate " + business_name + ": " + str(exc))
                    time.sleep(0.25)
            if saved >= daily_limit:
                break

        report_text = "\n".join(report) if report else "No new candidates met the initial heuristics today."
        print("## Daily website prospect discovery — " + today)
        print("\nSaved new candidates: " + str(saved) + "\n")
        print(report_text)
        if run_id:
            update = supabase(
                "PATCH", runs_url, supabase_key,
                params={"id": "eq." + run_id},
                json={"status": "completed", "leads_found": saved, "report_markdown": report_text, "finished_at": datetime.now(timezone.utc).isoformat()},
            )
            update.raise_for_status()
    except Exception as exc:
        if run_id:
            supabase(
                "PATCH", runs_url, supabase_key,
                params={"id": "eq." + run_id},
                json={"status": "failed", "error_summary": str(exc)[:500], "finished_at": datetime.now(timezone.utc).isoformat()},
            )
        raise


if __name__ == "__main__":
    main()
