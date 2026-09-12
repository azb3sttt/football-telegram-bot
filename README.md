# Football Telegram Bot

Polski bot Telegram do analizy dzisiejszych meczów, budowania kuponów z danych API-Football oraz **transparentnego trackingu wszystkich rekomendacji**. Projekt zapisuje kupony i selekcje w SQLite, automatycznie rozlicza zakończone mecze, liczy profit/yield/ROI, wysyła raporty i zachowuje historię po restarcie.

> To narzędzie analityczne. Prognoza i `confidence` nie są gwarancją wyniku. Bot nie stosuje progresji stawek ani odrabiania strat.

## 1. Co musisz wpisać do `.env`

Po rozpakowaniu:

```bash
cp .env.example .env
```

Uzupełnij **tylko te dwie wartości**:

```env
TELEGRAM_BOT_TOKEN=TU_TOKEN_OD_BOTFATHER
FOOTBALL_API_KEY=TU_KLUCZ_API_FOOTBALL
```

Reszta ustawień ma wartości domyślne i nie wymaga zmian.

API-Football: `https://v3.football.api-sports.io/`, autoryzacja nagłówkiem `x-apisports-key`.

## 2. Najszybsze uruchomienie — Docker

```bash
docker compose up -d --build
docker compose logs -f
```

Przy poprawnym starcie zobaczysz m.in.:

```text
✅ Połączenie z Telegramem
✅ Baza danych gotowa
✅ API piłkarskie dostępne
✅ Scheduler uruchomiony
✅ Bot działa
```

Zatrzymanie:

```bash
docker compose down
```

## 3. Uruchomienie bez Dockera

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m app
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m app
```

## 4. Sprawdzenie projektu bez tokenów i API

```bash
pip install -e ".[dev]"
pytest -q
python -m app --check
```

`--check` tworzy bazę i sprawdza importy/start lokalnych komponentów bez łączenia się z Telegramem i API-Football.

## 5. Komendy Telegram

Podstawowe:

- `/start`
- `/pomoc`
- `/dzisiaj`, `/mecze`
- `/analiza ID`
- `/kupon 5.0`
- `/value`, `/top`
- `/liga`, `/druzyna NAZWA`
- `/h2h ID`
- `/rogi ID`, `/gole ID`, `/kartki ID`, `/btts ID`
- `/wyniki`, `/dzisiaj_wyniki`
- `/tydzien`, `/miesiac`, `/yield`, `/roi`, `/historia`, `/statystyki_bota`
- `/powiadomienia`, `/ustawienia`, `/status`

Najprostszy workflow:

```text
/dzisiaj
/analiza 1234567
/kupon 5.0
/wyniki
/yield
```

## 6. Automatyczne rozliczanie

Scheduler sprawdza wyłącznie fixture'y mające oczekujące selekcje. Po zakończeniu meczu pobiera wynik, a gdy rynek tego wymaga również statystyki `/fixtures/statistics`.

Obsługiwane rozliczenia:

- `1X2` — wynik 90 minut,
- `BTTS` — gole obu drużyn,
- `Over/Under` — dokładna liczba goli,
- gole drużyny,
- rzuty rożne — statystyka `Corner Kicks`,
- kartki — `Yellow Cards` + `Red Cards` liczone po 1,
- Asian Handicap — także linie ćwiartkowe przez podział stawki na dwie połowy,
- anulowane/wo — `void`,
- mecze w toku — pozostają `pending`.

Statusy bazy:

```text
pending
won
lost
push
void
```

## 7. Yield / ROI

Dla pojedynczej rekomendacji bazowa stawka statystyczna to `1 unit`:

```text
wygrany: profit = odds - 1
przegrany: profit = -1
zwrot: profit = 0
```

Yield:

```text
profit / total_staked * 100
```

`void` jest raportowany osobno i nie wchodzi do obrotu używanego do wyliczenia skuteczności/yieldu. Przy stałej stawce jednostkowej ROI i yield są liczbowo równe.

Dla kuponów AKO baza prowadzi dodatkowo wynik 1 unit na cały kupon.

## 8. Raporty automatyczne

Strefa: `Europe/Warsaw`.

- raport dzienny: domyślnie 23:10, jeśli użytkownik go włączy,
- raport tygodniowy: niedziela 20:30,
- raport miesięczny: 1. dzień miesiąca o 09:00 za poprzedni miesiąc.

`/powiadomienia` pozwala osobno przełączać:

- wynik pojedynczego typu,
- wynik całego kuponu,
- raport dzienny,
- raport tygodniowy,
- raport miesięczny.

## 9. Analiza modelowa

Wbudowany model jest celowo prosty i audytowalny. Dla meczu pobiera ostatnie 5 spotkań obu zespołów, estymuje oczekiwane gole i używa rozkładu Poissona m.in. dla 1X2, overów i BTTS. Następnie porównuje prawdopodobieństwo modelu z implikowanym prawdopodobieństwem kursu:

```text
edge = model_probability - 1 / odds
```

Typ trafia do rankingu dopiero po spełnieniu minimalnego `confidence` oraz dodatniego edge. Brak kursu z API oznacza brak rekomendacji, zamiast wymyślania kursu.

## 10. Limity API

Bot ma cache i nie odpytuje API bez potrzeby. `/kupon` i `/top` analizują ograniczoną liczbę najbliższych spotkań z popularnych lig, ponieważ głęboka analiza wszystkich lig może szybko zużyć dzienny limit tańszego planu API-Football.

Na starcie bot jednym wywołaniem synchronizuje aktualne ligi i wybiera około 20 popularnych rozgrywek, m.in. Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Ekstraklasa, europejskie puchary, Eredivisie, Primeira Liga, Süper Lig, MLS itd.

## 11. Baza danych

Domyślnie SQLite. Historia nie znika po restarcie.

Kluczowe dane selekcji:

```text
coupon_id
user_id
created_at
fixture_id
market
selection
line
odds
model_probability
confidence
edge
status
final_result
profit_loss
```

## 12. GitHub

Po rozpakowaniu projektu:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin URL_REPO
git push -u origin main
```

`.env`, pliki SQLite, cache Pythona, logi i środowiska wirtualne są wykluczone przez `.gitignore`.

## 13. Testy CI

GitHub Actions uruchamia:

```text
python -m compileall -q app tests
python -m pytest -q
python -m app --check
```

## Ważna uwaga o settlementach bukmacherskich

Różni operatorzy mogą mieć własne zasady dotyczące dogrywki, czerwonych kartek, korekt statystyk i konkretnych rynków. Ten projekt rozlicza standardowe 1X2/gole na `score.fulltime` (90 minut), a rynki statystyczne na końcowych danych API-Football. Jeśli chcesz odwzorować regulamin konkretnego bukmachera, dodaj osobny profil zasad settlementu przed użyciem wyników do formalnego audytu.
