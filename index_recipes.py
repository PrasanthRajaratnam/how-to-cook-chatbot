import os
import re
import json
import time
from pathlib import Path
from urllib.parse import unquote
from deep_translator import GoogleTranslator

BASE_DIR = Path(__file__).resolve().parent
REPO_PATH = BASE_DIR / "recipe_repo"
DISHES_PATH = REPO_PATH / "dishes"
OUTPUT_INDEX_PATH = BASE_DIR / "recipes_bilingual.json"
CACHE_PATH = BASE_DIR / "translation_cache.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Load translation cache
translation_cache = {}
if CACHE_PATH.exists():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            translation_cache = json.load(f)
        print(f"Loaded {len(translation_cache)} cached translations.")
    except Exception as e:
        print(f"Error loading cache: {e}")

def save_cache():
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(translation_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving cache: {e}")

def batch_translate(texts):
    """
    Translates a list of texts from Chinese to English.
    Uses translation cache, and translates uncached texts in batches.
    """
    if not texts:
        return []
    
    results = {}
    uncached = []
    
    for text in texts:
        trimmed = text.strip()
        if not trimmed:
            results[text] = ""
            continue
        if trimmed in translation_cache:
            results[text] = translation_cache[trimmed]
        else:
            uncached.append(trimmed)
            
    if uncached:
        print(f"Translating {len(uncached)} new strings...")
        translator = GoogleTranslator(source='zh-CN', target='en')
        
        # Batch size of 40 to avoid URL length limit
        batch_size = 40
        for i in range(0, len(uncached), batch_size):
            batch = uncached[i:i+batch_size]
            joined_str = "\n---\n".join(batch)
            try:
                translated_joined = translator.translate(joined_str)
                # Split back
                translated_parts = [p.strip() for p in translated_joined.split("---")]
                
                # Check mismatch
                if len(translated_parts) != len(batch):
                    print(f"Warning: batch size mismatch (sent {len(batch)}, got {len(translated_parts)}). Falling back to individual translation.")
                    for item in batch:
                        try:
                            translated_item = translator.translate(item)
                            translation_cache[item] = translated_item
                            results[item] = translated_item
                            time.sleep(0.2) # Avoid aggressive rate limiting
                        except Exception as ex:
                            print(f"Error translating single item '{item}': {ex}")
                            results[item] = item # fallback to Chinese
                else:
                    for orig, trans in zip(batch, translated_parts):
                        translation_cache[orig] = trans
                        results[orig] = trans
                
                # Save cache progressively
                save_cache()
                time.sleep(0.5) # rate limit prevention
            except Exception as e:
                print(f"Error translating batch: {e}. Falling back to individual translation.")
                for item in batch:
                    try:
                        translated_item = translator.translate(item)
                        translation_cache[item] = translated_item
                        results[item] = translated_item
                        time.sleep(0.3)
                    except Exception as ex:
                        print(f"Error translating single item '{item}': {ex}")
                        results[item] = item
                save_cache()
                
    return [results[t] if t.strip() else "" for t in texts]

def parse_difficulty(diff_str):
    # e.g., "预估烹饪难度：★★★" -> count stars
    stars = diff_str.count("★")
    return stars

def parse_calories(cal_str):
    # e.g., "预估卡路里：1495 大卡" -> extract digits
    match = re.search(r'(\d+)', cal_str)
    if match:
        return int(match.group(1))
    return None

def infer_time_from_text(intro, steps):
    """
    Infers cooking time in minutes.
    First tries to find in intro.
    If not found, sums durations found in steps.
    """
    # 1. Search intro for patterns
    # "全程约 1.5 小时" -> 90 mins
    intro_hour_match = re.search(r'(?:全程约|大约|只需|需要)\s*(\d+(?:\.\d+)?)\s*小时', intro)
    if intro_hour_match:
        return int(float(intro_hour_match.group(1)) * 60)
        
    intro_min_match = re.search(r'(?:全程约|大约|只需|需要)\s*(\d+)\s*分钟', intro)
    if intro_min_match:
        return int(intro_min_match.group(1))
        
    intro_generic_match = re.search(r'(\d+)\s*分钟', intro)
    if intro_generic_match:
        return int(intro_generic_match.group(1))

    # 2. Search steps
    total_mins = 0
    time_found = False
    
    for step in steps:
        # Check range first like "15 - 20 分钟" -> take 20
        range_match = re.findall(r'(\d+)\s*-\s*(\d+)\s*(分钟|小时)', step)
        if range_match:
            for item in range_match:
                val = int(item[1])
                unit = item[2]
                if unit == '小时':
                    total_mins += val * 60
                else:
                    total_mins += val
                time_found = True
            continue
            
        # Single matches like "炖 1 小时", "搅拌 10 分钟"
        matches = re.findall(r'(\d+)\s*(分钟|小时)', step)
        for val_str, unit in matches:
            val = int(val_str)
            if unit == '小时':
                total_mins += val * 60
            else:
                total_mins += val
            time_found = True
            
    if time_found and total_mins > 0:
        return total_mins
        
    return None # Fallback done on loading

def natural_sort_key(path):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', str(path))
    ]

def image_priority(path):
    name = path.name.lower()
    score = 10
    preferred_terms = ["成品", "完成", "finished", "final", "result"]
    if any(term in name for term in preferred_terms):
        score = 0
    elif path.stem in {"1", "01"}:
        score = 1
    return (score, natural_sort_key(path.name))

def image_path_to_repo_relative(image_path):
    try:
        resolved_path = image_path.resolve()
        rel_img_path = resolved_path.relative_to(REPO_PATH)
    except ValueError:
        return None
    if not resolved_path.is_file() or resolved_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    return str(rel_img_path).replace(os.sep, "/")

def extract_markdown_image_paths(content):
    image_paths = []
    for line in content.splitlines():
        if "![" not in line:
            continue
        start = 0
        while True:
            marker = line.find("](", start)
            if marker == -1:
                break
            close_candidates = [
                pos for ext in IMAGE_EXTENSIONS
                for pos in [line.lower().find(ext, marker + 2)]
                if pos != -1
            ]
            if not close_candidates:
                start = marker + 2
                continue
            ext_pos = min(close_candidates)
            close = line.find(")", ext_pos)
            if close == -1:
                break
            image_paths.append(line[marker + 2:close].strip())
            start = close + 1
    return image_paths

def discover_recipe_images(filepath, content):
    images = []
    seen = set()

    def add_image(image_path):
        if not image_path or image_path in seen:
            return
        seen.add(image_path)
        images.append(image_path)

    for img_path in extract_markdown_image_paths(content):
        clean_img_path = unquote(img_path.strip().strip("<>"))
        if clean_img_path.startswith(("http://", "https://", "www.")):
            continue
        rel_path = image_path_to_repo_relative(filepath.parent / clean_img_path)
        if rel_path:
            add_image(rel_path)

    candidate_dirs = []
    category_dir = REPO_PATH / "dishes" / filepath.relative_to(REPO_PATH / "dishes").parts[0]
    if filepath.parent != category_dir:
        candidate_dirs.append(filepath.parent)

    same_name_dir = filepath.with_suffix("")
    if same_name_dir.exists() and same_name_dir.is_dir():
        candidate_dirs.append(same_name_dir)

    for candidate_dir in dict.fromkeys(candidate_dirs):
        local_images = sorted(
            [
                item for item in candidate_dir.iterdir()
                if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS
            ],
            key=image_priority
        )
        for image_file in local_images:
            rel_path = image_path_to_repo_relative(image_file)
            if rel_path:
                add_image(rel_path)

    return images

def parse_recipe_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

    # Get relative path from REPO_PATH
    rel_path = filepath.relative_to(REPO_PATH)
    
    # Initialize fields
    name = filepath.stem
    intro = ""
    difficulty = 0
    calories = None
    ingredients = []
    steps = []
    images = []
    
    # Parse title
    title_match = re.search(r'^#\s*(.*?)(?:的做法)?\s*$', content, re.MULTILINE)
    if title_match:
        name = title_match.group(1).strip()
        
    # Extract linked images and discover sibling dish-folder photos.
    images = discover_recipe_images(filepath, content)
            
    # Split content by sections
    sections = re.split(r'^##\s+', content, flags=re.MULTILINE)
    
    # First section is intro
    intro_part = sections[0]
    # Remove HTML comments and title
    intro_clean = re.sub(r'<!--.*?-->', '', intro_part, flags=re.DOTALL)
    intro_clean = re.sub(r'^#.*$', '', intro_clean, flags=re.MULTILINE)
    intro_lines = [line.strip() for line in intro_clean.split('\n') if line.strip() and not line.strip().startswith('![')]
    intro = "\n".join(intro_lines)
    
    # Parse metadata from intro-like lines
    difficulty = 2
    difficulty_is_estimated = True
    diff_match = re.search(r'预估烹饪难度：(.*)', intro_clean)
    if diff_match:
        difficulty = parse_difficulty(diff_match.group(1))
        difficulty_is_estimated = False
        
    calories = None
    calories_is_estimated = True
    cal_match = re.search(r'预估卡路里：(.*)', intro_clean)
    if cal_match:
        calories = parse_calories(cal_match.group(1))
        calories_is_estimated = False
        
    # Process other sections
    for sec in sections[1:]:
        lines = sec.split('\n')
        sec_title = lines[0].strip()
        sec_content = '\n'.join(lines[1:])
        
        # Clean section content comments
        sec_content_clean = re.sub(r'<!--.*?-->', '', sec_content, flags=re.DOTALL)
        
        if sec_title in ["必备原料和工具", "计算"]:
            # Parse bulleted lists
            bullet_matches = re.findall(r'^[-\*\+]\s*(.*?)$', sec_content_clean, re.MULTILINE)
            for item in bullet_matches:
                item_clean = item.strip()
                if item_clean and item_clean not in ingredients:
                    ingredients.append(item_clean)
        elif sec_title in ["操作", "步骤"]:
            # Parse numbered lists
            step_matches = re.findall(r'^\d+\.\s*(.*?)$', sec_content_clean, re.MULTILINE)
            for step in step_matches:
                step_clean = step.strip()
                # Remove inline markdown image notations from steps
                step_clean = re.sub(r'!\[.*?\]\(.*?\)', '', step_clean).strip()
                if step_clean:
                    steps.append(step_clean)
                    
    # Infer time
    # Check if we have hour/minute directly in description
    intro_hour_match = re.search(r'(?:全程约|大约|只需|需要)\s*(\d+(?:\.\d+)?)\s*小时', intro)
    intro_min_match = re.search(r'(?:全程约|大约|只需|需要)\s*(\d+)\s*分钟', intro)
    intro_generic_match = re.search(r'(\d+)\s*分钟', intro)

    time_is_estimated = False
    inferred_time = None
    
    if intro_hour_match:
        inferred_time = int(float(intro_hour_match.group(1)) * 60)
    elif intro_min_match:
        inferred_time = int(intro_min_match.group(1))
    elif intro_generic_match:
        inferred_time = int(intro_generic_match.group(1))
    else:
        # We need to search steps
        inferred_time = infer_time_from_text("", steps)
        if inferred_time is not None:
            time_is_estimated = True

    if inferred_time is None:
        # Fallback based on difficulty stars
        star_to_time = {0: 10, 1: 10, 2: 15, 3: 30, 4: 45, 5: 60}
        inferred_time = star_to_time.get(difficulty, 30)
        time_is_estimated = True
        
    # Categorize time
    if inferred_time <= 15:
        time_category = "under 15 minutes"
    elif inferred_time <= 30:
        time_category = "15-30 minutes"
    elif inferred_time <= 60:
        time_category = "30-60 minutes"
    else:
        time_category = "over 60 minutes"
        
    # Category is the directory under dishes/
    parts = filepath.relative_to(REPO_PATH / "dishes").parts
    category = parts[0] if len(parts) > 0 else "unknown"
    
    return {
        "name_zh": name,
        "intro_zh": intro,
        "difficulty": difficulty,
        "difficulty_is_estimated": difficulty_is_estimated,
        "calories": calories,
        "calories_is_estimated": calories_is_estimated,
        "ingredients_zh": ingredients,
        "steps_zh": steps,
        "images": images,
        "cooking_time_minutes": inferred_time,
        "time_is_estimated": time_is_estimated,
        "time_category": time_category,
        "category": category,
        "file_path": str(rel_path)
    }

def main():
    print("Scanning dishes in HowToCook repository...")
    recipes = []
    seen_names = set()
    for root, dirs, files in os.walk(DISHES_PATH):
        for file in files:
            if file.endswith(".md") and file != "README.md":
                filepath = Path(root) / file
                recipe = parse_recipe_file(filepath)
                if recipe and recipe["ingredients_zh"] and recipe["steps_zh"]:
                    name = recipe["name_zh"]
                    # Skip duplicate recipes or template examples to keep database clean
                    if name in seen_names or "示例" in name or "template" in str(filepath).lower():
                        continue
                    seen_names.add(name)
                    recipes.append(recipe)
                    
    print(f"Found and parsed {len(recipes)} recipes.")
    
    # Now build bilingual index
    # We will gather all texts to translate:
    # 1. Names
    # 2. Intros
    # 3. All unique ingredients
    # 4. Steps
    print("Preparing translations...")
    all_texts = []
    for r in recipes:
        all_texts.append(r["name_zh"])
        all_texts.append(r["intro_zh"])
        for ing in r["ingredients_zh"]:
            all_texts.append(ing)
        for step in r["steps_zh"]:
            all_texts.append(step)
            
    # Filter unique non-empty texts to translate
    unique_texts = list(set([t.strip() for t in all_texts if t.strip()]))
    print(f"Total unique strings to translate: {len(unique_texts)}")
    
    # Run translation
    batch_translate(unique_texts)
    
    # Construct bilingual recipes list
    print("Constructing bilingual database...")
    bilingual_recipes = []
    for idx, r in enumerate(recipes, 1):
        # Translate name
        name_en = translation_cache.get(r["name_zh"].strip(), r["name_zh"])
        # Translate intro
        intro_en = translation_cache.get(r["intro_zh"].strip(), r["intro_zh"])
        # Translate ingredients
        ingredients_en = [translation_cache.get(ing.strip(), ing) for ing in r["ingredients_zh"]]
        # Translate steps
        steps_en = [translation_cache.get(step.strip(), step) for step in r["steps_zh"]]
        
        bilingual_recipes.append({
            "recipe_id": f"recipe_{idx:03d}",
            "recipe_name_original": r["name_zh"],
            "recipe_name_english": name_en,
            "category": r["category"],
            "intro_zh": r["intro_zh"],
            "intro_en": intro_en,
            "difficulty": r["difficulty"],
            "difficulty_is_estimated": r["difficulty_is_estimated"],
            "calories": r["calories"],
            "calories_is_estimated": r["calories_is_estimated"],
            "ingredients_zh": r["ingredients_zh"],
            "ingredients_en": ingredients_en,
            "steps_zh": r["steps_zh"],
            "steps_en": steps_en,
            "images": r["images"],
            "cooking_time_minutes": r["cooking_time_minutes"],
            "time_is_estimated": r["time_is_estimated"],
            "time_category": r["time_category"],
            "language": "zh",
            "file_path": r["file_path"]
        })
        
    # Save output
    with open(OUTPUT_INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(bilingual_recipes, f, ensure_ascii=False, indent=2)
    print(f"Bilingual recipe index saved to {OUTPUT_INDEX_PATH}")
    print("Done!")

if __name__ == "__main__":
    main()
