import streamlit as st
import pandas as pd
import time
import re
import os
import threading
import traceback
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import requests

# --- KONFIGURACJA ---
st.set_page_config(page_title="Monitor Ogłoszeń", layout="wide")
CSV_FILE = "miasta_analiza_kompletna.csv"
UPDATE_INTERVAL = 300  # 5 minut

# --- SYSTEM LOGOWANIA ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# --- LOGIKA SCRAPOWANIA ---
def make_slug(text):
    text = text.lower().strip()
    text = text.replace('ł', 'l').replace('ś', 's').replace('ć', 'c').replace('ń', 'n')
    text = text.replace('ó', 'o').replace('ż', 'z').replace('ź', 'z').replace('ą', 'a').replace('ę', 'e')
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')

def run_scraper():
    try:
        logger.info("Rozpoczynam nowy cykl scrapowania...")
        session = cffi_requests.Session(impersonate="chrome120")
        session.cookies.set("warning", "1", domain=".escort.club")
        session.cookies.set("warning", "1", domain="pl.escort.club")

        provinces = {
            "2": "Dolnośląskie", "4": "Kujawsko-Pomorskie", "6": "Lubelskie", "8": "Lubuskie",
            "10": "Łódzkie", "12": "Małopolskie", "14": "Mazowieckie", "16": "Opolskie",
            "18": "Podkarpackie", "20": "Podlaskie", "22": "Pomorskie", "24": "Śląskie",
            "26": "Świętokrzyskie", "28": "Warmińsko-Mazurskie", "30": "Wielkopolskie", "32": "Zachodniopomorskie"
        }

        all_cities = []
        logger.info("Pobieram miasta dla województw...")
        for prov_id, prov_name in provinces.items():
            url = "https://pl.escort.club/getCity.php"
            headers = {"x-requested-with": "XMLHttpRequest", "origin": "https://pl.escort.club"}
            data = {"state_id": prov_id, "selected": "false", "front": "1", "search": "1"}
            
            resp = session.post(url, data=data, headers=headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Błąd API dla woj. {prov_name} (HTTP {resp.status_code})")
                continue
                
            soup = BeautifulSoup(resp.text, "html.parser")
            for opt in soup.find_all("option"):
                val = opt.get("value", "")
                name = opt.text.strip()
                if val and val != "0" and "wybierz" not in name.lower():
                    all_cities.append({"province": prov_name, "city": name, "slug": make_slug(name)})
            time.sleep(0.1)

        logger.info(f"Pobrano {len(all_cities)} miast. Zaczynam liczenie ogłoszeń...")
        
        results = []
        for idx, city_data in enumerate(all_cities, 1):
            url = f"https://pl.escort.club/anonse/towarzyskie/{city_data['slug']}/"
            if idx % 50 == 0:
                logger.info(f"Sprawdzam miasto {idx}/{len(all_cities)}: {city_data['city']}...")
                
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                count = 0
                match_count = re.search(r"Lista wyników:\s*(\d+)", html) or re.search(r"spośród\s*<strong>(\d+)</strong>", html) or re.search(r"Znaleziono\s*(\d+)", html)
                if match_count:
                    count = int(match_count.group(1))
                if count > 0:
                    results.append({"Wojewodztwo": city_data["province"], "Miasto": city_data["city"], "Liczba_Ogloszen": count, "URL": url})
            else:
                 logger.warning(f"Błąd HTTP {resp.status_code} dla miasta {city_data['city']}")
            time.sleep(0.2)

        logger.info(f"Znaleziono ogłoszenia w {len(results)} miastach. Pobieram populację z Wikidata...")
        
        query = """
        SELECT ?cityLabel (MAX(?pop) AS ?population) WHERE {
          ?city wdt:P17 wd:Q36 .
          ?city wdt:P31/wdt:P279* wd:Q515 .
          ?city wdt:P1082 ?pop .
          SERVICE wikibase:label { bd:serviceParam wikibase:language "pl". }
        } GROUP BY ?cityLabel
        """
        wiki_headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MonitorApp/1.0"
        }
        resp_wiki = requests.get("https://query.wikidata.org/sparql", params={'format': 'json', 'query': query}, headers=wiki_headers, timeout=30)
        
        if resp_wiki.status_code != 200:
            logger.error(f"Błąd Wikidata: HTTP {resp_wiki.status_code}")
        else:
            resp_data = resp_wiki.json()
            cities_pop = {}
            for item in resp_data['results']['bindings']:
                c_name = re.sub(r'\s*\(.*?\)', '', item['cityLabel']['value']).strip()
                cities_pop[c_name] = int(item['population']['value'])
                
            df_pop = pd.DataFrame(list(cities_pop.items()), columns=["Miasto", "Populacja"])

            logger.info("Łączę dane i zapisuję CSV...")
            df_ads = pd.DataFrame(results)
            df = pd.merge(df_ads, df_pop, on="Miasto", how="inner")
            df = df[df["Populacja"] > 0]
            df["Ogloszenia_na_10k_mieszk"] = (df["Liczba_Ogloszen"] / df["Populacja"] * 10000).round(2)
            df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")
            logger.info("✅ Cykl zakończony sukcesem! Dane zaktualizowane.")

    except Exception as e:
        logger.error(f"❌ KRYTYCZNY BŁĄD SCRAPOWANIA: {e}")
        logger.error(traceback.format_exc())

# --- URUCHOMIENIE WĄTKU W TLE ---
@st.cache_resource
def start_background_task():
    logger.info("Uruchamianie wątku w tle...")
    def task():
        while True:
            run_scraper()
            time.sleep(UPDATE_INTERVAL)
    thread = threading.Thread(target=task, daemon=True)
    thread.start()
    return thread

start_background_task()

# --- INTERFEJS UŻYTKOWNIKA (STREAMLIT) ---
st.title("📊 Monitor Ogłoszeń w Polsce")
st.markdown("Aplikacja automatycznie odświeża i analizuje dane co 5 minut. **Kliknij nagłówek kolumny, aby posortować tabelę.**")

if not os.path.exists(CSV_FILE):
    st.info("Trwa pierwsze pobieranie danych. Odśwież stronę za około 2 minuty...")
    st.stop()

# Wczytanie gotowych danych
df = pd.read_csv(CSV_FILE)
last_modified = datetime.fromtimestamp(os.path.getmtime(CSV_FILE)).strftime('%Y-%m-%d %H:%M:%S')
st.caption(f"🕒 Ostatnia aktualizacja danych: {last_modified}")

# Filtry
col1, col2 = st.columns(2)
with col1:
    min_pop = st.number_input("Minimalna populacja miasta:", min_value=0, max_value=2000000, value=30000, step=10000)
with col2:
    woj_filter = st.selectbox("Wybierz województwo:", ["Wszystkie"] + sorted(df["Wojewodztwo"].unique().tolist()))

# Aplikacja filtrów
df_filtered = df[df["Populacja"] >= min_pop]
if woj_filter != "Wszystkie":
    df_filtered = df_filtered[df_filtered["Wojewodztwo"] == woj_filter]

# Wyświetlanie tabeli
st.dataframe(
    df_filtered[["Wojewodztwo", "Miasto", "Populacja", "Liczba_Ogloszen", "Ogloszenia_na_10k_mieszk", "URL"]],
    column_config={
        "URL": st.column_config.LinkColumn("Link do miasta"),
        "Populacja": st.column_config.NumberColumn(format="%d"),
        "Ogloszenia_na_10k_mieszk": st.column_config.NumberColumn("Ogłoszenia (na 10k)", format="%.2f")
    },
    use_container_width=True,
    hide_index=True,
    height=600
)
