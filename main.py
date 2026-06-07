import os
import re
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="HowToCook Multilingual Chatbot API")

# Enable CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path("/Users/prasanthrajaratnam/.gemini/antigravity/scratch/HowToCookChatbot")
INDEX_PATH = BASE_DIR / "recipes_bilingual.json"
REPO_PATH = BASE_DIR / "recipe_repo"

# Load bilingual recipes index
recipes: List[Dict[str, Any]] = []
if INDEX_PATH.exists():
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            recipes = json.load(f)
        print(f"Loaded {len(recipes)} recipes.")
    except Exception as e:
        print(f"Error loading recipes index: {e}")
else:
    print("Warning: recipes_bilingual.json not found. Run index_recipes.py first.")

# Session memory to store conversation history and context
# Structure: { session_id: { "messages": [...], "last_suggestions": [...] } }
conversations: Dict[str, Dict[str, Any]] = {}

class ChatRequest(BaseModel):
    message: str
    sessionId: str

def is_chinese(text: str) -> bool:
    """Helper to detect if text contains Chinese characters."""
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def extract_time_limit(text: str) -> Optional[int]:
    """
    Extracts time limit in minutes from the message.
    Handles English ("30 mins", "half an hour", etc.) and Chinese ("30分钟", "半小时", etc.).
    """
    text_lower = text.lower().strip()
    
    # Check for common phrases
    if "half an hour" in text_lower or "半小时" in text_lower or "半个多小时" in text_lower:
        return 30
    if "an hour" in text_lower or "一小时" in text_lower or "一个小时" in text_lower:
        return 60
    if "one hour" in text_lower:
        return 60
    if "quarter of an hour" in text_lower or "一刻钟" in text_lower:
        return 15
        
    # Match numbers followed by mins/minutes/h/hours/分钟/小时
    # e.g., "30 min", "15m", "1.5 h", "2 小时", "45分钟"
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:min|minute|m|h|hour|分钟|分|小时|点钟)', text_lower)
    if matches:
        # Take the first matched time
        val = float(matches[0])
        # If the unit is hours
        unit_match = re.search(rf'{matches[0]}\s*(?:h|hour|小时)', text_lower)
        if unit_match:
            return int(val * 60)
        return int(val)
        
    # Match bare numbers if the user says "in 30" or "under 15"
    bare_match = re.search(r'(?:under|in|less than|低于|少于|不超过|在|限时)\s*(\d+)\s*(?!大卡|卡|g|克)', text_lower)
    if bare_match:
        return int(bare_match.group(1))
        
    return None

def clean_ingredient_name(name: Any) -> str:
    """Removes comments and quantities from ingredient text for better matching."""
    if not name or not isinstance(name, str):
        return ""
    # Remove contents in parenthesis
    name = re.sub(r'（.*?）', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    # Remove quantities (digits followed by units)
    name = re.sub(r'\d+.*$', '', name)
    return name.strip().lower()

def extract_ingredients(text: str, is_zh_lang: bool) -> List[str]:
    """
    Extracts ingredients from user message by cross-referencing with unique ingredients database.
    """
    text_clean = text.lower()
    matched_ingredients = []
    
    # Gather all unique ingredient names from our recipe database
    all_ingredients = set()
    for r in recipes:
        ing_list = r["ingredients_zh"] if is_zh_lang else r["ingredients_en"]
        for ing in ing_list:
            if ing:
                cleaned = clean_ingredient_name(ing)
                if len(cleaned) > 1: # avoid single character matches
                    all_ingredients.add(cleaned)
                
    # Sort ingredients by length descending to match longer phrases first (e.g. "five flower pork" before "pork")
    sorted_ingredients = sorted(list(all_ingredients), key=len, reverse=True)
    
    # Search for ingredient keywords in user message
    for ing in sorted_ingredients:
        # Escape for regex
        escaped_ing = re.escape(ing)
        # Use boundary check for English to avoid matching parts of words
        if not is_zh_lang:
            pattern = rf'\b{escaped_ing}s?\b'
        else:
            pattern = escaped_ing
            
        if re.search(pattern, text_clean):
            matched_ingredients.append(ing)
            # Remove matched ingredient from text to prevent sub-string double matching
            text_clean = re.sub(pattern, ' ', text_clean)
            
    return matched_ingredients

def find_recipes_by_criteria(time_limit: Optional[int], matched_ingredients: List[str], search_text: str, is_zh_lang: bool) -> List[Dict[str, Any]]:
    """Filters and ranks recipes based on time, ingredient matches, or text search."""
    filtered = []
    
    for r in recipes:
        # Time filter
        if time_limit is not None:
            if r["cooking_time_minutes"] > time_limit:
                continue
                
        # Ingredient match score
        match_count = 0
        r_ings = r["ingredients_zh"] if is_zh_lang else r["ingredients_en"]
        r_ings_clean = [clean_ingredient_name(ing) for ing in r_ings if ing]
        r_ings_clean = [c for c in r_ings_clean if c]
        
        for user_ing in matched_ingredients:
            # Check if user ingredient matches any ingredient of the recipe
            for r_ing in r_ings_clean:
                if user_ing in r_ing or r_ing in user_ing:
                    match_count += 1
                    break
                    
        match_ratio = match_count / len(r_ings_clean) if r_ings_clean else 0
        
        # Text search match (fallback if no ingredients or time limit)
        text_match = False
        name = r["name_zh"] if is_zh_lang else r["name_en"]
        intro = r["intro_zh"] if is_zh_lang else r["intro_en"]
        if search_text:
            if search_text.lower() in name.lower() or search_text.lower() in intro.lower():
                text_match = True
                
        filtered.append({
            "recipe": r,
            "match_count": match_count,
            "match_ratio": match_ratio,
            "text_match": text_match
        })
        
    # Rank results
    if matched_ingredients:
        # Filter: must match at least one ingredient if ingredients were specified
        filtered = [f for f in filtered if f["match_count"] > 0]
        # Sort by match count descending, then match ratio descending
        filtered.sort(key=lambda x: (x["match_count"], x["match_ratio"]), reverse=True)
    elif search_text and not time_limit:
        # Filter: must match search text
        filtered = [f for f in filtered if f["text_match"]]
        filtered.sort(key=lambda x: len(x["recipe"]["name_zh"])) # prefer shorter names
    else:
        # Just sorted by cooking time or difficulty
        filtered.sort(key=lambda x: x["recipe"]["cooking_time_minutes"])
        
    # Limit to 5 suggestions
    results = [f["recipe"] for f in filtered[:5]]
    return results

@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    global recipes
    # Lazy reload recipes index if empty
    if not recipes and INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                recipes = json.load(f)
        except Exception:
            pass

    user_msg = payload.message.strip()
    session_id = payload.sessionId
    
    if not user_msg:
        raise HTTPException(status_code=400, detail="Empty message")
        
    # Initialize session
    if session_id not in conversations:
        conversations[session_id] = {
            "messages": [],
            "last_suggestions": []
        }
        
    session = conversations[session_id]
    session["messages"].append({"role": "user", "content": user_msg})
    
    # Auto detect language
    is_zh = is_chinese(user_msg)
    lang = "zh" if is_zh else "en"
    
    # Check if the user is requesting steps for a recipe from previous suggestions
    recipe_index_to_show = None
    step_keywords_zh = ["步骤", "做法", "操作", "第", "怎么做", "如何做", "详细", "流程"]
    step_keywords_en = ["step", "recipe", "how to cook", "details", "instruction", "make", "prepare"]
    
    has_step_intent = False
    if is_zh:
        has_step_intent = any(k in user_msg for k in step_keywords_zh)
    else:
        has_step_intent = any(k in user_msg.lower() for k in step_keywords_en)
        
    # Try to parse index (1-5) or match recipe name
    matched_recipe = None
    
    # 1. Look for index reference in message (e.g., "1", "show recipe 2", "第3个")
    digit_match = re.search(r'\b([1-5])\b', user_msg)
    if not digit_match:
        # Chinese numbers
        cn_digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
        for k, v in cn_digits.items():
            if k in user_msg:
                recipe_index_to_show = v
                break
    else:
        recipe_index_to_show = int(digit_match.group(1))
        
    # If the user asks for steps, check if they referenced a valid index from last suggestions
    if has_step_intent and recipe_index_to_show is not None:
        idx = recipe_index_to_show - 1
        if 0 <= idx < len(session["last_suggestions"]):
            matched_recipe = session["last_suggestions"][idx]
            
    # 2. Alternatively, match a recipe name directly in the message
    if not matched_recipe:
        for r in recipes:
            name_zh = r["name_zh"].lower()
            name_en = r["name_en"].lower()
            if name_zh in user_msg.lower() or name_en in user_msg.lower():
                matched_recipe = r
                break
                
    # If we found a recipe to show details for:
    if matched_recipe:
        # Reset last suggestions so user is now focusing on this recipe
        # Return full cooking instructions
        name = matched_recipe["name_zh"] if is_zh else matched_recipe["name_en"]
        intro = matched_recipe["intro_zh"] if is_zh else matched_recipe["intro_en"]
        difficulty = "★" * matched_recipe["difficulty"] if matched_recipe["difficulty"] else "Normal"
        calories = matched_recipe["calories"]
        time_mins = matched_recipe["cooking_time_minutes"]
        ingredients = matched_recipe["ingredients_zh"] if is_zh else matched_recipe["ingredients_en"]
        ingredients = [ing for ing in ingredients if ing]
        steps = matched_recipe["steps_zh"] if is_zh else matched_recipe["steps_en"]
        images = matched_recipe["images"]
        
        # Build reply
        if is_zh:
            reply = f"### 📖 {name}\n\n"
            reply += f"*{intro}*\n\n"
            reply += f"⏱️ **预估时间**: {time_mins} 分钟 | ⭐ **难度**: {difficulty}"
            if calories:
                reply += f" | 🔥 **卡路里**: {calories} 大卡"
            reply += "\n\n#### 🛒 必备原料和工具\n"
            for ing in ingredients:
                reply += f"- {ing}\n"
            reply += "\n#### 🍳 详细操作步骤\n"
            for i, step in enumerate(steps, 1):
                reply += f"{i}. {step}\n"
        else:
            reply = f"### 📖 {name}\n\n"
            reply += f"*{intro}*\n\n"
            reply += f"⏱️ **Time**: {time_mins} mins | ⭐ **Difficulty**: {difficulty}"
            if calories:
                reply += f" | 🔥 **Calories**: {calories} kcal"
            reply += "\n\n#### 🛒 Ingredients & Tools Required\n"
            for ing in ingredients:
                reply += f"- {ing}\n"
            reply += "\n#### 🍳 Detailed Cooking Steps\n"
            for i, step in enumerate(steps, 1):
                reply += f"{i}. {step}\n"
                
        # Append images if available
        image_html = ""
        if images:
            image_html = f"\n\n![{name}](/api/images?path={images[0]})"
            reply += image_html
            
        session["messages"].append({"role": "bot", "content": reply})
        return {
            "reply": reply,
            "recipes": [matched_recipe], # Return the selected recipe in payload
            "mode": "detail"
        }
        
    # If not requesting steps, perform recipe search
    # Parse ingredients
    matched_ingredients = extract_ingredients(user_msg, is_zh)
    # Parse time limit
    time_limit = extract_time_limit(user_msg)
    
    # Perform search
    suggested = find_recipes_by_criteria(time_limit, matched_ingredients, user_msg, is_zh)
    
    # Store suggestions in session context
    session["last_suggestions"] = suggested
    
    # Build conversational short answer
    if is_zh:
        if not suggested:
            reply = "抱歉，没有找到符合您要求的菜谱。您可以尝试提供其他食材或调整烹饪时间！"
        else:
            reply = f"我为您找到了以下 **{len(suggested)}** 道精选菜谱：\n\n"
            for i, r in enumerate(suggested, 1):
                diff = "★" * r["difficulty"] if r["difficulty"] else "无"
                reply += f"{i}. **{r['name_zh']}** ({r['cooking_time_minutes']} 分钟 | 难度 {diff})\n"
            reply += "\n💡 **您可以直接点击下方的菜谱卡片**，或在聊天中输入 **“查看第几道菜的步骤”**（例如：“详细做法 1” 或 “如何做 {recipe_name}”）来获取详细烹饪步骤！"
    else:
        if not suggested:
            reply = "Sorry, I couldn't find any recipes matching your criteria. Try suggesting different ingredients or adjusting the cooking time!"
        else:
            reply = f"I found **{len(suggested)}** recommended recipe(s) for you:\n\n"
            for i, r in enumerate(suggested, 1):
                diff = "★" * r["difficulty"] if r["difficulty"] else "Normal"
                reply += f"{i}. **{r['name_en']}** ({r['cooking_time_minutes']} mins | Difficulty: {diff})\n"
            reply += "\n💡 **You can click the recipe cards below**, or reply with **\"show steps for recipe X\"** (e.g. \"show steps for 1\" or \"how to cook {recipe_name}\") to see full instructions!"
            
    session["messages"].append({"role": "bot", "content": reply})
    
    return {
        "reply": reply,
        "recipes": suggested,
        "mode": "list"
    }

@app.get("/api/images")
async def get_image(path: str = Query(..., description="Relative path of the image inside recipe_repo")):
    # Security check to prevent directory traversal
    clean_path = path.replace("\\", "/").strip("/")
    if not clean_path.startswith("dishes/"):
        raise HTTPException(status_code=400, detail="Invalid path prefix")
        
    resolved_file = (REPO_PATH / clean_path).resolve()
    if not resolved_file.exists() or not resolved_file.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
        
    # Double check it is inside the dishes folder
    try:
        resolved_file.relative_to(REPO_PATH / "dishes")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")
        
    # Determine media type
    suffix = resolved_file.suffix.lower()
    media_type = "image/jpeg"
    if suffix in [".png"]:
        media_type = "image/png"
    elif suffix in [".webp"]:
        media_type = "image/webp"
    elif suffix in [".gif"]:
        media_type = "image/gif"
        
    return FileResponse(resolved_file, media_type=media_type)

# Mount frontend static files
frontend_dir = BASE_DIR / "frontend"
if not frontend_dir.exists():
    frontend_dir.mkdir(parents=True, exist_ok=True)
    
app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
