import os
import sys
import logging
import requests
import xml.etree.ElementTree as ET
from google.oauth2 import service_account
from googleapiclient.discovery import build
import time
from ratelimit import limits, sleep_and_retry
from tqdm import tqdm

# Ρύθμιση βασικού logger για να μην ξαναβγάλει AttributeError
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class GoogleIndexer:
    def __init__(self, credentials_path):
        self.credentials_path = credentials_path
        self.service = self._build_service()

    def _build_service(self):
        if not os.path.exists(self.credentials_path):
            logging.error(f"❌ Το αρχείο διαπιστευτηρίων {self.credentials_path} ΔΕΝ βρέθηκε!")
            sys.exit(1)
        try:
            scopes = ['https://googleapis.com']
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=scopes
            )
            return build('indexing', 'v3', credentials=credentials)
        except Exception as e:
            logging.error(f"❌ Αποτυχία σύνδεσης με το Google API: {str(e)}")
            sys.exit(1)

    def get_links_from_feed(self, feed_url):
        logging.info(f"Ανάγνωση Feed: {feed_url}")
        links = []
        try:
            response = requests.get(feed_url, timeout=15)
            if response.status_code != 200:
                logging.error(f"❌ Σφάλμα HTTP {response.status_code} στο Feed")
                return links
            
            root = ET.fromstring(response.content)
            ns = {'atom': 'http://w3.org'}
            
            for entry in root.findall('atom:entry', ns):
                for link in entry.findall('atom:link', ns):
                    if link.get('rel') == 'alternate' and link.get('type') == 'text/html':
                        links.append(link.get('href'))
        except Exception as e:
            logging.error(f"❌ Αποτυχία ανάλυσης του Feed: {str(e)}")
        return links

    @sleep_and_retry
    @limits(calls=100, period=60)  # Περιορισμός για αποφυγή spamming
    def publish_url(self, url):
        body = {'url': url, 'type': 'URL_UPDATED'}
        try:
            self.service.urlNotifications().publish(body=body).execute()
            logging.info(f"✅ Επιτυχές Index Request: {url}")
            return True
        except Exception as e:
            logging.error(f"❌ Αποτυχία υποβολής για το URL {url}: {str(e)}")
            return False

def main():
    # Διαβάζει το όνομα αρχείου που ορίζει το YAML (service-account-key.json)
    credentials_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'service-account-key.json')
    feed_string = os.environ.get('BLOGGER_RSS_FEED', '')
    
    if not feed_string:
        logging.error("❌ Δεν βρέθηκε η μεταβλητή BLOGGER_RSS_FEED στο YAML")
        sys.exit(1)
        
    indexer = GoogleIndexer(credentials_path)
    
    # Διαχωρισμός των 10 URLs από το YAML με βάση το κόμμα
    feed_urls = [url.strip() for url in feed_string.split(',') if url.strip()]
    all_links = set()
    
    logging.info(f"Βρέθηκαν {len(feed_urls)} Feeds προς επεξεργασία.")
    for feed_url in feed_urls:
        discovered = indexer.get_links_from_feed(feed_url)
        all_links.update(discovered)
        
    unique_links = list(all_links)
    logging.info(f"Συνολικά μοναδικά άρθρα που βρέθηκαν: {len(unique_links)}")
    
    max_requests = int(os.environ.get('MAX_REQUESTS_PER_DAY', 200))
    count = 0
    
    if unique_links:
        logging.info(f"Έναρξη υποβολής στη Google (Όριο: {max_requests})...")
        for url in tqdm(unique_links):
            if count >= max_requests:
                logging.info("🛑 Συμπληρώθηκε το ημερήσιο όριο (quota) αιτημάτων.")
                break
            if indexer.publish_url(url):
                count += 1
                time.sleep(1)

if __name__ == "__main__":
    main()
