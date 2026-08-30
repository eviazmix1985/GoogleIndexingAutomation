import os
import sys
import logging
import xml.etree.ElementTree as ET
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CREDENTIALS_FILE = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service-account-key.json')
FEED_URL = os.getenv('BLOGGER_RSS_FEED')
MAX_REQUESTS = int(os.getenv('MAX_REQUESTS_PER_DAY', 200))

def get_urls_from_feed(feed_url):
    logging.info(f"Ανάγνωση feed από: {feed_url}")
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; GoogleIndexingBot/1.0)'}
    
    try:
        response = requests.get(feed_url, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Αποτυχία λήψης του feed: {e}")
        return []

    urls = []
    try:
        root = ET.fromstring(response.content)
        
        # 1. Έλεγχος για RSS 2.0 (<item><link>)
        for item in root.findall('.//item'):
            link = item.find('link')
            if link is not None and link.text:
                urls.append(link.text.strip())

        # 2. Έλεγχος για Atom (<entry><link href="...">)
        if not urls:
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//atom:entry', ns) or root.findall('.//entry')
            for entry in entries:
                links = entry.findall('atom:link', ns) or entry.findall('link')
                for link in links:
                    if link.get('rel') == 'alternate' or not link.get('rel'):
                        href = link.get('href')
                        if href and href.endswith('.html'):
                            urls.append(href.strip())

        # Αφαίρεση διπλότυπων διατηρώντας τη σειρά
        unique_urls = list(dict.fromkeys(urls))
        logging.info(f"Βρέθηκαν {len(unique_urls)} μοναδικά URLs στο feed.")
        return unique_urls
    except Exception as e:
        logging.error(f"Σφάλμα επεξεργασίας XML: {e}")
        return []

def main():
    if not FEED_URL:
        logging.error("Η μεταβλητή BLOGGER_RSS_FEED δεν έχει οριστεί!")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_FILE):
        logging.error(f"Το αρχείο διαπιστευτηρίων {CREDENTIALS_FILE} ΔΕΝ βρέθηκε!")
        sys.exit(1)

    try:
        scopes = ['https://www.googleapis.com/auth/indexing']
        credentials = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=scopes
        )
        service = build('indexing', 'v3', credentials=credentials)
    except Exception as e:
        logging.error(f"Αποτυχία σύνδεσης με το Google Indexing API: {e}")
        sys.exit(1)

    urls = get_urls_from_feed(FEED_URL)
    if not urls:
        logging.warning("Δεν βρέθηκαν άρθρα προς αποστολή.")
        return

    submitted = 0
    for url in urls[:MAX_REQUESTS]:
        try:
            logging.info(f"Αποστολή URL στο Google: {url}")
            body = {'url': url, 'type': 'URL_UPDATED'}
            service.urlNotifications().publish(body=body).execute()
            logging.info(f"ΕΠΙΤΥΧΙΑ (200 OK): {url}")
            submitted += 1
        except Exception as e:
            logging.error(f"Σφάλμα κατά την αποστολή του {url}: {e}")

    logging.info(f"Ολοκληρώθηκε! Στάλθηκαν συνολικά {submitted} URLs.")

if __name__ == '__main__':
    main()
