# Football Telegram Bot V6 — STRICT TARGET + BET BUILDER

## Najważniejsza naprawa `/kupon`

Bot nie może już zwrócić kursu 13.23 dla celu 3.00.

Dla celu `3.00`:
- preferowane: `2.85–3.15` (±5%)
- awaryjne: `2.70–3.30` (±10%)
- poza `2.70–3.30`: **brak automatycznego kuponu**

Optimizer najpierw pilnuje kursu docelowego, dopiero później confidence/edge/data quality.

## Profile

- `/profil safe` → minimum 75/100
- `/profil normal` → minimum 65/100 (domyślny)
- `/profil risky` → minimum 55/100

Remis wymaga dodatkowo co najmniej 75/100. Wysoki edge nie wystarcza.

Pojedyncze kursy są ograniczone:
- SAFE: maks. ok. 2.10
- NORMAL: maks. ok. 2.50
- RISKY: maks. ok. 3.00

## Kupon

Przykłady:

```text
/kupon 3
/kupon 3 single
/kupon 3 mecze=2
/kupon 5 mecze=3
/kupon 3 1,2 3
```

Kreator `/kupon` nadal pyta o kurs, ligi i liczbę meczów. Można wpisać `AUTO`.

## Bet Builder

```text
/betbuilder
/builder
/betbuilder Arsenal Chelsea
/betbuilder Arsenal Chelsea 3.00
```

Po wyborze meczu masz przyciski:
- Gole
- Rożne
- Kartki
- Faule
- Strzały
- Strzały celne
- Spalone
- Bramkarz – obrony
- Zawodnicy
- 1X2 / Double Chance

oraz:
- cel 2.00 / 3.00 / 5.00 / własny
- 2 / 3 / 4 zdarzenia
- ANALIZUJ

Aliasy:
- `/builder_gole`
- `/builder_rogi`
- `/builder_kartki`
- `/builder_strzaly`
- `/builder_celne`
- `/builder_faule`

## Kurs Bet Buildera

Jeśli API nie zwraca oficjalnego kursu połączonego Same Game Parlay, bot pokazuje:

`📊 Orientacyjny iloczyn kursów`

oraz ostrzeżenie, że bukmacher może wycenić kombinację inaczej przez korelację.

## Korelacja

Builder posiada `correlation_score 0–100`. Kombinacje mocno skorelowane są karane w optimizerze, np.:
- gole + 1X2
- strzały + celne
- kartki + faule

## Tracker

Wygenerowany kupon jest propozycją. Do własnego yield wchodzi dopiero po:

```text
/stawiam ID
```

lub:

```text
/stawiam ID 2.5
```

## Railway

Variables:

```text
TELEGRAM_BOT_TOKEN=...
FOOTBALL_API_KEY=...
```

Volume:

```text
/data
```
