import streamlit as st
import pandas as pd
import time
import re
import os
import threading
from datetime import datetime
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import requests

# --- KONFIGURACJA ---
st.set_page_config(page_title="Monitor Ogłoszeń", layout="wide")
CSV_FILE = "miasta_analiza_kompletna.csv"
UPDATE_INTERVAL = 300  # 5 minut (w sekundach)

# --- LOGIKA SCRAPOWANIA (Działa w tle) ---
def make_slug(text):
    text = text.lower().strip()
    text = text.replace('ł', 'l').replace('ś', 's').replace('ć', 'c').replace('ń', 'n')
    text = text.replace('ó', 'o').replace('ż', 'z').replace('ź', 'z').replace('ą', 'a').replace('ę', 'e')
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')

def run_scraper():
    try:
        session = cffi_requests.Session(impersonate="chrome120")
        session.cookies.set("warning", "1", domain=".escort.club")
        session.cookies.set("warning", "1", domain="pl.escort.club")

        # 1. Pobieranie województw (uproszczone z fallbackiem dla szybkości i stabilności)
        provinces = {
            "2": "Dolnośląskie", "3": "Kujawsko-Pomorskie", "4": "Lubelskie", "5": "Lubuskie",
            "6": "Łódzkie", "7": "Małopolskie", "8": "Mazowieckie", "9": "Opolskie",
            "10": "Podkarpackie", "11": "Podlaskie", "12": "Pomorskie", "13": "Śląskie",
            "14": "Świętokrzyskie", "15": "Warmińsko-Mazurskie", "16": "Wielkopolskie", "17": "Zachodniopomorskie"
        }

        all_cities = []
        for prov_id, prov_name in provinces.items():
            url = "https://pl.escort.club/getCity.php"
            headers = {"x-requested-with": "XMLHttpRequest", "origin": "https://pl.escort.club"}
            data = {"state_id": prov_id, "selected": "false", "front": "1", "search": "1"}
            
            resp = session.post(url, data=data, headers=headers, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            
            for opt in soup.find_all("option"):
                val = opt.get("value", "")
                name = opt.text.strip()
                if val and val != "0" and "wybierz" not in name.lower():
                    all_cities.append({"province": prov_name, "city": name, "slug": make_slug(name)})
            time.sleep(0.1)

        results = []
        for city_data in all_cities:
            url = f"https://pl.escort.club/anonse/towarzyskie/{city_data['slug']}/"
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                count = 0
                match_count = re.search(r"Lista wyników:\s*(\d+)", html) or re.search(r"spośród\s*<strong>(\d+)</strong>", html) or re.search(r"Znaleziono\s*(\d+)", html)
                if match_count:
                    count = int(match_count.group(1))
                if count > 0:
                    results.append({"Wojewodztwo": city_data["province"], "Miasto": city_data["city"], "Liczba_Ogloszen": count, "URL": url})
            time.sleep(0.2)

        df_ads = pd.DataFrame(results)

        # 2. Pobieranie populacji z Wikidata
        query = """
        SELECT ?cityLabel (MAX(?pop) AS ?population) WHERE {
          ?city wdt:P17 wd:Q36 .
          ?city wdt:P31/wdt:P279* wd:Q515 .
          ?city wdt:P1082 ?pop .
          SERVICE wikibase:label { bd:serviceParam wikibase:language "pl". }
        } GROUP BY ?cityLabel
        """
        resp_wiki = requests.get("https://query.wikidata.org/sparql", params={'format': 'json', 'query': query}, timeout=30).json()
        
        cities_pop = {}
        for item in resp_wiki['results']['bindings']:
            c_name = re.sub(r'\s*\(.*?\)', '', item['cityLabel']['value']).strip()
            cities_pop[c_name] = int(item['population']['value'])
            
        df_pop = pd.DataFrame(list(cities_pop.items()), columns=["Miasto", "Populacja"])

        # 3. Łączenie i zapis
        df = pd.merge(df_ads, df_pop, on="Miasto", how="inner")
        df = df[df["Populacja"] > 0]
        df["Ogloszenia_na_10k_mieszk"] = (df["Liczba_Ogloszen"] / df["Populacja"] * 10000).round(2)
        df.to_csv(CSV_FILE, index=False, encoding="utf-8-sig")

    except Exception as e:
        print(f"Błąd scrapowania: {e}")

# --- URUCHOMIENIE WĄTKU W TLE ---
@st.cache_resource
def start_background_task():
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

# Wyświetlanie tabeli z formatowaniem (linki i liczby)
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
