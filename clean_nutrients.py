"""
clean_nutrients.py  -  cleans the Smart Eat food dataset
=========================================================

HOW TO RUN
    python clean_nutrients.py
    python clean_nutrients.py my_input.csv my_output.csv      (optional file names)

WHAT IT DOES, STEP BY STEP
    1. Reads the raw CSV as plain text (so nothing is changed by accident)
    2. Tidies the text (extra spaces, hidden characters)
    3. Turns the number columns into real numbers
           t  or  t'   (trace amount)   ->  0
           1,419       (comma)          ->  1419
           8-44        (a range)        ->  26      (the middle)
           -1          (scan error)     ->  1
    4. Fixes typos in the Measure column      ("I med." -> "1 med.")
    5. Rebuilds food names that were split over several rows
           "Raw, grated"  (a row under Carrots)  ->  "Carrots, raw, grated"
    6. Cleans the categories (3 vegetable lists -> "Vegetables", etc.)
    7. Removes rows that cannot be trusted (placeholder numbers, impossible values)
    8. Fills small gaps and writes warnings for suspicious rows in a Notes column
    9. Saves the clean CSV and prints a short report

The columns are the same as the raw file, so the Smart Eat backend can use the
result directly. Two columns are added at the end:
    Source_Row : the row number in the raw file (so you can look the row up in Excel)
    Notes      : what was changed or what looks suspicious for that row
"""
import re
import sys
from collections import defaultdict

import pandas as pd

INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else "nutrients_csvfile_1.csv"
OUTPUT_FILE = sys.argv[2] if len(sys.argv) > 2 else "nutrients_clean.csv"

NUMBER_COLUMNS = ["Grams", "Calories", "Protein", "Fat", "Sat.Fat", "Fiber", "Carbs"]
OUTPUT_COLUMNS = ["Food", "Measure", "Grams", "Calories", "Protein", "Fat",
                  "Sat.Fat", "Fiber", "Carbs", "Category", "Source_Row", "Notes"]


# ============================================================================
#  SETTINGS  -  the decisions made about this dataset. Edit them freely.
#  "Excel row" = the row number you see when you open the raw CSV in Excel
#  (row 1 is the header, so the first food is row 2).
# ============================================================================

# --- Step 4: typos in the Measure column -----------------------------------
MEASURE_FIXES = {
    "1T.": "1 T.",                 # missing space
    "1 med": "1 med.",             # missing dot
    "I med.": "1 med.",            # letter I scanned instead of number 1
    "1 mad.": "1 med.",            # scan typo
    "8 oz. or": "8 oz.",           # text was cut off
    "aver.": "1 average serving",  # abbreviation
}
# Measures that need a different fix for one specific row only:
MEASURE_FIXES_BY_ROW = {
    37: "1 T. (2 pats)",           # Margarine: original said "2 pat or 1 T."
    225: "1-lb. loaf (20 slices)", # White bread: the "20 slices" had slipped into the name
}

# --- Step 5: food names -----------------------------------------------------
# In the raw file many rows are only part of a name ("Raw, grated" belongs to
# Carrots). Each line below says:  row: ("name now", "proper name").
# The script checks that "name now" really is what is in that row, so a wrong
# row number can never silently rename the wrong food.
NAME_FIXES = {
    # Excel row: (name as it is in the raw file,  proper name),
    2: ("Cows' milk", "Cows' milk, whole"),
    3: ("Milk skim", "Cows' milk, skim"),
    5: ("Evaporated, undiluted", "Evaporated milk, undiluted"),
    8: ("skim, instant", "Powdered milk, skim, instant"),
    9: ("skim, non-instant", "Powdered milk, skim, non-instant"),
    11: ("(1/2 cup ice cream)", "Milk drink with 1/2 cup ice cream"),
    13: ("skim. milk", "Cocoa made with skim milk"),
    14: ("(cornstarch)", "Cornstarch pudding"),
    18: ("Cream or half-and-half", "Cream, half-and-half"),
    19: ("or whipping", "Cream, whipping"),
    20: ("Cheese", "Cottage cheese, creamed"),
    21: ("uncreamed", "Cottage cheese, uncreamed"),
    22: ("Cheddar", "Cheddar cheese"),
    23: ("Cheddar, grated cup", "Cheddar cheese, grated"),
    26: ("Roquefort type", "Roquefort-type cheese"),
    27: ("Swiss", "Swiss cheese"),
    28: ("Eggs raw", "Eggs, raw"),
    29: ("Eggs Scrambled or fried", "Eggs, scrambled or fried"),
    30: ("Yolks", "Egg yolks"),
    37: ("Margarine, 2 pat or", "Margarine"),
    48: ("Ground lean", "Hamburger, ground lean"),
    50: ("Steak", "Beef steak"),
    51: ("Steak, lean, as round", "Beef steak, lean, as round"),
    53: ("Corned beef hash canned", "Corned beef hash, canned"),
    54: ("Corned beef hash Dried", "Corned beef hash, dried"),
    55: ("Pot-pie", "Beef pot pie"),
    56: ("Corned beef hash Stew", "Beef stew"),
    57: ("chicken", "Chicken"),
    58: ("Fried, breast or leg and thigh chicken", "Chicken, fried, breast or leg and thigh"),
    59: ("Roasted chicken", "Chicken, roasted"),
    63: ("Leg roasted", "Lamb, leg, roasted"),
    64: ("Shoulder, braised", "Lamb, shoulder, braised"),
    65: ("Pork, chop, 1 thick", "Pork, chop, 1 in. thick"),
    66: ("Ham pan-broiled", "Ham, pan-broiled"),
    67: ("Ham, as", "Ham, cooked"),
    73: ("Roast", "Veal, roast"),
    77: ("Fish sticks fried", "Fish sticks, fried"),
    95: ("Beans", "Beans, green"),
    96: ("Lima", "Beans, lima"),
    97: ("Lima, dry, cooked", "Beans, lima, dry, cooked"),
    98: ("Navy, baked with pork", "Beans, navy, baked with pork"),
    99: ("Red kidney", "Beans, red kidney"),
    106: ("Steamed cabbage", "Cabbage, steamed"),
    108: ("Raw, grated", "Carrots, raw, grated"),
    109: ("Strips, from raw", "Carrots, strips, from raw"),
    112: ("Stalk raw", "Celery, stalk, raw"),
    113: ("Chard steamed", "Chard, steamed"),
    116: ("cooked or canned", "Corn, cooked or canned"),
    123: ("Lambs quarters, steamed", "Lamb's-quarters, steamed"),
    126: ("Iceberg", "Lettuce, iceberg"),
    127: ("Mushrooms canned", "Mushrooms, canned"),
    131: ("Raw, green", "Onions, raw, green"),
    135: ("Fresh, steamed peas", "Peas, fresh, steamed"),
    136: ("Frozen peas", "Peas, frozen"),
    137: ("Split cooked peas", "Peas, split, cooked"),
    138: ("heated peas", "Peas, heated"),
    139: ("Peppers canned", "Peppers, canned"),
    140: ("Peppers Raw, green, sweet", "Peppers, raw, green, sweet"),
    143: ("French-fried", "Potatoes, french-fried"),
    144: ("Potatoes Mashed with milk and butter", "Potatoes, mashed, with milk and butter"),
    145: ("Potatoes, pan-tried", "Potatoes, pan-fried"),
    146: ("Scalloped with cheese potatoes", "Potatoes, scalloped, with cheese"),
    147: ("Steamed potatoes before peeling", "Potatoes, steamed, before peeling"),
    154: ("Winter, mashed", "Squash, winter, mashed"),
    156: ("Candied", "Sweet potatoes, candied"),
    158: ("Raw, 2 by 2 1/2", "Tomatoes, raw, 2 by 2 1/2 in."),
    164: ("Apple juice canned", "Apple juice, canned"),
    167: ("Stewed or canned", "Apples, stewed or canned"),
    169: ("Dried, uncooked", "Apricots, dried, uncooked"),
    170: ("Fresh", "Apricots, fresh"),
    171: ("Nectar, or juice", "Apricot nectar or juice"),
    178: ("Fresh, raw", "Cherries, fresh, raw"),
    179: ("Cranberry sauce sweetened", "Cranberry sauce, sweetened"),
    181: ("Figs", "Figs, dried"),
    182: ("Fresh, raw figs", "Figs, fresh, raw"),
    183: ("figs Canned with syrup", "Figs, canned, with syrup"),
    186: ("Grapefruit, fresh, 5\" diameter", "Grapefruit, fresh, 5 in. diameter"),
    189: ("European, as Muscat, Tokay", "Grapes, European (Muscat, Tokay)"),
    192: ("Lemonade concentratefrozen", "Lemonade concentrate, frozen"),
    193: ("Limeade concentrate frozen", "Limeade concentrate, frozen"),
    194: ("Olives large", "Olives, large"),
    195: ("OlivesRipe", "Olives, ripe"),
    196: ("Oranges 3\" diameter", "Oranges, 3 in. diameter"),
    198: ("Frozen", "Orange juice, frozen"),
    201: ("Fresh, raw", "Peaches, fresh, raw"),
    203: ("Raw, 3 by 2V", "Pears, raw, 3 by 2 1/2 in."),
    206: ("Pineapple Crushed", "Pineapple, crushed"),
    207: ("Raw, diced", "Pineapple, raw, diced"),
    210: ("Raw, 2\" diameter", "Plums, raw, 2 in. diameter"),
    215: ("Raw, red", "Raspberries, raw, red"),
    216: ("Rhubarb sweetened", "Rhubarb, sweetened"),
    218: ("Raw", "Strawberries, raw"),
    224: ("Rye", "Bread, rye"),
    225: ("White, 20 slices, or", "Bread, white"),
    226: ("Whole-wheat", "Bread, whole-wheat"),
    227: ("Whole-wheat", "Bread, whole-wheat"),
    228: ("Corn bread ground meal", "Corn bread, ground meal"),
    230: ("Corn grits cooked", "Corn grits, cooked"),
    233: ("Soda, 2 1/2 square", "Crackers, soda, 2 1/2 in. square"),
    235: ("Flour", "Flour, soybean"),
    236: ("Wheat (all purpose)", "Flour, wheat (all purpose)"),
    237: ("Wheat (whole)", "Flour, wheat (whole)"),
    239: ("Baked with cheese", "Macaroni, baked with cheese"),
    243: ("Pancakes 4\" diam.", "Pancakes, 4 in. diameter"),
    244: ("Wheat, pancakes 4\" diam.", "Pancakes, wheat, 4 in. diameter"),
    245: ("Pizza 14\" diam.", "Pizza, 14 in. diameter"),
    246: ("Popcorn salted", "Popcorn, salted"),
    248: ("Puffed wheat presweetened", "Puffed wheat, presweetened"),
    250: ("Converted", "Rice, converted"),
    251: ("White", "Rice, white"),
    255: ("of refined flour", "Rolls, refined flour"),
    256: ("whole-wheat", "Rolls, whole-wheat"),
    258: ("with tomatoes and cheese", "Spaghetti with tomatoes and cheese"),
    263: ("Wheat-germ cereal toasted", "Wheat-germ cereal, toasted"),
    264: ("Wheat meal cereal unrefined", "Wheat meal cereal, unrefined"),
    266: ("Bean soups", "Bean soup"),
    269: ("chicken soup", "Chicken soup"),
    271: ("Cream soups", "Cream soup"),
    272: ("Noodle", "Noodle soup"),
    275: ("Vegetable", "Vegetable soup"),
    278: ("Cakes", "Cake"),
    279: ("Chocolate fudge", "Chocolate fudge cake"),
    283: ("Plain, with no icing", "Cake, plain, with no icing"),
    297: ("preserves", "Preserves"),
    300: ("Cane Syrup", "Cane syrup"),
    301: ("9\" diam. pie", "Apple pie, 9 in. diameter"),
    302: ("Cherry Pie", "Cherry pie"),
    303: ("Custard", "Custard pie"),
    304: ("Lemon meringue", "Lemon meringue pie"),
    305: ("Mince", "Mince pie"),
    306: ("Pumpkin Pie", "Pumpkin pie"),
    307: ("Puddings Sugar", "Sugar, white, granulated"),
    308: ("3 teaspoons sugar", "Sugar, white, 3 teaspoons"),
    309: ("Brown, firm-packed, dark sugar", "Sugar, brown, dark, firm-packed"),
    311: ("table blends sugar", "Syrup, table blends"),
    314: ("roasted and salted", "Almonds, roasted and salted"),
    317: ("coconut sweetened", "Coconut, sweetened"),
    327: ("Wines", "Wine"),
    328: ("Table (12.2% alcohol)", "Wine, table (12.2% alcohol)"),
    329: ("Carbonated drinks Artificially sweetened", "Carbonated drink, artificially sweetened"),
}

# Names above that are my best reading of a broken name (for example "Flour" with
# 39 g protein is almost certainly soybean flour). They get a "please check" note.
UNSURE_NAMES = {11, 13, 14, 20, 21, 55, 56, 67, 95, 181, 203, 235, 279, 301}

# --- Step 6: categories -----------------------------------------------------
CATEGORY_NAMES = {
    "Dairy products": "Dairy & Eggs",
    "Fats, Oils, Shortenings": "Fats & Oils",
    "Meat, Poultry": "Meat & Poultry",
    "Fish, Seafood": "Fish & Seafood",
    "Vegetables A-E": "Vegetables",          # the A-E / F-P / R-Z split was only
    "Vegetables F-P": "Vegetables",          # the page layout of the printed table
    "Vegetables R-Z": "Vegetables",
    "Fruits A-F": "Fruits",
    "Fruits G-P": "Fruits",
    "Fruits R-Z": "Fruits",
    "Breads, cereals, fastfood,grains": "Breads, Cereals & Grains",
    "Soups": "Soups",
    "Desserts, sweets": "Desserts & Sweets",
    "Jams, Jellies": "Sweeteners, Jams & Syrups",
    "Seeds and Nuts": "Nuts & Seeds",
    "Drinks,Alcohol, Beverages": "Drinks & Alcohol",
}
# Rows that were put in the wrong category (checked against the food itself):
CATEGORY_FIXES_BY_ROW = {
    30: "Dairy & Eggs",                  # Egg yolks were listed under Fats
    163: "Vegetables",                   # Watercress was listed under Fruits
    307: "Sweeteners, Jams & Syrups",    # Sugar was listed under Desserts
    308: "Sweeteners, Jams & Syrups",
}

# --- Step 8: when is a row "suspicious"? ------------------------------------
# Calories should be close to  4 x protein + 4 x carbs + 9 x fat.
# A row is flagged when it is off by more than BOTH of these:
CALORIE_GAP_KCAL = 60
CALORIE_GAP_PERCENT = 40
DRINKS = "Drinks & Alcohol"   # alcohol has calories that are not in these macros, so skip drinks


# ============================================================================
#  HELPER FUNCTIONS
# ============================================================================

notes = defaultdict(list)   # row index -> list of notes about that row


def add_note(index, text):
    notes[index].append(text)


def tidy_text(text):
    """Remove hidden non-breaking spaces and repeated spaces."""
    return " ".join(str(text).replace("\xa0", " ").split())


def clean_number(text, column):
    """Turn one cell into a number.  Returns (number, note).
    number is None if the cell is empty or unreadable."""
    text = text.strip().replace(",", "")          # 1,419 -> 1419

    if text == "":
        return None, None

    if text.lower() in ("t", "t'"):               # 't' means "trace amount"
        return 0.0, None

    as_range = re.fullmatch(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text)
    if as_range:                                  # 8-44 -> 26
        middle = (float(as_range[1]) + float(as_range[2])) / 2
        return middle, f"{column} was the range '{text}', used the middle value {middle:g}"

    if re.fullmatch(r"-\d+(?:\.\d+)?", text):     # -1 -> 1 (a nutrient cannot be negative)
        return float(text[1:]), f"{column} was '{text}' (minus sign is a scan error), used {text[1:]}"

    try:
        return float(text), None
    except ValueError:                            # e.g. the letter 'a'
        return None, f"{column} was unreadable ('{text}')"


# ============================================================================
#  THE CLEANING STEPS
# ============================================================================

def step2_tidy_text(df):
    for column in ["Food", "Measure", "Category"]:
        df[column] = df[column].map(tidy_text)
    return df


def step3_fix_numbers(df):
    trace_count = 0
    for column in NUMBER_COLUMNS:
        cleaned = []
        for index, text in df[column].items():
            if text.strip().lower() in ("t", "t'"):
                trace_count += 1
            number, note = clean_number(text, column)
            if note:
                add_note(index, note)
            cleaned.append(number)
        df[column] = cleaned
    print(f"  Step 3: numbers converted ({trace_count} trace 't' values became 0)")
    return df


def step4_fix_measures(df):
    changed = 0
    for index, measure in df["Measure"].items():
        excel_row = index + 2
        new = MEASURE_FIXES_BY_ROW.get(excel_row) or MEASURE_FIXES.get(measure)

        # A measure that is only a number ("2", "1/2") gets a unit so it reads well
        if new is None and re.fullmatch(r"[\d/ ]+", measure):
            new = f"{measure} piece" if measure in ("1", "1/2") else f"{measure} pieces"

        if new and new != measure:
            df.at[index, "Measure"] = new
            changed += 1
    print(f"  Step 4: {changed} measures fixed")
    return df


def step5_fix_names(df):
    renamed = 0
    for excel_row, (old_name, new_name) in NAME_FIXES.items():
        index = excel_row - 2
        if df.at[index, "Food"] != old_name:       # safety check
            print(f"    WARNING: row {excel_row} is '{df.at[index, 'Food']}', expected '{old_name}' - skipped")
            continue
        df.at[index, "Food"] = new_name
        renamed += 1
        add_note(index, f"name rebuilt (was '{old_name}')")
        if excel_row in UNSURE_NAMES:
            add_note(index, "please check this name - it is a best guess")

    # Safety net: every name should start with a capital letter
    df["Food"] = df["Food"].map(lambda name: name[0].upper() + name[1:])
    print(f"  Step 5: {renamed} food names rebuilt")
    return df


def step6_fix_categories(df):
    df["Category"] = df["Category"].map(lambda c: CATEGORY_NAMES.get(c, c))
    for excel_row, category in CATEGORY_FIXES_BY_ROW.items():
        df.at[excel_row - 2, "Category"] = category
    unknown = sorted(set(df["Category"]) - set(CATEGORY_NAMES.values()))
    if unknown:
        print("    WARNING: unknown categories:", unknown)
    print(f"  Step 6: categories reduced to {df['Category'].nunique()}")
    return df


def find_unusable_rows(df):
    """Return {row index: reason} for rows that cannot be trusted."""
    problems = {}
    for index, row in df.iterrows():
        numbers = [row[c] for c in NUMBER_COLUMNS]

        # Placeholder rows look like 112, 113, 114, 115, ... (each number +1)
        if all(pd.notna(n) for n in numbers) and all(b == a + 1 for a, b in zip(numbers, numbers[1:])):
            problems[index] = "numbers are a placeholder sequence (e.g. 112, 113, 114 ...)"
        elif pd.isna(row["Grams"]) or row["Grams"] <= 0:
            problems[index] = "serving weight (Grams) is missing or zero"
        elif (row["Category"] != DRINKS and row["Calories"] > 20
              and row["Protein"] + row["Fat"] + row["Carbs"] == 0):
            problems[index] = "has calories but protein, fat and carbs are all 0"
    return problems


def step7_remove_bad_rows(df):
    problems = find_unusable_rows(df)
    for index, reason in problems.items():
        print(f"    removed row {index + 2}: {df.at[index, 'Food']} ({df.at[index, 'Measure']}) - {reason}")
    df = df.drop(index=list(problems))

    duplicates = df.duplicated(subset=["Food", "Measure"] + NUMBER_COLUMNS, keep="first")
    for index in df[duplicates].index:
        print(f"    removed row {index + 2}: {df.at[index, 'Food']} - exact duplicate")
    df = df[~duplicates]
    print(f"  Step 7: {len(problems) + int(duplicates.sum())} rows removed")
    return df


def step8_fill_gaps_and_flag(df):
    for index, row in df.iterrows():
        # Missing calories -> estimate from protein, carbs and fat
        if pd.isna(row["Calories"]):
            estimate = round(4 * row["Protein"] + 4 * row["Carbs"] + 9 * row["Fat"])
            df.at[index, "Calories"] = estimate
            add_note(index, f"Calories were missing, estimated as {estimate} from protein, carbs and fat")

        # Missing fiber -> 0
        if pd.isna(row["Fiber"]):
            df.at[index, "Fiber"] = 0
            add_note(index, "Fiber was missing, set to 0")

        # Missing saturated fat -> 0 if the food has almost no fat, otherwise leave it empty
        if pd.isna(row["Sat.Fat"]):
            if row["Fat"] < 1:
                df.at[index, "Sat.Fat"] = 0
                add_note(index, "Sat.Fat was missing, set to 0 (food has almost no fat)")
            else:
                add_note(index, "Sat.Fat is missing in the raw data and was left empty")

        # Warning: calories do not match the macros
        expected = 4 * row["Protein"] + 4 * row["Carbs"] + 9 * row["Fat"]
        gap = abs(df.at[index, "Calories"] - expected)
        if row["Category"] != DRINKS and gap > CALORIE_GAP_KCAL and gap > expected * CALORIE_GAP_PERCENT / 100:
            add_note(index, f"CHECK: calories ({df.at[index, 'Calories']:g}) do not match protein/carbs/fat "
                            f"(which add up to about {expected:g}) - one of the values is probably a scan error")

        # Warning: saturated fat can never be higher than total fat
        if pd.notna(df.at[index, "Sat.Fat"]) and df.at[index, "Sat.Fat"] > row["Fat"] + 0.5:
            add_note(index, f"CHECK: Sat.Fat ({df.at[index, 'Sat.Fat']:g}) is higher than Fat ({row['Fat']:g})")
    return df


# ============================================================================
#  MAIN
# ============================================================================

def main():
    # Step 1: read everything as text
    df = pd.read_csv(INPUT_FILE, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    rows_before = len(df)
    df["Source_Row"] = df.index + 2          # Excel row number in the raw file
    print(f"Read {rows_before} rows from {INPUT_FILE}")
    
    print("Cleaning:")
    df = step2_tidy_text(df)
    df = step3_fix_numbers(df)
    df = step4_fix_measures(df)
    df = step5_fix_names(df)
    df = step6_fix_categories(df)
    df = step7_remove_bad_rows(df)
    df = step8_fill_gaps_and_flag(df)

    # Step 9: save
    df["Notes"] = [" | ".join(notes[i]) for i in df.index]
    df = df[OUTPUT_COLUMNS]
    df.to_csv(OUTPUT_FILE, index=False, float_format="%g", encoding="utf-8")

    needs_check = df[df["Notes"].str.contains("CHECK|please check|left empty", regex=True)]
    print(f"\nSaved {len(df)} clean rows to {OUTPUT_FILE}  ({rows_before - len(df)} removed)")
    print(f"\n{len(needs_check)} rows are worth a quick look (see the Notes column):")
    for _, row in needs_check.iterrows():
        print(f"  row {row['Source_Row']:>3}  {row['Food']:<40} {row['Notes'].split(' | ')[-1][:90]}")


if __name__ == "__main__":
    main()
