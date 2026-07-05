"""
91mobiles.com scraper - FINAL working version.

Confirmed from your testing:
  - Real pagination endpoint:
      GET https://www.91mobiles.com/api/v1/guest/category/products-with-filters
      with startRow=0, 20, 40, ... (offset-based, 20 phones/page)
  - The JSON response has a top-level key 'products', but that key's VALUE
    is itself an HTML STRING (not JSON) containing the same
    <article class="listing">...</article> blocks as the original
    phonefinder.php page.
  - So: fetch JSON -> pull out data['products'] -> parse THAT string with
    BeautifulSoup exactly like the original scraper did.

This reuses the icon-class-based spec extraction (fixes the earlier
MediaTek Dimensity issue) and guarantees no null cells in the final CSV.
"""

import requests
from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin

API_ENDPOINT = "https://www.91mobiles.com/api/v1/guest/category/products-with-filters"

BASE_PARAMS = {
    'catId': 553,
    'currentPath': '/phonefinder.php',
    'pageType': 'ListPage',
    'srtBy': 'ga_views',
    'srtType': 'desc',
    'filters[rngFl][product_status.price.wap][]': '0-300000',
    'popularBrands': 'false',
    'isSliderRange': 1,
    'isCustomPageAuto': 'false',
}

PAGE_SIZE = 20

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': 'https://www.91mobiles.com/phonefinder.php',
}

FIELDNAMES = [
    'name', 'url', 'release_date', 'spec_score', 'image_url',
    'processor', 'ram_storage', 'rear_camera', 'front_camera',
    'battery', 'display', 'antutu_score', 'awards',
    'user_rating', 'expert_rating', 'price', 'store'
]

ICON_CLASS_MAP = {
    'icn_performance': 'processor',
    'icn_ram': 'ram_storage',
    'icn_memory': 'ram_storage',
    'icn_camera_rear': 'rear_camera',
    'icn_camera': 'rear_camera',
    'icn_camera_front': 'front_camera',
    'icn_selfie': 'front_camera',
    'icn_battery': 'battery',
    'icn_display': 'display',
    'icn_score': 'antutu_score',
    'icn_antutu': 'antutu_score',
}

CHIPSET_KEYWORDS = [
    'Snapdragon', 'Processor', 'MediaTek', 'Dimensity', 'Exynos',
    'Helio', 'Bionic', 'Tensor', 'Kirin', 'Unisoc', 'Tiger'
]


def get_spec_value(li):
    title = li.get('title', '').strip()
    return title if title else li.get_text(strip=True)


def classify_spec(li, mobile_data):
    classes = li.get('class', []) or []
    text = get_spec_value(li)
    if not text:
        return
    for cls in classes:
        if cls in ICON_CLASS_MAP:
            key = ICON_CLASS_MAP[cls]
            if not mobile_data.get(key):
                mobile_data[key] = text
            return
    if any(k in text for k in CHIPSET_KEYWORDS) and not mobile_data.get('processor'):
        mobile_data['processor'] = text
    elif 'RAM' in text and ('Storage' in text or 'ROM' in text) and not mobile_data.get('ram_storage'):
        mobile_data['ram_storage'] = text
    elif 'Rear Camera' in text and not mobile_data.get('rear_camera'):
        mobile_data['rear_camera'] = text
    elif 'Front Camera' in text and not mobile_data.get('front_camera'):
        mobile_data['front_camera'] = text
    elif 'mAh' in text and not mobile_data.get('battery'):
        mobile_data['battery'] = text
    elif ('inches' in text or 'Display' in text) and not mobile_data.get('display'):
        mobile_data['display'] = text
    elif 'AnTuTu' in text and not mobile_data.get('antutu_score'):
        mobile_data['antutu_score'] = text


def parse_article(article):
    mobile_data = {}

    name_tag = article.find('h2', class_='check-closest')
    if not name_tag:
        return None
    mobile_data['name'] = name_tag.get_text(strip=True)

    link_tag = name_tag.find('a')
    mobile_data['url'] = (
        urljoin('https://www.91mobiles.com', link_tag['href'])
        if link_tag and link_tag.get('href') else ''
    )

    release_date_tag = article.find('span', class_='rl-date')
    mobile_data['release_date'] = (
        release_date_tag.get_text(strip=True).replace('Release Date: ', '')
        if release_date_tag else ''
    )

    spec_score_tag = article.find('div', class_='prd_score')
    mobile_data['spec_score'] = spec_score_tag.get_text(strip=True) if spec_score_tag else ''

    img_tag = article.find('img', class_='prd-im')
    mobile_data['image_url'] = (img_tag.get('src') or img_tag.get('data-src') or '') if img_tag else ''

    spec_list = article.find('ul', class_='spec_hgt')
    if spec_list:
        for li in spec_list.find_all('li'):
            classify_spec(li, mobile_data)

    awards = []
    award_section = article.find('div', class_='award_col')
    if award_section:
        for award in award_section.find_all('a', class_='icn_award'):
            awards.append(award.get_text(strip=True))
    mobile_data['awards'] = ', '.join(awards)

    star_tags = article.find_all('span', class_='icn_star')
    mobile_data['user_rating'] = (
        star_tags[0].get_text(strip=True).split('(')[0].strip() if len(star_tags) > 0 else ''
    )
    mobile_data['expert_rating'] = star_tags[1].get_text(strip=True) if len(star_tags) > 1 else ''

    store_div = article.find('div', class_='store_dtl')
    if store_div:
        price_tag = store_div.find('span', class_='store_prc')
        mobile_data['price'] = price_tag.get_text(strip=True) if price_tag else ''
        store_name_tag = store_div.find('div', class_='store_nme')
        if store_name_tag:
            store_img = store_name_tag.find('img')
            if store_img and store_img.get('alt'):
                mobile_data['store'] = store_img['alt']
            else:
                store_text = store_name_tag.get_text(strip=True)
                mobile_data['store'] = ''.join(
                    c for c in store_text if not c.isdigit() and c not in '₹$€'
                ).strip()
        else:
            mobile_data['store'] = ''
    else:
        mobile_data['price'] = ''
        mobile_data['store'] = ''

    for field in FIELDNAMES:
        mobile_data.setdefault(field, '')
    return mobile_data


def fetch_batch(session, start_row):
    params = dict(BASE_PARAMS)
    params['startRow'] = start_row
    resp = session.get(API_ENDPOINT, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    html_fragment = payload.get('products', '')
    total_products = payload.get('total_products')

    print(f"  -> startRow={start_row}  [{resp.status_code}]  "
          f"html_len={len(html_fragment)}  total_products={total_products}")

    soup = BeautifulSoup(html_fragment, 'html.parser')
    articles = soup.find_all('article', class_='listing')

    results = []
    for article in articles:
        parsed = parse_article(article)
        if parsed:
            results.append(parsed)

    return results, total_products


def fetch_mobile_data(target_count=4000, delay_range=(1.0, 2.0)):
    session = requests.Session()
    all_data = []
    seen_urls = set()
    empty_streak = 0
    start_row = 0
    known_total = None

    while len(all_data) < target_count:
        try:
            batch, total_products = fetch_batch(session, start_row)
        except requests.RequestException as e:
            print(f"startRow={start_row}: request failed ({e}), retrying once...")
            time.sleep(5)
            try:
                batch, total_products = fetch_batch(session, start_row)
            except requests.RequestException as e2:
                print(f"startRow={start_row}: failed again ({e2}), stopping.")
                break

        if total_products is not None:
            try:
                known_total = int(str(total_products).replace(',', '').strip())
            except ValueError:
                known_total = None

        new_count = 0
        for item in batch:
            if not item['url'] or item['url'] in seen_urls:
                continue
            seen_urls.add(item['url'])
            all_data.append(item)
            new_count += 1

        print(f"    +{new_count} new phones (total collected: {len(all_data)}"
              f"{f' / {known_total} available' if known_total else ''})")

        if new_count == 0:
            empty_streak += 1
            if empty_streak >= 3:
                print("No new phones for 3 consecutive batches - stopping.")
                break
        else:
            empty_streak = 0

        if known_total is not None and len(all_data) >= known_total:
            print(f"Reached the site's total of {known_total} available phones - stopping.")
            break

        start_row += PAGE_SIZE
        time.sleep(random.uniform(*delay_range))

    return all_data[:target_count]


def clean_no_nulls(data):
    cleaned = []
    for row in data:
        clean_row = {}
        for field in FIELDNAMES:
            value = row.get(field, '')
            if value is None:
                value = 'N/A'
            else:
                value = str(value).strip()
                if value == '' or value.lower() in ('nan', 'none', 'null'):
                    value = 'N/A'
            clean_row[field] = value
        cleaned.append(clean_row)
    return cleaned


def save_to_csv(data, filename='mobiles_data.csv'):
    if not data:
        print("No data to save.")
        return
    data = clean_no_nulls(data)
    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(data)
    print(f"Saved {len(data)} rows with no null values to {filename}")


def main():
    print("Fetching mobile phone data from 91mobiles.com (products-with-filters API)...")
    mobile_data = fetch_mobile_data(target_count=4000)
    if mobile_data:
        print(f"Successfully fetched data for {len(mobile_data)} mobile phones.")
        save_to_csv(mobile_data)
    else:
        print("No mobile phone data found.")


if __name__ == "__main__":
    main()