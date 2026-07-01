import requests, csv, os, time, random, logging, re, json
from bs4 import BeautifulSoup
from datetime import datetime

BATCH_NAME    = 'BATCH8'
TARGET_BRANDS = ['Doogee', 'Ulefone', 'Verykool', 'Panasonic']
DELAY_MIN     = 25
DELAY_MAX     = 60
LONG_PAUSE_EVERY = 30
LONG_PAUSE_MIN   = 120
LONG_PAUSE_MAX   = 300
MAX_PER_SESSION  = 500
MAX_RETRIES      = 2
OUTPUT_FOLDER = f'GSMArenaDataset_{BATCH_NAME}'
MASTER_CSV    = f'GSMArena_{BATCH_NAME}.csv'
RETRY_FILE    = f'retry_queue_{BATCH_NAME}.json'
PROGRESS_FILE = f'progress_{BATCH_NAME}.json'
LOG_FILE      = f'scraper_{BATCH_NAME}.log'
BASE_URL      = 'https://www.gsmarena.com/'

CSV_COLUMNS = [
    'Brand', 'Model_Name', 'Model_URL', 'Model_Image',
    'Network_Technology', '5G_Support',
    'Announced', 'Status',
    'Dimensions', 'Weight_g', 'Back_Material', 'IP_Rating', 'SIM_Type',
    'Display_Type', 'Refresh_Rate_Hz', 'Display_Size_inch',
    'Resolution', 'PPI_Density', 'Screen_to_Body_Pct', 'Display_Protection',
    'OS', 'Chipset', 'Process_Node_nm', 'CPU', 'GPU',
    'RAM_GB', 'RAM_Max_GB', 'Storage_GB', 'Storage_Max_GB', 'Storage_Type',
    'Main_Camera_MP', 'Lens_Count', 'Main_Aperture', 'OIS',
    'Sensor_Size', 'Camera_4K_Video', 'Camera_Features', 'Camera_Video',
    'Selfie_Camera_MP', 'Selfie_4K_Video', 'Selfie_Camera_Features', 'Selfie_Camera_Video',
    'WiFi', 'NFC', 'Bluetooth_Version', 'USB_Type', 'Headphone_Jack', 'GPS',
    'Sensors',
    'Battery_mAh', 'Wired_Charging_W', 'Wireless_Charging_W',
    'Reverse_Wireless_Charging', 'Battery_ActiveUse',
    'Price_USD', 'Price_EUR', 'Price_GBP', 'Price_INR',
    'AnTuTu_Score', 'GeekBench_Score', 'ThreeDMark_Score',
    'Display_nits_tested', 'Loudspeaker_LUFS',
    'Colors', 'Model_Numbers', 'SAR_US', 'SAR_EU', 'FM_Radio',
]

DATA_SPEC = {
    'nettech':'Network_Technology', 'year':'Announced', 'status':'Status',
    'dimensions':'Dimensions', 'weight':'_weight', 'build':'_build',
    'sim':'SIM_Type', 'bodyother':'_bodyother', 'displaytype':'Display_Type',
    'displaysize':'_displaysize', 'displayresolution':'_displayres',
    'displayprotection':'_displayprot', 'os':'OS', 'chipset':'Chipset',
    'cpu':'CPU', 'gpu':'GPU', 'internalmemory':'_internalmem',
    'memoryother':'Storage_Type', 'cam1modules':'_cam1',
    'cam1features':'Camera_Features', 'cam1video':'Camera_Video',
    'cam2modules':'_cam2', 'cam2features':'Selfie_Camera_Features',
    'cam2video':'Selfie_Camera_Video', 'wlan':'WiFi', 'bluetooth':'_bluetooth',
    'gps':'GPS', 'nfc':'_nfc', 'radio':'FM_Radio', 'usb':'_usb',
    'sensors':'Sensors', 'batdescription1':'_battery', 'batlife2':'Battery_ActiveUse',
    'colors':'Colors', 'models':'Model_Numbers', 'sar-us':'SAR_US',
    'sar-eu':'SAR_EU', 'price':'_price', 'tbench':'_tbench',
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8'), logging.StreamHandler()]
)
log = logging.getLogger(__name__)
request_count = 0

def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': BASE_URL,
        'DNT': '1',
    })
    return s

SESSION = make_session()

def wait():
    global request_count
    request_count += 1
    if request_count == 1:
        return
    if request_count % LONG_PAUSE_EVERY == 0:
        p = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        log.info(f"  Long pause {p:.0f}s ...")
        time.sleep(p)
        return
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    log.info(f"  Waiting {t:.1f}s (req #{request_count})")
    time.sleep(t)

def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    json.dump(data, open(path, 'w'), indent=2)

def fetch(url, allow_skip=True):
    global SESSION
    if not url.startswith('http'):
        url = BASE_URL + url
    if request_count > 0 and request_count % 50 == 0:
        SESSION = make_session()
    for attempt in range(1, MAX_RETRIES + 1):
        wait()
        try:
            r = SESSION.get(url, timeout=30)
            log.info(f"  GET {url[:85]} -> {r.status_code}")
            if r.status_code == 200:
                return BeautifulSoup(r.text, 'lxml')
            elif r.status_code == 429:
                if allow_skip and attempt == MAX_RETRIES:
                    log.warning("  429 - SKIPPING")
                    return None
                b = random.uniform(90, 180)
                log.warning(f"  429 - sleeping {b:.0f}s ...")
                time.sleep(b)
                SESSION = make_session()
            elif r.status_code == 403:
                log.error("  403 - blocked")
                return None
        except requests.exceptions.Timeout:
            log.warning(f"  Timeout attempt {attempt}")
        except Exception as e:
            log.error(f"  Error: {e}")
            time.sleep(20)
    return None

def g(specs, key):
    return specs.get(key, '') or ''

def mp(raw):
    if not raw:
        return None
    m = re.findall(r'(\d+(?:\.\d+)?)\s*MP', raw, re.I)
    return float(m[0]) if m else None

def num(s, pat, cast=float):
    if not s:
        return None
    m = re.search(pat, s)
    try:
        return cast(m.group(1).replace(',', '')) if m else None
    except:
        return None

def parse_memory(raw):
    if not raw:
        return None, None, None, None

    def to_gb(val, unit):
        val = float(val)
        return round(val / 1024, 3) if unit.upper() == 'MB' else val

    # Match: Storage RAM pairs e.g. '256GB 8GB RAM' or '4GB 512MB RAM'
    pattern = r'(\d+(?:\.\d+)?)\s*(GB|MB)\s+(\d+(?:\.\d+)?)\s*(GB|MB)\s+RAM'
    matches = re.findall(pattern, raw, re.I)

    ram_vals  = []
    stor_vals = []

    if matches:
        for stor_val, stor_unit, ram_val, ram_unit in matches:
            stor_vals.append(to_gb(stor_val, stor_unit))
            ram_vals.append(to_gb(ram_val, ram_unit))
    else:
        # RAM only e.g. '512MB RAM' or '1GB RAM'
        for ram_val, ram_unit in re.findall(r'(\d+(?:\.\d+)?)\s*(GB|MB)\s+RAM', raw, re.I):
            ram_vals.append(to_gb(ram_val, ram_unit))
        # Storage from ROM pattern e.g. '6GB RAM 128GB ROM'
        for s_val, s_unit in re.findall(r'(\d+(?:\.\d+)?)\s*(GB|MB)\s+ROM', raw, re.I):
            stor_vals.append(to_gb(s_val, s_unit))

    return (
        min(ram_vals)  if ram_vals  else None,
        max(ram_vals)  if ram_vals  else None,
        min(stor_vals) if stor_vals else None,
        max(stor_vals) if stor_vals else None,
    )

def price(price_raw, currency):
    if not price_raw:
        return None
    sym_patterns = {
        'USD': r'\$\s*([\d,]+\.?\d*)',
        'EUR': r'€\s*([\d,]+\.?\d*)',
        'GBP': r'£\s*([\d,]+\.?\d*)',
        'INR': r'₹\s*([\d,]+)',
    }
    word_patterns = {
        'USD': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*(?:USD|\bUS\b)',
        'EUR': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*EUR',
        'GBP': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*GBP',
        'INR': r'(?:about|approx\.?)?\s*([\d,]+)\s*(?:INR|Rs\.?)',
    }
    m = re.search(sym_patterns.get(currency, ''), price_raw)
    if m:
        v = m.group(1).replace(',', '')
        return int(v) if currency == 'INR' else float(v)
    m = re.search(word_patterns.get(currency, ''), price_raw, re.I)
    if m:
        v = m.group(1).replace(',', '')
        return int(v) if currency == 'INR' else float(v)
    return None

def get_brands():
    soup = fetch('makers.php3', allow_skip=False)
    if not soup:
        return []
    table = soup.find('table')
    brands = []
    for a in (table.find_all('a') if table else []):
        href = a.get('href', '')
        if '-phones-' not in href:
            continue
        name = href.split('-phones-')[0].replace('-', ' ').title()
        spans = a.find_all('span')
        count = next((re.findall(r'\d+', s.get_text())[0]
                      for s in spans if re.findall(r'\d+', s.get_text())), '?')
        brands.append({'name': name, 'url': href, 'count': count})
    log.info(f"Found {len(brands)} brands")
    return brands

def get_links(brand):
    safe_name = re.sub(r'[^a-z0-9]', '_', brand['name'].lower())
    cache_file = os.path.join(OUTPUT_FOLDER, f'_links_{safe_name}.json')
    if os.path.exists(cache_file):
        cached = json.load(open(cache_file))
        log.info(f"  Loaded {len(cached)} links from cache")
        return cached

    soup = fetch(brand['url'])
    if not soup:
        return []

    pages = [brand['url']]
    nav = soup.find(class_='nav-pages')
    if nav:
        for a in nav.find_all('a', href=True):
            if a['href'] not in pages:
                pages.append(a['href'])

    SKIP = ['review', 'reviewcomm', 'news', 'newscomm',
            'hands_on', '-phones-', 'makers', 'search', 'glossary', 'compare']

    links = []
    for i, page_url in enumerate(pages):
        page_soup = soup if i == 0 else fetch(page_url)
        if not page_soup:
            continue
        container = (page_soup.find(class_='section-body') or
                     page_soup.find(id='phones-list') or
                     page_soup.find(class_='makers'))
        src = container if container else page_soup
        for a in src.find_all('a', href=True):
            h = a['href']
            if not re.search(r'-\d+\.php$', h):
                continue
            if any(kw in h for kw in SKIP):
                continue
            if h not in links:
                links.append(h)

    log.info(f"  Found {len(links)} phone links for {brand['name']}")
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    json.dump(links, open(cache_file, 'w'), indent=2)
    return links

def scrape_phone(url, brand_name):
    soup = fetch(url)
    if soup is None:
        return None

    specs = {}
    for tag in soup.find_all(attrs={'data-spec': True}):
        key = tag['data-spec']
        col = DATA_SPEC.get(key)
        if not col:
            continue
        text = re.sub(r'\s+', ' ', tag.get_text(separator=' ', strip=True))
        specs[col] = text

    if not specs:
        log.warning(f"  No data-spec fields for {url}")
        return {}

    tag = soup.find(class_='specs-phone-name-title') or soup.find('h1')
    model_name = tag.get_text(strip=True) if tag else url

    img_div = soup.find(class_='specs-photo-main')
    model_img = ''
    if img_div:
        img = img_div.find('img')
        model_img = img.get('src', '') if img else ''

    weight_raw = g(specs, '_weight')
    body_other = g(specs, '_bodyother')
    build_raw  = g(specs, '_build')
    dsize_raw  = g(specs, '_displaysize')
    dres_raw   = g(specs, '_displayres')
    dprot_raw  = g(specs, '_displayprot')
    mem_raw    = g(specs, '_internalmem')
    cam1_raw   = g(specs, '_cam1')
    cam2_raw   = g(specs, '_cam2')
    bt_raw     = g(specs, '_bluetooth')
    nfc_raw    = g(specs, '_nfc')
    usb_raw    = g(specs, '_usb')
    bat_raw    = g(specs, '_battery')
    price_raw  = g(specs, '_price')
    bench_raw  = g(specs, '_tbench')
    net_raw    = g(specs, 'Network_Technology')

    lens_type = None
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Main Camera' in th.get_text():
            for td in tbl.find_all('td', class_='ttl'):
                t = td.get_text(strip=True)
                if t in ('Single', 'Dual', 'Triple', 'Quad', 'Penta'):
                    lens_type = t
                    break

    display_nits = None
    loudspeaker_lufs = None
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Tests' in th.get_text():
            for td in tbl.find_all('td', class_='nfo'):
                if td.get('data-spec'):
                    continue
                txt = td.get_text(strip=True)
                if 'nits' in txt.lower():
                    m = re.search(r'(\d+)\s*nits', txt, re.I)
                    if m:
                        display_nits = int(m.group(1))
                if 'LUFS' in txt:
                    m = re.search(r'(-?\d+\.?\d*)\s*LUFS', txt)
                    if m:
                        loudspeaker_lufs = float(m.group(1))

    back_mat = None
    for mat in ['titanium', 'ceramic', 'glass', 'plastic', 'aluminum', 'aluminium', 'polycarbonate']:
        if mat in build_raw.lower() and 'back' in build_raw.lower():
            back_mat = mat.title()
            break

    ip_m = re.search(r'IP\d{2}', body_other, re.I)
    ip_rating = ip_m.group(0).upper() if ip_m else None

    res_m = re.search(r'(\d{3,4})\s*[x×]\s*(\d{3,4})', dres_raw)
    resolution = f"{res_m.group(1)}x{res_m.group(2)}" if res_m else None

    disp_prot = None
    for p in ['Ceramic Shield 2', 'Ceramic Shield', 'Gorilla Glass Victus+',
               'Gorilla Glass Victus', 'Gorilla Glass 7i', 'Gorilla Glass 7',
               'Gorilla Glass 6', 'Gorilla Glass 5', 'Gorilla Glass 3']:
        if p.lower() in dprot_raw.lower():
            disp_prot = p
            break
    if not disp_prot and dprot_raw:
        disp_prot = dprot_raw[:40]

    # Fixed memory extraction - handles both GB and MB, converts MB to GB
    ram_min, ram_max, stor_min, stor_max = parse_memory(mem_raw)

    aperture_m = re.search(r'f/(\d+\.?\d*)', cam1_raw)
    sensor_m   = re.search(r'(1/\d+\.?\d*)[\"″]?', cam1_raw)

    nfc = 'Yes' if 'yes' in nfc_raw.lower() else ('No' if nfc_raw else None)

    usb_t = None
    if usb_raw:
        if re.search(r'type-c|usb-c', usb_raw, re.I):
            usb_t = 'Type-C'
        elif re.search(r'micro.?usb', usb_raw, re.I):
            usb_t = 'Micro-USB'
        elif re.search(r'lightning', usb_raw, re.I):
            usb_t = 'Lightning'
        else:
            usb_t = usb_raw[:20]

    jack_raw = ''
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Sound' in th.get_text():
            for row in tbl.find_all('tr'):
                ttl = row.find('td', class_='ttl')
                nfo = row.find('td', class_='nfo')
                if ttl and nfo and '3.5' in ttl.get_text():
                    jack_raw = nfo.get_text(strip=True)

    chg_raw = ''
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Battery' in th.get_text():
            for row in tbl.find_all('tr'):
                ttl = row.find('td', class_='ttl')
                nfo = row.find('td', class_='nfo')
                if ttl and nfo and 'Charging' in ttl.get_text():
                    chg_raw = nfo.get_text(separator=' ', strip=True)

    antutu = num(bench_raw, r'AnTuTu[:\s]+([\d,]+)', int)
    geek   = num(bench_raw, r'GeekBench[:\s]+([\d,]+)', int)
    dmark  = num(bench_raw, r'3DMark[:\s]+([\d,]+)', int)

    phone = {
        'Brand':                   brand_name,
        'Model_Name':              model_name,
        'Model_URL':               BASE_URL + url if not url.startswith('http') else url,
        'Model_Image':             model_img,
        'Network_Technology':      net_raw,
        '5G_Support':              'Yes' if '5G' in net_raw else 'No',
        'Announced':               g(specs, 'Announced'),
        'Status':                  g(specs, 'Status'),
        'Dimensions':              g(specs, 'Dimensions'),
        'Weight_g':                num(weight_raw, r'(\d+(?:\.\d+)?)\s*g\b'),
        'Back_Material':           back_mat,
        'IP_Rating':               ip_rating,
        'SIM_Type':                g(specs, 'SIM_Type'),
        'Display_Type':            g(specs, 'Display_Type'),
        'Refresh_Rate_Hz':         num(g(specs, 'Display_Type'), r'(\d{2,3})Hz', int),
        'Display_Size_inch':       num(dsize_raw, r'(\d+\.?\d*)\s*inches'),
        'Resolution':              resolution,
        'PPI_Density':             num(dres_raw, r'~?(\d+)\s*ppi', int),
        'Screen_to_Body_Pct':      num(dsize_raw, r'~?(\d+\.?\d*)\s*%\s*screen.to.body'),
        'Display_Protection':      disp_prot,
        'OS':                      g(specs, 'OS'),
        'Chipset':                 g(specs, 'Chipset'),
        'Process_Node_nm':         num(g(specs, 'Chipset'), r'\((\d+)\s*nm\)', int),
        'CPU':                     g(specs, 'CPU'),
        'GPU':                     g(specs, 'GPU'),
        'RAM_GB':                  ram_min,
        'RAM_Max_GB':              ram_max,
        'Storage_GB':              stor_min,
        'Storage_Max_GB':          stor_max,
        'Storage_Type':            g(specs, 'Storage_Type'),
        'Main_Camera_MP':          mp(cam1_raw),
        'Lens_Count':              {'Single':1,'Dual':2,'Triple':3,'Quad':4,'Penta':5}.get(lens_type),
        'Main_Aperture':           float(aperture_m.group(1)) if aperture_m else None,
        'OIS':                     'Yes' if 'OIS' in cam1_raw else 'No',
        'Sensor_Size':             sensor_m.group(1) if sensor_m else None,
        'Camera_4K_Video':         'Yes' if '4K' in g(specs, 'Camera_Video') else 'No',
        'Camera_Features':         g(specs, 'Camera_Features'),
        'Camera_Video':            g(specs, 'Camera_Video'),
        'Selfie_Camera_MP':        mp(cam2_raw),
        'Selfie_4K_Video':         'Yes' if '4K' in g(specs, 'Selfie_Camera_Video') else 'No',
        'Selfie_Camera_Features':  g(specs, 'Selfie_Camera_Features'),
        'Selfie_Camera_Video':     g(specs, 'Selfie_Camera_Video'),
        'WiFi':                    g(specs, 'WiFi'),
        'NFC':                     nfc,
        'Bluetooth_Version':       num(bt_raw, r'^(\d+\.?\d*)'),
        'USB_Type':                usb_t,
        'Headphone_Jack':          'Yes' if 'yes' in jack_raw.lower() else ('No' if jack_raw else None),
        'GPS':                     g(specs, 'GPS'),
        'Sensors':                 g(specs, 'Sensors'),
        'Battery_mAh':             num(bat_raw, r'(\d+)\s*mAh', int),
        'Wired_Charging_W':        num(chg_raw, r'(\d+)W(?:\s+wired)?', int),
        'Wireless_Charging_W':     num(chg_raw, r'(\d+)W\s+wireless', int),
        'Reverse_Wireless_Charging': 'Yes' if 'reverse wireless' in chg_raw.lower() else 'No',
        'Battery_ActiveUse':       g(specs, 'Battery_ActiveUse'),
        'Price_USD':               price(price_raw, 'USD'),
        'Price_EUR':               price(price_raw, 'EUR'),
        'Price_GBP':               price(price_raw, 'GBP'),
        'Price_INR':               price(price_raw, 'INR'),
        'AnTuTu_Score':            antutu,
        'GeekBench_Score':         geek,
        'ThreeDMark_Score':        dmark,
        'Display_nits_tested':     display_nits,
        'Loudspeaker_LUFS':        loudspeaker_lufs,
        'Colors':                  g(specs, 'Colors'),
        'Model_Numbers':           g(specs, 'Model_Numbers'),
        'SAR_US':                  g(specs, 'SAR_US'),
        'SAR_EU':                  g(specs, 'SAR_EU'),
        'FM_Radio':                g(specs, 'FM_Radio'),
    }

    log.info(f"  {model_name} | RAM={ram_min}GB | Storage={stor_min}GB | Main={phone['Main_Camera_MP']}MP")
    return phone

def write_csv(filepath, row, write_header=False):
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, 'w' if write_header else 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        if write_header:
            w.writeheader()
        w.writerow(row)

def run():
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Brands: {TARGET_BRANDS}")
    log.info("Warm-up wait 60s ...")
    time.sleep(60)

    for attempt in range(1, 4):
        try:
            r = SESSION.get(BASE_URL, timeout=30)
            if r.status_code == 200:
                log.info("Connected")
                break
            elif r.status_code == 403:
                log.error("403 blocked - use mobile hotspot")
                return
        except Exception as e:
            log.warning(f"Connection attempt {attempt} failed: {e}")
            if attempt < 3:
                time.sleep(30 * attempt)
            else:
                log.error("Cannot connect - check internet")
                return

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    prog        = load_json(PROGRESS_FILE, {'done_phones': [], 'done_brands': []})
    retry       = load_json(RETRY_FILE, [])
    done_phones = set(prog['done_phones'])
    done_brands = set(prog['done_brands'])

    all_brands = get_brands()
    target_set = {b.lower() for b in TARGET_BRANDS}
    brands     = [b for b in all_brands if b['name'].lower() in target_set]
    log.info(f"Matched: {[b['name'] for b in brands]}")

    master_path = os.path.join(OUTPUT_FOLDER, MASTER_CSV)
    master_new  = not os.path.exists(master_path)
    session_tot = 0
    start_time  = datetime.now()

    for b_idx, brand in enumerate(brands, 1):
        if brand['name'] in done_brands:
            log.info(f"[{b_idx}/{len(brands)}] SKIP {brand['name']}")
            continue

        log.info(f"[{b_idx}/{len(brands)}] {brand['name']} ({brand['count']} phones)")
        links = get_links(brand)
        if not links:
            continue

        safe       = re.sub(r'[^a-zA-Z0-9]', '_', brand['name'])
        brand_path = os.path.join(OUTPUT_FOLDER, f"{safe}.csv")
        brand_new  = not os.path.exists(brand_path)
        bc = 0

        for p_idx, link in enumerate(links, 1):
            if link in done_phones:
                continue
            if session_tot >= MAX_PER_SESSION:
                log.info("Session cap - saving and stopping")
                save_json(PROGRESS_FILE, prog)
                save_json(RETRY_FILE, retry)
                return

            log.info(f"  [{p_idx}/{len(links)}]")
            data = scrape_phone(link, brand['name'])

            if data is None:
                if link not in [x.get('url', '') for x in retry]:
                    retry.append({'url': link, 'brand': brand['name']})
                save_json(RETRY_FILE, retry)
                continue

            if data:
                write_csv(brand_path, data, write_header=brand_new)
                brand_new = False
                write_csv(master_path, data, write_header=master_new)
                master_new = False
                done_phones.add(link)
                prog['done_phones'] = list(done_phones)
                save_json(PROGRESS_FILE, prog)
                bc += 1
                session_tot += 1
                log.info(f"  Saved [{bc}/{len(links)}]")

        done_brands.add(brand['name'])
        prog['done_brands'] = list(done_brands)
        save_json(PROGRESS_FILE, prog)
        log.info(f"Done: {brand['name']} - {bc} phones")

    if retry:
        log.info(f"Retrying {len(retry)} skipped ...")
        still = []
        for item in retry:
            url, br = item['url'], item['brand']
            if url in done_phones:
                continue
            time.sleep(random.uniform(60, 120))
            data = scrape_phone(url, br)
            if data:
                write_csv(master_path, data, write_header=False)
                done_phones.add(url)
                prog['done_phones'] = list(done_phones)
                save_json(PROGRESS_FILE, prog)
            else:
                still.append(item)
        save_json(RETRY_FILE, still)

    elapsed = (datetime.now() - start_time).seconds // 60
    log.info(f"Finished - {session_tot} phones in {elapsed} min")

if __name__ == '__main__':
    print(f"\n{BATCH_NAME} - {TARGET_BRANDS}")
    print(f"Output: {OUTPUT_FOLDER}/")
    print("Ctrl+C to stop - progress is always saved\n")
    try:
        run()
    except requests.exceptions.ConnectTimeout:
        print("Timeout - check internet or use mobile hotspot")
    except requests.exceptions.ConnectionError:
        print("No connection - connect to internet and re-run")
    except KeyboardInterrupt:
        print("Stopped - progress saved - re-run to continue")
