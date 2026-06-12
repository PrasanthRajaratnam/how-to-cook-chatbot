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

BASE_DIR = Path(__file__).resolve().parent
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
    ingredients: Optional[List[str]] = None
    timeLimit: Optional[int] = None
    difficulty: Optional[int] = None
    language: Optional[str] = None
    category: Optional[str] = None

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
    matches = re.findall(r'(\d+(?:\.\d+)?)\s*(?:min|minute|m|h|hour|分钟|分|小时|点钟)', text_lower)
    if matches:
        val = float(matches[0])
        unit_match = re.search(rf'{matches[0]}\s*(?:h|hour|小时)', text_lower)
        if unit_match:
            return int(val * 60)
        return int(val)
        
    bare_match = re.search(r'(?:under|in|less than|低于|少于|不超过|在|限时)\s*(\d+)\s*(?!大卡|卡|g|克)', text_lower)
    if bare_match:
        return int(bare_match.group(1))
        
    return None

def clean_ingredient_name(name: Any) -> str:
    """Removes comments and quantities from ingredient text for better matching."""
    if not name or not isinstance(name, str):
        return ""
    name = re.sub(r'（.*?）', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'\d+.*$', '', name)
    return name.strip().lower()

def extract_ingredients(text: str, is_zh_lang: bool) -> List[str]:
    """
    Extracts ingredients from user message by cross-referencing with unique ingredients database.
    """
    text_clean = text.lower()
    matched_ingredients = []
    
    all_ingredients = set()
    for r in recipes:
        ing_list = r["ingredients_zh"] if is_zh_lang else r["ingredients_en"]
        for ing in ing_list:
            if ing:
                cleaned = clean_ingredient_name(ing)
                if len(cleaned) > 1:
                    all_ingredients.add(cleaned)
                
    sorted_ingredients = sorted(list(all_ingredients), key=len, reverse=True)
    
    for ing in sorted_ingredients:
        escaped_ing = re.escape(ing)
        if not is_zh_lang:
            pattern = rf'\b{escaped_ing}s?\b'
        else:
            pattern = escaped_ing
            
        if re.search(pattern, text_clean):
            matched_ingredients.append(ing)
            text_clean = re.sub(pattern, ' ', text_clean)
            
    return matched_ingredients

def compute_semantic_similarity(query: str, name: str, intro: str) -> float:
    """Computes a simple term frequency match score between the query and recipe text."""
    query_clean = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query.lower())
    query_words = [w.strip() for w in re.split(r'[\s,，.。!！?？]+', query_clean) if w.strip()]
    if not query_words:
        return 1.0  # default to full score if no search query text
    matches = 0
    total_len = len(query_words)
    for qw in query_words:
        if len(qw) > 1 or is_chinese(qw):
            if qw in name.lower() or qw in intro.lower():
                matches += 1
    return matches / total_len if total_len > 0 else 1.0

def rank_recipes(
    query_message: str,
    selected_ingredients: Optional[List[str]],
    time_limit: Optional[int],
    difficulty_pref: Optional[int],
    category_pref: Optional[str],
    is_zh_lang: bool
) -> List[Dict[str, Any]]:
    """Ranks recipes using the explainable formula: 0.45*ingredients + 0.30*semantic + 0.15*time + 0.10*difficulty."""
    ranked = []
    
    user_ings_clean = []
    if selected_ingredients:
        user_ings_clean = [clean_ingredient_name(ing) for ing in selected_ingredients if ing]
        
    for r in recipes:
        # Category filter
        if category_pref and category_pref != "all":
            if r["category"] != category_pref:
                continue

        # 1. Ingredient Match Score (0.45)
        r_ings = r["ingredients_zh"] if is_zh_lang else r["ingredients_en"]
        r_ings_clean = [clean_ingredient_name(ing) for ing in r_ings if ing]
        r_ings_clean = [c for c in r_ings_clean if c]
        
        match_count = 0
        matched_list = []
        if user_ings_clean:
            for user_ing in user_ings_clean:
                for idx, r_ing in enumerate(r_ings_clean):
                    if user_ing in r_ing or r_ing in user_ing:
                        match_count += 1
                        matched_list.append(r_ings[idx])
                        break
            ingredient_match_score = match_count / len(r_ings_clean) if r_ings_clean else 0.0
        else:
            ingredient_match_score = 1.0  # default

        # 2. Semantic Similarity Score (0.30)
        name = r["recipe_name_original"] if is_zh_lang else r["recipe_name_english"]
        intro = r["intro_zh"] if is_zh_lang else r["intro_en"]
        semantic_score = compute_semantic_similarity(query_message, name, intro)
        
        # 3. Time Constraint Match Score (0.15)
        time_minutes = r["cooking_time_minutes"]
        if time_limit:
            if time_minutes <= time_limit:
                time_score = 1.0
            else:
                time_score = max(0.0, 1.0 - (time_minutes - time_limit) / time_limit)
        else:
            time_score = 1.0
            
        # 4. Difficulty Preference Match Score (0.10)
        diff_level = r["difficulty"]
        if difficulty_pref:
            if diff_level <= difficulty_pref:
                diff_score = 1.0
            else:
                diff_score = max(0.0, 1.0 - (diff_level - difficulty_pref) / 5.0)
        else:
            diff_score = 1.0
            
        # Final weighted score
        final_score = (
            0.45 * ingredient_match_score +
            0.30 * semantic_score +
            0.15 * time_score +
            0.10 * diff_score
        )
        
        # Explainability text
        if is_zh_lang:
            why_details = []
            if match_count > 0:
                why_details.append(f"匹配了 {match_count} 种您有的食材 ({'、'.join(matched_list[:3])})")
            if time_limit and time_minutes <= time_limit:
                why_details.append(f"烹饪时间只需 {time_minutes} 分钟，符合您的时间要求")
            if difficulty_pref and diff_level <= difficulty_pref:
                why_details.append("难度低，适合新手制作")
            why_recommended = "。".join(why_details) + "。" if why_details else "推荐这道经典美味菜谱。"
        else:
            why_details = []
            if match_count > 0:
                why_details.append(f"matches {match_count} of your ingredients ({', '.join(matched_list[:3])})")
            if time_limit and time_minutes <= time_limit:
                why_details.append(f"takes only {time_minutes} minutes, fitting your time limit")
            if difficulty_pref and diff_level <= difficulty_pref:
                why_details.append("is beginner-friendly")
            why_recommended = "Recommended because it " + ", and ".join(why_details) + "." if why_details else "Recommended classic recipe."

        ranked.append({
            "recipe": r,
            "scores": {
                "ingredient_match": round(ingredient_match_score * 100, 1),
                "semantic_similarity": round(semantic_score * 100, 1),
                "time_match": round(time_score * 100, 1),
                "difficulty_match": round(diff_score * 100, 1),
                "final_score": round(final_score * 100, 1)
            },
            "why_recommended": why_recommended
        })
        
    if user_ings_clean:
        ranked = [item for item in ranked if item["scores"]["ingredient_match"] > 0]
        
    ranked.sort(key=lambda x: x["scores"]["final_score"], reverse=True)
    return ranked[:5]

def generate_structured_rag_response(recipe: Dict[str, Any], why_recommended: str, scores: Dict[str, float], is_zh: bool) -> str:
    """Generates a structured RAG-style grounded assistant response."""
    name = recipe["recipe_name_original"] if is_zh else recipe["recipe_name_english"]
    intro = recipe["intro_zh"] if is_zh else recipe["intro_en"]
    difficulty_stars = "★" * recipe["difficulty"] if recipe["difficulty"] else "Normal"
    
    diff_source = "original dataset" if not recipe["difficulty_is_estimated"] else "estimated fallback"
    difficulty_label = f"{difficulty_stars} ({diff_source} field)"
    
    time_mins = recipe["cooking_time_minutes"]
    time_source = "original dataset" if not recipe["time_is_estimated"] else "inferred from steps"
    time_label = f"{time_mins} minutes ({time_source} field)"
    
    calories = recipe["calories"]
    if calories:
        calories_source = "original dataset" if not recipe["calories_is_estimated"] else "estimated"
        calories_label = f"{calories} kcal ({calories_source} field)"
    else:
        calories_label = "Not specified" if not is_zh else "未指定"
        
    ingredients = recipe["ingredients_zh"] if is_zh else recipe["ingredients_en"]
    steps = recipe["steps_zh"] if is_zh else recipe["steps_en"]
    source_file = recipe["file_path"]
    
    # Filter empty items
    ingredients = [ing for ing in ingredients if ing]
    steps = [step for step in steps if step]
    
    if is_zh:
        res = f"### 🤖 HowToCook AI RAG 助手推荐\n\n"
        res += f"**推荐菜谱**: {name}\n\n"
        res += f"**为什么匹配**: {why_recommended} (综合推荐得分: **{scores['final_score']}%**)\n"
        res += f"  - 食材匹配度: {scores['ingredient_match']}%\n"
        res += f"  - 语义相关度: {scores['semantic_similarity']}%\n"
        res += f"  - 时间符合度: {scores['time_match']}%\n"
        res += f"  - 难度符合度: {scores['difficulty_match']}%\n\n"
        res += f"**食材清单**:\n"
        for ing in ingredients:
            res += f"- {ing}\n"
        res += f"\n**详细烹饪步骤**:\n"
        for i, step in enumerate(steps, 1):
            res += f"{i}. {step}\n"
        res += f"\n**预估时间**: {time_label}\n"
        res += f"**难度等级**: {difficulty_label}\n"
        res += f"**卡路里估算**: {calories_label}\n"
        res += f"**说明/提示**: {intro}\n"
        res += f"**数据来源**: [{source_file}](https://github.com/Anduin2017/HowToCook/blob/main/dishes/{source_file})\n"
    else:
        res = f"### 🤖 HowToCook AI RAG Assistant Recommendation\n\n"
        res += f"**Recommended recipe**: {name}\n\n"
        res += f"**Why it matches**: {why_recommended} (Total Recommendation Score: **{scores['final_score']}%**)\n"
        res += f"  - Ingredient Match: {scores['ingredient_match']}%\n"
        res += f"  - Semantic Similarity: {scores['semantic_similarity']}%\n"
        res += f"  - Time Constraint: {scores['time_match']}%\n"
        res += f"  - Difficulty Match: {scores['difficulty_match']}%\n\n"
        res += f"**Ingredients**:\n"
        for ing in ingredients:
            res += f"- {ing}\n"
        res += f"\n**Cooking steps**:\n"
        for i, step in enumerate(steps, 1):
            res += f"{i}. {step}\n"
        res += f"\n**Estimated time**: {time_label}\n"
        res += f"**Difficulty**: {difficulty_label}\n"
        res += f"**Calories**: {calories_label}\n"
        res += f"**Notes**: {intro}\n"
        res += f"**Source**: [recipe_repo/dishes/{source_file}](https://github.com/Anduin2017/HowToCook/blob/main/dishes/{source_file})\n"
        
    return res

@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    global recipes
    if not recipes and INDEX_PATH.exists():
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                recipes = json.load(f)
        except Exception:
            pass

    user_msg = payload.message.strip()
    session_id = payload.sessionId
    
    # Initialize session
    if session_id not in conversations:
        conversations[session_id] = {
            "messages": [],
            "last_suggestions": []
        }
        
    session = conversations[session_id]
    
    # Auto detect language
    # Override if explicit language setting passed from frontend
    if payload.language:
        is_zh = (payload.language == "zh")
    else:
        is_zh = is_chinese(user_msg)
        
    # Check if the user is requesting steps for a recipe from previous suggestions
    recipe_index_to_show = None
    step_keywords_zh = ["步骤", "做法", "操作", "第", "怎么做", "如何做", "详细", "流程", "preview", "预览"]
    step_keywords_en = ["step", "recipe", "how to cook", "details", "instruction", "make", "prepare", "preview"]
    
    has_step_intent = False
    if is_zh:
        has_step_intent = any(k in user_msg for k in step_keywords_zh) if user_msg else False
    else:
        has_step_intent = any(k in user_msg.lower() for k in step_keywords_en) if user_msg else False
        
    # Try to parse index (1-5) or match recipe name
    matched_recipe_item = None
    
    if user_msg:
        digit_match = re.search(r'\b([1-5])\b', user_msg)
        if not digit_match:
            cn_digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
            for k, v in cn_digits.items():
                if k in user_msg:
                    recipe_index_to_show = v
                    break
        else:
            recipe_index_to_show = int(digit_match.group(1))
            
        if has_step_intent and recipe_index_to_show is not None:
            idx = recipe_index_to_show - 1
            if 0 <= idx < len(session["last_suggestions"]):
                matched_recipe_item = session["last_suggestions"][idx]
                
        if not matched_recipe_item:
            # Match by name
            for r_item in session["last_suggestions"]:
                r = r_item["recipe"]
                name_zh = r["recipe_name_original"].lower()
                name_en = r["recipe_name_english"].lower()
                if name_zh in user_msg.lower() or name_en in user_msg.lower():
                    matched_recipe_item = r_item
                    break

    # If we found a recipe to show details for (step request):
    if matched_recipe_item:
        recipe = matched_recipe_item["recipe"]
        scores = matched_recipe_item["scores"]
        why_recommended = matched_recipe_item["why_recommended"]
        
        reply = generate_structured_rag_response(recipe, why_recommended, scores, is_zh)
        images = recipe["images"]
        if images:
            reply += f"\n\n![{recipe['recipe_name_original']}](/api/images?path={images[0]})"
            
        session["messages"].append({"role": "user", "content": user_msg or "Show Details"})
        session["messages"].append({"role": "bot", "content": reply})
        
        return {
            "reply": reply,
            "recipes": [recipe],
            "scores": [scores],
            "why_recommended": [why_recommended],
            "mode": "detail"
        }
        
    # If not requesting steps, perform ranked search
    # Resolve filters
    user_ingredients = payload.ingredients
    if not user_ingredients and user_msg:
        # Extract from text message
        user_ingredients = extract_ingredients(user_msg, is_zh)
        
    time_limit = payload.timeLimit
    if not time_limit and user_msg:
        time_limit = extract_time_limit(user_msg)
        
    difficulty_pref = payload.difficulty
    category_pref = payload.category
    
    # Run explainable ranking search
    suggested_items = rank_recipes(
        query_message=user_msg,
        selected_ingredients=user_ingredients,
        time_limit=time_limit,
        difficulty_pref=difficulty_pref,
        category_pref=category_pref,
        is_zh_lang=is_zh
    )
    
    # Store suggestions in session context
    session["last_suggestions"] = suggested_items
    
    # Extract recipe structures
    suggested_recipes = [item["recipe"] for item in suggested_items]
    suggested_scores = [item["scores"] for item in suggested_items]
    suggested_why = [item["why_recommended"] for item in suggested_items]
    
    # Build reply
    if not suggested_items:
        if is_zh:
            reply = "抱歉，没有找到符合您要求的菜谱。您可以尝试提供其他食材或调整过滤条件！"
        else:
            reply = "Sorry, I couldn't find any recipes matching your criteria. Try suggesting different ingredients or adjusting the filters!"
    else:
        # RAG grounded response for top recommendation
        top_item = suggested_items[0]
        reply = generate_structured_rag_response(top_item["recipe"], top_item["why_recommended"], top_item["scores"], is_zh)
        images = top_item["recipe"]["images"]
        if images:
            reply += f"\n\n![{top_item['recipe']['recipe_name_original']}](/api/images?path={images[0]})"
            
    if user_msg:
        session["messages"].append({"role": "user", "content": user_msg})
    session["messages"].append({"role": "bot", "content": reply})
    
    return {
        "reply": reply,
        "recipes": suggested_recipes,
        "scores": suggested_scores,
        "why_recommended": suggested_why,
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
