# -*- coding: utf-8 -*-
"""
test_llm_only.py
================
Quick LLM-only test — no Chrome, no scraping.
Uses hardcoded sample listing descriptions to test the LLM output directly.

Usage:
    cd /Users/Q662452/Desktop/immo
    source venv/bin/activate
    python test_llm_only.py
"""

from pathlib import Path
from dotenv import load_dotenv
from llm_personalizer import personalise_message

HERE = Path(__file__).parent
load_dotenv(HERE.parent / "config" / ".env")
TEMPLATE = (HERE.parent / "config" / "message.txt").read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Sample listings — representative of real WG-Gesucht descriptions
# ---------------------------------------------------------------------------

SAMPLES = [
    {
        "name": "Tom",
        "url": "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.123.html",
        "description": (
            "Willkommen in unserer WG! Ein Zimmer mit eigenem Balkon wird bald frei. "
            "Wenn du eine ruhige, saubere und entspannte WG-Umgebung bevorzugst, bist du bei uns genau richtig. "
            "Unsere 81qm große Wohnung bietet viel Platz mit einer Küche inklusive Geschirrspüler und Waschmaschine "
            "sowie 2 Bädern, sodass es keinen Badezimmerstau gibt. "
            "Wir sind zwei Berufstätige (28, 31) und suchen jemanden, der gerne mal zusammen kocht oder einen Film schaut, "
            "aber auch die eigene Ruhe schätzt. Rauchen nur draußen auf dem Balkon. "
            "Die WG liegt 2 Minuten vom U-Bahnhof Frankfurter Ring (U2) entfernt - ideal für BMW und die TUM."
        ),
    },
    {
        "name": "Lena",
        "url": "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.456.html",
        "description": (
            "Wir sind 3 Studentinnen (23-26) und suchen eine neue Mitbewohnerin. "
            "Wir kochen gerne zusammen, schauen Serien und gehen manchmal klettern oder zum Yoga. "
            "Wir legen sehr viel Wert auf Sauberkeit — wir haben einen Putzplan und halten ihn auch ein. "
            "Rauchen ist in der Wohnung absolut nicht erlaubt. "
            "Das Zimmer ist 14qm² und liegt im ruhigen Schwabing, 5 Minuten von der U3."
        ),
    },
    {
        "name": "Marco",
        "url": "https://www.wg-gesucht.de/wohnungen-in-Muenchen.789.html",
        "description": (
            "Ich vermiete mein möbliertes Zimmer in einer internationalen 4er WG in Maxvorstadt. "
            "Bei uns wohnen Leute aus Deutschland, Spanien und Indien. "
            "Wir sprechen meist Englisch, manchmal Deutsch. "
            "Wir respektieren die Privatsphäre der anderen, aber frühstücken gelegentlich zusammen. "
            "Nur an WOCHENENDHEIMFAHRER — das Zimmer ist Mo-Fr verfügbar."
        ),
    },
    {
        "name": "",   # no name — should fall back to generic greeting
        "url": "https://www.wg-gesucht.de/wg-zimmer-in-Muenchen.999.html",
        "description": (
            "Room in a quiet, professional flat-share near Garching Forschungszentrum. "
            "We are two PhD students working at TUM. "
            "The flat is very clean and tidy. We value privacy and a calm atmosphere. "
            "No smoking anywhere in the flat or building."
        ),
    },
    {
        "name": "Julia",
        "url": "https://www.wg-gesucht.de/wg-zimmer-in-Berlin.321.html",
        "description": (
            "Wir sind eine bunte WG aus 4 Personen (25-32) in Berlin-Mitte und suchen eine neue Mitbewohnerin oder "
            "einen neuen Mitbewohner. Die Wohnung liegt direkt am Volkspark Friedrichshain, 3 Minuten zur U5. "
            "Das freie Zimmer ist 16qm mit großem Fenster und wird unmöbliert übergeben. "
            "Wir sind alle Berufstätige in Kreativ- und Techbranchen. "
            "Gelegentlich kochen wir zusammen, ansonsten lebt jeder sein eigenes Leben. "
            "Haustiere leider nicht möglich. Einzug ab 01.08."
        ),
    },
]

# ---------------------------------------------------------------------------

SEP = "─" * 72

def run():
    print(f"\n{SEP}")
    print("  LLM Personaliser — Direct Model Test")
    print(f"{SEP}\n")

    for i, s in enumerate(SAMPLES, 1):
        print(f"\n{'═' * 72}")
        print(f"  Sample {i}/{len(SAMPLES)}")
        print(f"  Poster : {s['name'] or '(none)'}")
        print(f"  URL    : {s['url']}")
        print(f"{'═' * 72}\n")
        print("  Description:")
        print(f"  {s['description'][:200]}…\n")
        print("  Generating…\n")

        msg = personalise_message(
            template=TEMPLATE,
            listing_description=s["description"],
            poster_name=s["name"],
            listing_url=s["url"],
        )

        print(f"  ┌{'─'*68}┐")
        for line in msg.splitlines():
            print(f"  │  {line:<66}│")
        print(f"  └{'─'*68}┘\n")

    print(f"\n{SEP}")
    print("  ✅  Done. Review the messages above.")
    print(f"{SEP}\n")

if __name__ == "__main__":
    run()
