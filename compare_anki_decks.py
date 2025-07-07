import os
import re
import zipfile
import sqlite3
from collections import defaultdict
from bs4 import BeautifulSoup
import json
from pathlib import Path

def extract_notes_from_apkg(apkg_path):
    """Extract notes and card metadata from an .apkg file."""
    with zipfile.ZipFile(apkg_path, 'r') as zipf:
        zipf.extract('collection.anki21', path='.')
    conn = sqlite3.connect('collection.anki21')
    cur = conn.cursor()

    # Load notes
    cur.execute("SELECT id, guid, mid, tags, flds FROM notes")
    notes_raw = cur.fetchall()
    notes_by_id = {note[0]: note for note in notes_raw}

    # Load cards and deck info
    cur.execute("SELECT nid, did, queue FROM cards")  # nid = note id, did = deck id
    cards = cur.fetchall()

    # Load deck names from deck table
    cur.execute("SELECT decks FROM col")
    import json
    decks = json.loads(cur.fetchone()[0])  # dict: id -> deck info
    deck_names = {int(k): v['name'] for k, v in decks.items()}

    # Map note_id to list of its (deck_name, suspended) cards
    note_card_info = defaultdict(list)
    for nid, did, queue in cards:
        deck_name = deck_names.get(did, "")
        suspended = (queue == -1)
        note_card_info[nid].append((deck_name, suspended))

    cur.close()
    conn.close()
    os.remove('collection.anki21')

    # Filter notes: exclude suspended notes unless they're in Genki lesson 9+
    filtered_notes = []
    for nid, note in notes_by_id.items():
        cards = note_card_info.get(nid, [])
        keep = False
        for deck_name, suspended in cards:
            if not suspended:
                keep = True
                break
            if "Genki" in deck_name:
                lesson_match = re.search(r"Lesson (\d+)", deck_name)
                if lesson_match and int(lesson_match.group(1)) >= 9:
                    keep = True
                    break
        if keep:
            filtered_notes.append(note)

    return filtered_notes


def parse_main_deck(notes):
    vocab_set = set()
    extra_words = []
    for note in notes:
        fields = note[4].split('\x1f')  # Anki field separator
        if len(fields) < 2:
            continue
        word_kanji = fields[0].strip()
        word_reading = fields[1].strip()
        vocab_set.add((word_kanji, word_reading))
        extra_words.append({
            "Word": word_kanji,
            "Reading": word_reading
        })
    return vocab_set, extra_words

def clean_ruby_html(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    kanji = ""
    reading = ""

    for ruby in soup.find_all("ruby"):
        rt = ruby.find("rt")
        if rt:
            kanji += ruby.text.replace(rt.text, "")
            reading += rt.text.replace("(", "").replace(")", "")
    if not kanji:
        # Kana-only word
        kanji = soup.get_text()
        reading = kanji
    return kanji.strip(), reading.strip()

def parse_genki_deck(notes):
    lessons = defaultdict(list)
    for note in notes:
        fields = note[4].split('\x1f')
        if len(fields) < 1:
            continue
        question = fields[0].strip()
        match = re.search(r'::(Lesson \d+)', note[3])  # note[3] = tags may contain deck info
        lesson = match.group(1) if match else "Unknown"

        kanji, reading = clean_ruby_html(question)
        lessons[lesson].append((kanji, reading))
    return lessons
  
def parse_genki_deck(notes: list[tuple]) -> dict[str, list[str]]:
    lesson_vocab: dict[str, list[str]] = {}

    for note in notes:
        note_id, guid, mid, deck_name, fields_raw = note

        # Normalize lesson name
        lesson = normalize_genki_lesson_name(deck_name)
        if lesson is None:
            lesson = "Unknown"

        # Extract vocab entry: usually first field before the first \x1f
        fields = fields_raw.split("\x1f")
        question = fields[0].strip()
        vocab = clean_ruby_html(question)
        if not vocab:
            continue

        lesson_vocab.setdefault(lesson, []).append(vocab)

    return lesson_vocab


def normalize_genki_lesson_name(deck_name: str) -> str | None:
    deck_name = deck_name.strip().lower()

    # Match strings like "genki lesson-1" or "genki lesson 1"
    match = re.search(r'genki[\s\-]?lesson[\s\-]?(\d+)', deck_name)
    if match:
        lesson_num = match.group(1).zfill(2)  # zero-pad to 2 digits
        return f"Genki 1::Lesson {lesson_num}"
    
    return None  # unmatched deck

def compare_vocab(main_vocab, genki_vocab_by_lesson):
    missing_by_lesson = defaultdict(list)
    found_words = set()

    for lesson, words in sorted(genki_vocab_by_lesson.items()):
        for kanji, reading in words:
            if (kanji, reading) in main_vocab or (kanji, "") in main_vocab or ("", reading) in main_vocab:
                found_words.add((kanji, reading))
            else:
                missing_by_lesson[lesson].append({"Word": kanji, "Reading": reading})
    
    extras = [entry for entry in main_vocab if entry not in found_words]
    return missing_by_lesson, extras

def summarize_results(missing_by_lesson: dict, extras: list, max_preview=5):
    print("\n📘 Missing Words by Genki Lesson:")
    total_missing = 0

    for lesson, entries in sorted(missing_by_lesson.items()):
        count = len(entries)
        total_missing += count
        print(f"\n📖 {lesson} — {count} missing word(s)")
        preview = entries[:max_preview]
        print([x['Word'] for x in preview])

        if count > max_preview:
            print(f"... and {count - max_preview} more")

    print(f"\n🧾 Total Missing: {total_missing}")

    print("\n🗃️ Extra Words in Main Deck (not in Genki):")
    print(f"Total Extras: {len(extras)}")
    if extras:
        preview = extras[:max_preview]
        print([w for w, r in preview])
        if len(extras) > max_preview:
            print(f"... and {len(extras) - max_preview} more")

def save_full_results_to_file(missing_by_lesson: dict, extras: list, path="out/vocab_diff_results.json"):
    output = {
        "missing_by_lesson": missing_by_lesson,
        "extra_vocab": [{"Word": k, "Reading": r} for k, r in extras],
    }

    out_path = Path(path)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Full results saved to: {out_path.resolve()}")
    
if __name__ == "__main__":
    main_apkg = r'./data/Kaishi1.5k.apkg'#input("Path to Main deck .apkg: ").strip()
    genki_apkg = r'./data/Genki1.apkg'#input("Path to Genki deck .apkg: ").strip()

    print("Extracting and parsing Main deck...")
    main_notes = extract_notes_from_apkg(main_apkg)
    # Number of extracted notes in pretty output:
    print(f"Extracted {len(main_notes)} notes from Main deck")
    main_vocab, main_raw = parse_main_deck(main_notes)

    print("Extracting and parsing Genki deck...")
    genki_notes = extract_notes_from_apkg(genki_apkg)
    print(f"Extracted {len(main_notes)} notes from Genki deck")
    genki_vocab = parse_genki_deck(genki_notes)

    print("Comparing vocabularies...")
    missing, extras = compare_vocab(main_vocab, genki_vocab)

    summarize_results(missing, extras)
    save_full_results_to_file(missing, extras)
