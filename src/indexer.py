import os
import re
import sys
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CREDENTIALS_FILE = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service-account-key.json')
FEED_URL = os.getenv('BLOGGER_RSS_FEED')
MAX_REQUESTS = int(os.getenv('MAX_REQUESTS_PER_DAY', 200))
LOOKBACK_HOURS = float(os.getenv('LOOKBACK_HOURS', 24))
FORCE_REINDEX = os.getenv('FORCE_REINDEX', 'false').strip().lower() == 'true'

ATOM = '{http://www.w3.org/2005/Atom}'


def parse_iso(value):
    """Ημερομηνία ISO 8601 (π.χ. 2026-09-30T12:15:33.123+03:00 ή ...Z) -> UTC."""
    s = value.strip().replace('Z', '+00:00')
    m = re.match(r'^(.+T\d{2}:\d{2}:\d{2})(\.\d+)?(.*)$', s)
    if m:
        frac = m.group(2) or ''
        if frac:
            frac = frac[:7].ljust(7, '0')  # η Google δίνει έως 9 δεκαδικά, η Python θέλει 6
        s = m.group(1) + frac + m.group(3)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_rfc822(value):
    """Ημερομηνία RSS (π.χ. Wed, 30 Sep 2026 09:12:00 +0000) -> UTC."""
    dt = parsedate_to_datetime(value.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def safe(parser, value):
    if not value:
        return None
    try:
        return parser(value)
    except Exception:
        return None


def get_posts_from_feed(feed_url):
    """Επιστρέφει λίστα (url, ώρα τελευταίας αλλαγής) από το feed του Blogger."""
    logging.info(f"Ανάγνωση feed από: {feed_url}")
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; GoogleIndexingBot/1.0)'}

    try:
        response = requests.get(feed_url, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Αποτυχία λήψης του feed: {e}")
        return []

    posts = {}

    def add(url, dates):
        dates = [d for d in dates if d is not None]
        changed = max(dates) if dates else None
        if url in posts and posts[url] is not None and changed is not None:
            changed = max(changed, posts[url])
        posts[url] = changed

    try:
        root = ET.fromstring(response.content)

        # 1. RSS 2.0: <item><link>, <pubDate>, <atom:updated>
        for item in root.iter('item'):
            link = item.findtext('link')
            if link and link.strip():
                add(link.strip(), [
                    safe(parse_rfc822, item.findtext('pubDate')),
                    safe(parse_iso, item.findtext(ATOM + 'updated')),
                ])

        # 2. Atom: <entry><link rel="alternate" href="...">, <published>, <updated>
        if not posts:
            for entry in root.iter(ATOM + 'entry'):
                url = None
                for link in entry.findall(ATOM + 'link'):
                    href = link.get('href')
                    if link.get('rel') in (None, 'alternate') and href and href.endswith('.html'):
                        url = href.strip()
                        break
                if url:
                    add(url, [
                        safe(parse_iso, entry.findtext(ATOM + 'published')),
                        safe(parse_iso, entry.findtext(ATOM + 'updated')),
                    ])
    except Exception as e:
        logging.error(f"Σφάλμα επεξεργασίας XML: {e}")
        return []

    logging.info(f"Βρέθηκαν {len(posts)} μοναδικά URLs στο feed.")
    return list(posts.items())


def already_notified(service, url, changed):
    """
    Ρωτά τη Google αν το URL έχει ήδη σταλεί μετά την τελευταία του αλλαγή.
    Ο έλεγχος αυτός ΔΕΝ χρεώνεται στο ημερήσιο όριο των 200 αποστολών.
    Επιστρέφει True (ήδη σταλμένο), False (πρέπει να σταλεί) ή None (άγνωστο).
    """
    try:
        meta = service.urlNotifications().getMetadata(url=url).execute()
    except HttpError as e:
        if e.resp.status == 404:
            return False  # δεν έχει σταλεί ποτέ
        logging.warning(f"Δεν ήταν δυνατός ο έλεγχος για {url} ({e.resp.status}). Θα ξαναδοκιμαστεί στην επόμενη εκτέλεση.")
        return None
    except Exception as e:
        logging.warning(f"Δεν ήταν δυνατός ο έλεγχος για {url}: {e}. Θα ξαναδοκιμαστεί στην επόμενη εκτέλεση.")
        return None

    notify_time = safe(parse_iso, (meta.get('latestUpdate') or {}).get('notifyTime'))
    if notify_time is None:
        return False
    return notify_time >= changed


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

    posts = get_posts_from_feed(FEED_URL)
    if not posts:
        logging.warning("Δεν βρέθηκαν άρθρα στο feed.")
        return

    no_date = sum(1 for _, changed in posts if changed is None)
    if no_date:
        logging.warning(f"{no_date} άρθρα χωρίς αναγνώσιμη ημερομηνία παραλείπονται.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    recent = [(url, changed) for url, changed in posts if changed is not None and changed >= cutoff]
    logging.info(f"{len(recent)} άρθρα δημοσιεύτηκαν ή άλλαξαν τις τελευταίες {LOOKBACK_HOURS:g} ώρες.")
    if FORCE_REINDEX:
        logging.info("FORCE_REINDEX=true: αποστολή χωρίς έλεγχο αν έχουν ήδη σταλεί.")

    submitted = 0
    skipped = 0
    for url, changed in recent:
        if submitted >= MAX_REQUESTS:
            logging.warning(f"Έφτασε το όριο των {MAX_REQUESTS} αποστολών για αυτή την εκτέλεση.")
            break

        if not FORCE_REINDEX:
            status = already_notified(service, url, changed)
            if status is None:
                continue
            if status:
                skipped += 1
                logging.info(f"Ήδη σταλμένο, παραλείπεται: {url}")
                continue

        try:
            logging.info(f"Αποστολή URL στο Google: {url}")
            body = {'url': url, 'type': 'URL_UPDATED'}
            service.urlNotifications().publish(body=body).execute()
            logging.info(f"ΕΠΙΤΥΧΙΑ (200 OK): {url}")
            submitted += 1
        except HttpError as e:
            if e.resp.status == 429:
                logging.error("Εξαντλήθηκε το ημερήσιο όριο της Google (429). "
                              "Σταματάω εδώ. Τα υπόλοιπα θα σταλούν σε επόμενη εκτέλεση.")
                break
            logging.error(f"Σφάλμα κατά την αποστολή του {url}: {e}")
        except Exception as e:
            logging.error(f"Σφάλμα κατά την αποστολή του {url}: {e}")

    logging.info(f"Ολοκληρώθηκε! Στάλθηκαν {submitted} νέα/ενημερωμένα URLs, "
                 f"παραλείφθηκαν {skipped} που είχαν ήδη σταλεί.")


if __name__ == '__main__':
    # 1. Εκτέλεση του κανονικού script για τα άρθρα
    main()
    
    # 2. Χειροκίνητη αποστολή της αρχικής σελίδας αμέσως μετά
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        import os
        
        blog_url = os.getenv('BLOG_URL')
        credentials_file = os.getenv('GOOGLE_APPLICATION_CREDENTIALS', 'service-account-key.json')
        
        if blog_url and os.path.exists(credentials_file):
            scopes = ['https://googleapis.com']
            creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=scopes)
            service = build('indexing', 'v3', credentials=creds)
            
            body = {'url': blog_url, 'type': 'URL_UPDATED'}
            service.urlNotifications().publish(body=body).execute()
            print(f"SUCCESS: Initialized and sent homepage {blog_url} to Google Indexing API.")
    except Exception as e:
        print(f"Error indexing homepage: {e}")
