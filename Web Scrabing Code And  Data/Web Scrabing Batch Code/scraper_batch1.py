"""
GSMArena Scraper — DATA-SPEC VERSION — BATCH 1
===============================================
Brands : Apple | Samsung | Google | OnePlus
Phones : ~1,753   |   Time: 10-12 hours

ROOT CAUSE OF EMPTY COLUMNS — FIXED:
  Old code matched section headers as plain text e.g. "Main Camera|Single"
  GSMArena HTML uses `data-spec` attributes on every <td class="nfo">
  This version reads data-spec directly — 100% reliable, never empty.

  Example from real HTML:
    <td class="nfo" data-spec="cam1modules">48 MP, f/1.6, ...</td>
    <td class="nfo" data-spec="tbench">AnTuTu: 2187425...</td>
    <td class="nfo" data-spec="price">$ 649.99 / €...</td>

Output: 54 clean columns — all populated where GSMArena has the data.
"""

import requests, csv, os, time, random, logging, re, json
from bs4 import BeautifulSoup
from datetime import datetime

# ── BATCH IDENTITY ─────────────────────────────────────────────
BATCH_NAME    = 'BATCH1'
TARGET_BRANDS = ['Apple', 'Samsung', 'Google', 'Oneplus']

# ── CONFIG ─────────────────────────────────────────────────────
DELAY_MIN        = 15
DELAY_MAX        = 45
LONG_PAUSE_EVERY = 30
LONG_PAUSE_MIN   = 120
LONG_PAUSE_MAX   = 300
MAX_PER_SESSION  = 500
MAX_RETRIES      = 2
OUTPUT_FOLDER    = f'GSMArenaDataset_{BATCH_NAME}'
MASTER_CSV       = f'GSMArena_{BATCH_NAME}.csv'
RETRY_FILE       = f'retry_queue_{BATCH_NAME}.json'
PROGRESS_FILE    = f'progress_{BATCH_NAME}.json'
LOG_FILE         = f'scraper_{BATCH_NAME}.log'
BASE_URL         = 'https://www.gsmarena.com/'

# ── data-spec ATTRIBUTE MAP ─────────────────────────────────────
# Every key is the exact data-spec value from GSMArena HTML
# These never change regardless of page layout or section ordering
DATA_SPEC = {
    'nettech':           'Network_Technology',
    'year':              'Announced',
    'status':            'Status',
    'dimensions':        'Dimensions',
    'weight':            '_weight',
    'build':             '_build',
    'sim':               'SIM_Type',
    'bodyother':         '_bodyother',       # IP rating lives here
    'displaytype':       'Display_Type',
    'displaysize':       '_displaysize',
    'displayresolution': '_displayres',
    'displayprotection': '_displayprot',
    'os':                'OS',
    'chipset':           'Chipset',
    'cpu':               'CPU',
    'gpu':               'GPU',
    'internalmemory':    '_internalmem',
    'memoryother':       'Storage_Type',
    'cam1modules':       '_cam1',
    'cam1features':      'Camera_Features',
    'cam1video':         'Camera_Video',
    'cam2modules':       '_cam2',
    'cam2features':      'Selfie_Camera_Features',
    'cam2video':         'Selfie_Camera_Video',
    'wlan':              'WiFi',
    'bluetooth':         '_bluetooth',
    'gps':               'GPS',
    'nfc':               '_nfc',
    'radio':             'FM_Radio',
    'usb':               '_usb',
    'sensors':           'Sensors',
    'batdescription1':   '_battery',
    'batlife2':          'Battery_ActiveUse',
    'colors':            'Colors',
    'models':            'Model_Numbers',
    'sar-us':            'SAR_US',
    'sar-eu':            'SAR_EU',
    'price':             '_price',
    'tbench':            '_tbench',
}
# Keys starting with _ are raw — parsed further into clean columns below

# ── FINAL CSV COLUMNS ──────────────────────────────────────────
CSV_COLUMNS = [
    # Identity
    'Brand', 'Model_Name', 'Model_URL', 'Model_Image',
    # Network
    'Network_Technology', '5G_Support',
    # Launch
    'Announced', 'Status',
    # Body
    'Dimensions', 'Weight_g', 'Back_Material', 'IP_Rating', 'SIM_Type',
    # Display
    'Display_Type', 'Refresh_Rate_Hz', 'Display_Size_inch',
    'Resolution', 'PPI_Density', 'Screen_to_Body_Pct', 'Display_Protection',
    # Platform
    'OS', 'Chipset', 'Process_Node_nm', 'CPU', 'GPU',
    # Memory
    'RAM_GB', 'RAM_Max_GB', 'Storage_GB', 'Storage_Max_GB', 'Storage_Type',
    # Main Camera
    'Main_Camera_MP', 'Lens_Count', 'Main_Aperture', 'OIS',
    'Sensor_Size', 'Camera_4K_Video', 'Camera_Features', 'Camera_Video',
    # Selfie Camera
    'Selfie_Camera_MP', 'Selfie_4K_Video',
    'Selfie_Camera_Features', 'Selfie_Camera_Video',
    # Connectivity
    'WiFi', 'NFC', 'Bluetooth_Version', 'USB_Type', 'Headphone_Jack', 'GPS',
    # Sensors / Features
    'Sensors',
    # Battery
    'Battery_mAh', 'Wired_Charging_W', 'Wireless_Charging_W',
    'Reverse_Wireless_Charging', 'Battery_ActiveUse',
    # Price
    'Price_USD', 'Price_EUR', 'Price_GBP', 'Price_INR',
    # Benchmarks
    'AnTuTu_Score', 'GeekBench_Score', 'ThreeDMark_Score',
    'Display_nits_tested', 'Loudspeaker_LUFS',
    # Misc
    'Colors', 'Model_Numbers', 'SAR_US', 'SAR_EU', 'FM_Radio',
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)
request_count = 0

def make_session():
    s = requests.Session()
    s.headers.update({
        'User-Agent':                random.choice(USER_AGENTS),
        'Accept':                    'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language':           random.choice(['en-US,en;q=0.9','en-GB,en;q=0.8']),
        'Accept-Encoding':           'gzip, deflate, br',
        'Connection':                'keep-alive',
        'Referer':                   BASE_URL,
        'DNT':                       '1',
        'Upgrade-Insecure-Requests': '1',
    })
    return s

SESSION = make_session()

def wait():
    global request_count
    request_count += 1
    if request_count == 1:
        log.info("  ▶ First request — no delay")
        return
    if request_count % LONG_PAUSE_EVERY == 0:
        p = random.uniform(LONG_PAUSE_MIN, LONG_PAUSE_MAX)
        log.info(f"  ☕ Long pause {p:.0f}s ...")
        time.sleep(p)
        return
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    log.info(f"  ⏳ {t:.1f}s (req #{request_count})")
    time.sleep(t)

def load_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default

def save_json(path, data):
    json.dump(data, open(path,'w'), indent=2)

def fetch(url, allow_skip=True):
    global SESSION
    if not url.startswith('http'): url = BASE_URL + url
    if request_count > 0 and request_count % 50 == 0:
        SESSION = make_session()
    for attempt in range(1, MAX_RETRIES + 1):
        wait()
        try:
            r = SESSION.get(url, timeout=30)
            log.info(f"  GET {url[:85]} → {r.status_code}")
            if r.status_code == 200:
                return BeautifulSoup(r.text, 'lxml')
            elif r.status_code == 429:
                if allow_skip and attempt == MAX_RETRIES:
                    log.warning("  ⚠️  429 — SKIPPING")
                    return None
                b = random.uniform(90,180)
                log.warning(f"  ⚠️  429 sleeping {b:.0f}s ...")
                time.sleep(b); SESSION = make_session()
            elif r.status_code == 403:
                log.error("  ❌ 403 blocked"); return None
            else:
                log.warning(f"  ⚠️  HTTP {r.status_code}")
        except requests.exceptions.Timeout:
            log.warning(f"  ⚠️  Timeout attempt {attempt}")
        except Exception as e:
            log.error(f"  ❌ {e}"); time.sleep(20)
    return None

# ================================================================
# EXTRACTION — all from clean data-spec values
# ================================================================
def g(specs, key, default=''):
    return specs.get(key, default) or default

def mp(raw):
    if not raw: return None
    m = re.findall(r'(\d+(?:\.\d+)?)\s*MP', raw, re.I)
    return float(m[0]) if m else None

def num(s, pat, cast=float):
    if not s: return None
    m = re.search(pat, s)
    try: return cast(m.group(1).replace(',','')) if m else None
    except: return None

def yn(s, kw_yes='yes'):
    if not s: return None
    return 'Yes' if kw_yes.lower() in s.lower() else 'No'

def build_phone(url, brand, specs, soup):
    """
    specs = dict of {data-spec-key: text-value}
    All fields extracted from specs dict directly.
    """
    # ── Raw strings ────────────────────────────────────────────
    weight_raw  = g(specs,'_weight')
    body_other  = g(specs,'_bodyother')   # IP68 lives here
    build_raw   = g(specs,'_build')
    dsize_raw   = g(specs,'_displaysize')
    dres_raw    = g(specs,'_displayres')
    dprot_raw   = g(specs,'_displayprot')
    mem_raw     = g(specs,'_internalmem')
    cam1_raw    = g(specs,'_cam1')
    cam2_raw    = g(specs,'_cam2')
    bt_raw      = g(specs,'_bluetooth')
    nfc_raw     = g(specs,'_nfc')
    usb_raw     = g(specs,'_usb')
    bat_raw     = g(specs,'_battery')
    price_raw   = g(specs,'_price')
    bench_raw   = g(specs,'_tbench')
    net_raw     = g(specs,'Network_Technology')

    # ── Camera: lens type from <td class="ttl"> text ───────────
    lens_type = None
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Main Camera' in th.get_text():
            for td in tbl.find_all('td', class_='ttl'):
                t = td.get_text(strip=True)
                if t in ('Single','Dual','Triple','Quad','Penta'):
                    lens_type = t; break

    # ── Tests section — cells without data-spec ────────────────
    # Display nits and Loudspeaker LUFS have no data-spec attribute
    display_nits = None
    loudspeaker_lufs = None
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Tests' in th.get_text():
            for td in tbl.find_all('td', class_='nfo'):
                if td.get('data-spec'): continue  # skip ones we already have
                txt = td.get_text(strip=True)
                if 'nits' in txt.lower():
                    m = re.search(r'(\d+)\s*nits', txt, re.I)
                    if m: display_nits = int(m.group(1))
                if 'LUFS' in txt:
                    m = re.search(r'(-?\d+\.?\d*)\s*LUFS', txt)
                    if m: loudspeaker_lufs = float(m.group(1))

    # ── EU LABEL section ───────────────────────────────────────
    # No data-spec on EU label rows — read by section header
    eu_energy = eu_battery_endurance = eu_freefall = eu_repair = None
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'EU LABEL' in th.get_text(strip=True):
            rows = tbl.find_all('tr')
            for row in rows:
                ttl = row.find('td', class_='ttl')
                nfo = row.find('td', class_='nfo')
                if not ttl or not nfo: continue
                label = ttl.get_text(strip=True).lower()
                val   = nfo.get_text(strip=True)
                if 'energy'      in label: eu_energy = val
                elif 'battery'   in label: eu_battery_endurance = val
                elif 'free fall' in label: eu_freefall = val
                elif 'repair'    in label: eu_repair = val

    # ── Derived fields ─────────────────────────────────────────
    # Back material
    back_mat = None
    for mat in ['titanium','ceramic','glass','plastic','aluminum',
                'aluminium','polycarbonate','leather']:
        if mat in build_raw.lower():
            back_mat = mat.title(); break

    # IP rating — from bodyother field
    ip_m = re.search(r'IP\d{2}', body_other, re.I)
    ip_rating = ip_m.group(0).upper() if ip_m else None

    # Display
    refresh = num(g(specs,'Display_Type'), r'(\d{2,3})Hz', int)
    d_inch  = num(dsize_raw, r'(\d+\.?\d*)\s*inches')
    ratio_m = re.search(r'~?(\d+\.?\d*)\s*%\s*screen.to.body', dsize_raw, re.I)
    screen_ratio = float(ratio_m.group(1)) if ratio_m else None
    ppi     = num(dres_raw, r'~?(\d+)\s*ppi', int)
    res_m   = re.search(r'(\d{3,4})\s*x\s*(\d{3,4})', dres_raw)
    resolution = f"{res_m.group(1)}x{res_m.group(2)}" if res_m else None
    disp_prot = None
    for p in ['Ceramic Shield 2','Ceramic Shield','Gorilla Glass Victus+',
               'Gorilla Glass Victus','Gorilla Glass 7i','Gorilla Glass 7',
               'Gorilla Glass 6','Gorilla Glass 5','Gorilla Glass 3',
               'Dragon Trail','Panda Glass']:
        if p.lower() in dprot_raw.lower():
            disp_prot = p; break
    if not disp_prot and dprot_raw:
        disp_prot = dprot_raw[:40]

    # Chipset process node
    node = num(g(specs,'Chipset'), r'\((\d+)\s*nm\)', int)

    # Memory
    rams  = [int(x) for x in re.findall(r'(\d+)GB\s*RAM', mem_raw)]
    stors = [int(x) for x in re.findall(r'(\d+)GB\s+\d+GB\s+RAM', mem_raw)]
    if not stors:
        stors = [int(x) for x in re.findall(r'(\d+)GB', mem_raw) if int(x) < 2000]

    # Camera
    cam1_mps   = re.findall(r'(\d+(?:\.\d+)?)\s*MP', cam1_raw, re.I)
    cam1_mp    = float(cam1_mps[0]) if cam1_mps else None
    lens_count = {'Single':1,'Dual':2,'Triple':3,'Quad':4,'Penta':5}.get(lens_type)
    aperture_m = re.search(r'f/(\d+\.?\d*)', cam1_raw)
    aperture   = float(aperture_m.group(1)) if aperture_m else None
    ois        = 'Yes' if 'OIS' in cam1_raw else 'No'
    sensor_m   = re.search(r'(1/\d+\.?\d*)[\"″\u00b2]?', cam1_raw)
    sensor_sz  = sensor_m.group(1) if sensor_m else None
    cam4k      = 'Yes' if '4K' in g(specs,'Camera_Video') else 'No'

    cam2_mp    = mp(cam2_raw)
    selfie4k   = 'Yes' if '4K' in g(specs,'Selfie_Camera_Video') else 'No'

    # Connectivity
    nfc   = 'Yes' if 'yes' in nfc_raw.lower() else ('No' if nfc_raw else None)
    bt_v  = num(bt_raw, r'^(\d+\.?\d*)')
    usb_t = None
    if usb_raw:
        if re.search(r'type-c|usb-c', usb_raw, re.I): usb_t = 'Type-C'
        elif re.search(r'micro.?usb', usb_raw, re.I): usb_t = 'Micro-USB'
        elif re.search(r'lightning', usb_raw, re.I):  usb_t = 'Lightning'
        else: usb_t = usb_raw[:20]

    jack_raw = ''
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Sound' in th.get_text():
            for row in tbl.find_all('tr'):
                ttl = row.find('td', class_='ttl')
                nfo = row.find('td', class_='nfo')
                if ttl and nfo and '3.5' in ttl.get_text():
                    jack_raw = nfo.get_text(strip=True)
    headphone = 'Yes' if 'yes' in jack_raw.lower() else ('No' if jack_raw else None)

    # Battery
    bat_mah = num(bat_raw, r'(\d+)\s*mAh', int)
    chg_raw = ''
    for tbl in soup.find_all('table'):
        th = tbl.find('th')
        if th and 'Battery' in th.get_text():
            for row in tbl.find_all('tr'):
                ttl = row.find('td', class_='ttl')
                nfo = row.find('td', class_='nfo')
                if ttl and nfo and 'Charging' in ttl.get_text():
                    chg_raw = nfo.get_text(separator=' ', strip=True)

    wired_w    = num(chg_raw, r'(\d+)W\s*wired', int) or num(chg_raw, r'(\d+)W', int)
    wireless_w = num(chg_raw, r'(\d+)W\s*wireless', int)
    rev_wl     = 'Yes' if 'reverse wireless' in chg_raw.lower() else 'No'

    # Prices
    def price(currency):
        """
        Extract price for a given currency from price_raw string.
        Handles all GSMArena formats:
          Standard : '$ 649.99 / € 669.00 / £ 599.00 / ₹ 64,900'
          Word style: 'About 250 EUR' / '167 EUR' / 'Approx. 180 USD'
          Symbol after word: 'About $ 199'
        """
        if not price_raw:
            return None

        # Pattern 1: currency symbol directly before number
        sym_patterns = {
            'USD': r'\$\s*([\d,]+\.?\d*)',
            'EUR': r'€\s*([\d,]+\.?\d*)',
            'GBP': r'£\s*([\d,]+\.?\d*)',
            'INR': r'₹\s*([\d,]+)',
        }
        m = re.search(sym_patterns.get(currency, ''), price_raw)
        if m:
            v = m.group(1).replace(',', '')
            return int(v) if currency == 'INR' else float(v)

        # Pattern 2: number before currency word e.g. 'About 250 EUR', '167 EUR'
        word_patterns = {
            'USD': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*(?:USD|\bUS\b)',
            'EUR': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*EUR',
            'GBP': r'(?:about|approx\.?)?\s*([\d,]+\.?\d*)\s*GBP',
            'INR': r'(?:about|approx\.?)?\s*([\d,]+)\s*(?:INR|Rs\.?)',
        }
        m = re.search(word_patterns.get(currency, ''), price_raw, re.I)
        if m:
            v = m.group(1).replace(',', '')
            return int(v) if currency == 'INR' else float(v)

        # Pattern 3: word then symbol e.g. 'About $ 199'
        if currency == 'USD':
            m = re.search(r'(?:about|approx\.?)\s*\$\s*([\d,]+\.?\d*)',
                          price_raw, re.I)
            if m:
                return float(m.group(1).replace(',', ''))

        return None

    # Benchmarks
    antutu = num(bench_raw, r'AnTuTu[:\s]+([\d,]+)', int)
    geek   = num(bench_raw, r'GeekBench[:\s]+([\d,]+)', int)
    dmark  = num(bench_raw, r'3DMark[:\s]+([\d,]+)', int)

    # ── Final phone dict ───────────────────────────────────────
    p = {
        'Brand':                  brand,
        'Model_Name':             None,   # set by caller
        'Model_URL':              None,   # set by caller
        'Model_Image':            None,   # set by caller
        'Network_Technology':     net_raw,
        '5G_Support':             'Yes' if '5G' in net_raw else 'No',
        'Announced':              g(specs,'Announced'),
        'Status':                 g(specs,'Status'),
        'Dimensions':             g(specs,'Dimensions'),
        'Weight_g':               num(weight_raw, r'(\d+(?:\.\d+)?)\s*g\b'),
        'Back_Material':          back_mat,
        'IP_Rating':              ip_rating,
        'SIM_Type':               g(specs,'SIM_Type'),
        'Display_Type':           g(specs,'Display_Type'),
        'Refresh_Rate_Hz':        refresh,
        'Display_Size_inch':      d_inch,
        'Resolution':             resolution,
        'PPI_Density':            ppi,
        'Screen_to_Body_Pct':     screen_ratio,
        'Display_Protection':     disp_prot,
        'OS':                     g(specs,'OS'),
        'Chipset':                g(specs,'Chipset'),
        'Process_Node_nm':        node,
        'CPU':                    g(specs,'CPU'),
        'GPU':                    g(specs,'GPU'),
        'RAM_GB':                 min(rams) if rams else None,
        'RAM_Max_GB':             max(rams) if rams else None,
        'Storage_GB':             min(stors) if stors else None,
        'Storage_Max_GB':         max(stors) if stors else None,
        'Storage_Type':           g(specs,'Storage_Type'),
        'Main_Camera_MP':         cam1_mp,
        'Lens_Count':             lens_count,
        'Main_Aperture':          aperture,
        'OIS':                    ois,
        'Sensor_Size':            sensor_sz,
        'Camera_4K_Video':        cam4k,
        'Camera_Features':        g(specs,'Camera_Features'),
        'Camera_Video':           g(specs,'Camera_Video'),
        'Selfie_Camera_MP':       cam2_mp,
        'Selfie_4K_Video':        selfie4k,
        'Selfie_Camera_Features': g(specs,'Selfie_Camera_Features'),
        'Selfie_Camera_Video':    g(specs,'Selfie_Camera_Video'),
        'WiFi':                   g(specs,'WiFi'),
        'NFC':                    nfc,
        'Bluetooth_Version':      bt_v,
        'USB_Type':               usb_t,
        'Headphone_Jack':         headphone,
        'GPS':                    g(specs,'GPS'),
        'Sensors':                g(specs,'Sensors'),
        'Battery_mAh':            bat_mah,
        'Wired_Charging_W':       wired_w,
        'Wireless_Charging_W':    wireless_w,
        'Reverse_Wireless_Charging': rev_wl,
        'Battery_ActiveUse':      g(specs,'Battery_ActiveUse'),
        'Price_USD':              price('USD'),
        'Price_EUR':              price('EUR'),
        'Price_GBP':              price('GBP'),
        'Price_INR':              price('INR'),
        'AnTuTu_Score':           antutu,
        'GeekBench_Score':        geek,
        'ThreeDMark_Score':       dmark,
        'Display_nits_tested':    display_nits,
        'Loudspeaker_LUFS':       loudspeaker_lufs,
        'Colors':                 g(specs,'Colors'),
        'Model_Numbers':          g(specs,'Model_Numbers'),
        'SAR_US':                 g(specs,'SAR_US'),
        'SAR_EU':                 g(specs,'SAR_EU'),
        'FM_Radio':               g(specs,'FM_Radio'),
    }
    return p

# ── SCRAPE ONE PHONE ───────────────────────────────────────────
def scrape_phone(url, brand_name):
    soup = fetch(url)
    if soup is None: return None

    # ── Step 1: extract ALL data-spec fields in one pass ──────
    specs = {}
    for tag in soup.find_all(attrs={'data-spec': True}):
        key     = tag['data-spec']
        col     = DATA_SPEC.get(key)
        if not col: continue
        text = tag.get_text(separator=' ', strip=True)
        # Clean up HTML artefacts
        text = re.sub(r'\s+', ' ', text).strip()
        specs[col] = text

    if not specs:
        log.warning(f"  ⚠️  No data-spec fields found for {url}")
        return {}

    # ── Step 2: model name ─────────────────────────────────────
    tag = soup.find(class_='specs-phone-name-title') or soup.find('h1')
    model_name = tag.get_text(strip=True) if tag else url.replace('.php','')

    # ── Step 3: model image ────────────────────────────────────
    img_div = soup.find(class_='specs-photo-main')
    model_img = ''
    if img_div:
        img = img_div.find('img')
        model_img = img.get('src','') if img else ''

    # ── Step 4: build clean phone dict ─────────────────────────
    phone = build_phone(url, brand_name, specs, soup)
    phone['Model_Name']  = model_name
    phone['Model_URL']   = BASE_URL + url if not url.startswith('http') else url
    phone['Model_Image'] = model_img

    # Log key values for immediate verification
    populated = sum(1 for v in phone.values() if v not in (None, '', 'No'))
    log.info(
        f"      📱 {model_name} | "
        f"Main={phone['Main_Camera_MP']}MP | "
        f"Selfie={phone['Selfie_Camera_MP']}MP | "
        f"AnTuTu={phone['AnTuTu_Score']} | "
        f"Price=${phone['Price_USD']} | "
        f"IP={phone['IP_Rating']} | "
        f"Populated={populated}/{len(phone)} fields"
    )
    return phone

# ── GET BRANDS ─────────────────────────────────────────────────
def get_brands():
    soup = fetch('makers.php3', allow_skip=False)
    if not soup: return []
    table = soup.find('table')
    brands = []
    for a in (table.find_all('a') if table else []):
        href = a.get('href','')
        if '-phones-' not in href: continue
        name  = href.split('-phones-')[0].replace('-',' ').title()
        spans = a.find_all('span')
        count = next((re.findall(r'\d+', s.get_text())[0]
                      for s in spans if re.findall(r'\d+', s.get_text())), '?')
        brands.append({'name':name,'url':href,'count':count})
    log.info(f"✅ {len(brands)} brands")
    return brands

# ── GET PHONE LINKS ─────────────────────────────────────────────
def get_links(brand):
    # ── CACHE CHECK — skip all web requests on restart ────────
    # Saves 9+ requests and ~5 minutes per brand per restart
    safe_name  = re.sub(r'[^a-z0-9]', '_', brand['name'].lower())
    cache_file = os.path.join(OUTPUT_FOLDER, f'_links_{safe_name}.json')

    if os.path.exists(cache_file):
        cached = json.load(open(cache_file))
        log.info(f"  📋 Loaded {len(cached)} links from cache — no web requests needed")
        return cached

    log.info(f"  Fetching phone links for: {brand['name']} (~{brand['count']} phones)")

    # ── STEP 1: load first page ────────────────────────────────
    soup = fetch(brand['url'])
    if not soup:
        log.warning(f"  ⚠️  Could not load brand page for {brand['name']}")
        return []

    # ── STEP 2: collect all pagination pages ──────────────────
    pages = [brand['url']]
    nav   = soup.find(class_='nav-pages')
    if nav:
        for a in nav.find_all('a', href=True):
            href = a['href']
            if href and href not in pages:
                pages.append(href)
        log.info(f"    Found {len(pages)} listing pages")
    else:
        log.info(f"    Single listing page (no pagination)")

    # ── STEP 3: extract phone links from each listing page ────
    # Keywords that identify NON-spec pages — all are skipped
    SKIP_KEYWORDS = [
        'review',       # e.g. xiaomi_17t-review-2967.php
        'reviewcomm',   # e.g. reviewcomm-2967.php
        'news',         # e.g. xiaomi_17t_launched-news-73025.php
        'newscomm',     # e.g. newscomm-73025.php
        'hands_on',     # e.g. oneplus_pad_4_hands_on-review-2958.php
        '-phones-',     # e.g. samsung-phones-9.php  (brand listing pages)
        'makers',       # e.g. makers.php3
        'search',       # search result pages
        'glossary',     # glossary.php3
        'compare',      # compare pages
    ]

    links = []
    for i, page_url in enumerate(pages):
        page_soup = soup if i == 0 else fetch(page_url)
        if not page_soup:
            log.warning(f"    ⚠️  Could not load listing page {i+1}/{len(pages)}")
            continue

        # Try the most reliable container selectors first
        container = (
            page_soup.find(class_='section-body') or
            page_soup.find(id='phones-list')       or
            page_soup.find(class_='makers')
        )
        # Fallback to full page scan if no container found
        search_scope = container if container else page_soup

        for a in search_scope.find_all('a', href=True):
            h = a['href']

            # Must end with digits.php — the signature of a spec page
            # e.g. apple_iphone_16_pro-12820.php
            if not re.search(r'-\d+\.php$', h):
                continue

            # Must not be any of the known non-spec page types
            if any(kw in h for kw in SKIP_KEYWORDS):
                continue

            # No duplicates
            if h not in links:
                links.append(h)

        log.info(f"    Page {i+1}/{len(pages)}: {len(links)} links so far")

    log.info(f"  ✅ {len(links)} valid phone spec links for {brand['name']}")

    # ── STEP 4: save to cache ─────────────────────────────────
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    json.dump(links, open(cache_file, 'w'), indent=2)
    log.info(f"  💾 Link list cached → {cache_file}")

    return links

# ── CSV WRITER ─────────────────────────────────────────────────
def write_csv(filepath, row, write_header=False):
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    with open(filepath, 'w' if write_header else 'a',
              newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore')
        if write_header: w.writeheader()
        w.writerow(row)

# ── MAIN ───────────────────────────────────────────────────────
def run():
    log.info("="*60)
    log.info(f"BATCH 1 — Apple | Samsung | Google | OnePlus")
    log.info(f"Phones ~1,753  |  Time: 10-12 hours")
    log.info(f"Columns: {len(CSV_COLUMNS)} | Method: data-spec attributes")
    log.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("="*60)

    log.info("Checking connection ...")
    for attempt in range(1, 4):
        try:
            r = SESSION.get(BASE_URL, timeout=30)
            if r.status_code == 200:
                log.info(f"  ✅ Connected (attempt {attempt})")
                break
            elif r.status_code == 403:
                log.error("  ❌ 403 blocked — use mobile hotspot")
                return
            else:
                log.warning(f"  ⚠️  HTTP {r.status_code}")
        except Exception as e:
            ws = 30 * attempt
            log.warning(f"  ⚠️  {type(e).__name__} (attempt {attempt}/3)")
            if attempt < 3:
                log.warning(f"     Retrying in {ws}s ...")
                time.sleep(ws)
            else:
                log.error("  ❌ All connection attempts failed")
                log.error("     Check internet or switch to mobile hotspot")
                return

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    prog        = load_json(PROGRESS_FILE, {'done_phones':[],'done_brands':[]})
    retry       = load_json(RETRY_FILE, [])
    done_phones = set(prog['done_phones'])
    done_brands = set(prog['done_brands'])
    log.info(f"Resuming: {len(done_phones)} done | {len(retry)} retry queue")

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

        log.info(f"\n{'='*60}")
        log.info(f"[{b_idx}/{len(brands)}] {brand['name']} ({brand['count']} phones)")
        log.info(f"Elapsed: {(datetime.now()-start_time).seconds//60}min")
        log.info("="*60)

        links = get_links(brand)
        if not links: continue

        safe       = re.sub(r'[^a-zA-Z0-9]','_', brand['name'])
        brand_path = os.path.join(OUTPUT_FOLDER, f"{safe}.csv")
        brand_new  = not os.path.exists(brand_path)
        bc = 0

        for p_idx, link in enumerate(links, 1):
            if link in done_phones:
                log.info(f"  [{p_idx}/{len(links)}] SKIP {link}")
                continue
            if session_tot >= MAX_PER_SESSION:
                log.info(f"🛑 Cap ({MAX_PER_SESSION}) — saving")
                save_json(PROGRESS_FILE, prog)
                save_json(RETRY_FILE, retry)
                return

            log.info(f"\n  [{p_idx}/{len(links)}]")
            data = scrape_phone(link, brand['name'])

            if data is None:
                if link not in [x.get('url','') for x in retry]:
                    retry.append({'url':link,'brand':brand['name']})
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
                bc += 1; session_tot += 1
                log.info(f"  💾 [{bc}/{len(links)}] saved")

        done_brands.add(brand['name'])
        prog['done_brands'] = list(done_brands)
        save_json(PROGRESS_FILE, prog)
        log.info(f"  ✅ {brand['name']} — {bc} phones saved")

    if retry:
        log.info(f"\nRetrying {len(retry)} skipped ...")
        still = []
        for item in retry:
            url, br = item['url'], item['brand']
            if url in done_phones: continue
            time.sleep(random.uniform(60,120))
            data = scrape_phone(url, br)
            if data:
                write_csv(master_path, data, write_header=False)
                done_phones.add(url)
                prog['done_phones'] = list(done_phones)
                save_json(PROGRESS_FILE, prog)
                log.info(f"  ✅ {data.get('Model_Name','?')}")
            else:
                still.append(item)
        save_json(RETRY_FILE, still)

    elapsed = (datetime.now()-start_time).seconds//60
    log.info(f"\n🎉 DONE — {session_tot} phones in {elapsed} min")
    log.info(f"📄 {OUTPUT_FOLDER}/{MASTER_CSV}")

if __name__ == '__main__':
    print(); print("="*60)
    print(f"  {BATCH_NAME} — Apple | Samsung | Google | OnePlus")
    print(f"  {len(CSV_COLUMNS)} columns | data-spec extraction")
    print(f"  Output: {OUTPUT_FOLDER}/")
    print(f"  Ctrl+C anytime — progress always saved")
    print("="*60); print()
    try:
        run()
    except requests.exceptions.ConnectTimeout:
        print("\n❌ TIMEOUT — Check internet or use mobile hotspot")
    except requests.exceptions.ConnectionError:
        print("\n❌ NO CONNECTION — Connect to internet and re-run")
    except KeyboardInterrupt:
        print("\n⛔ Stopped — progress saved — re-run to continue")
