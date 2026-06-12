// Generate a random session ID if not already stored
let sessionId = localStorage.getItem('cook_chatbot_session_id');
if (!sessionId) {
    sessionId = 'session_' + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('cook_chatbot_session_id', sessionId);
}

// Global state for current search results
let currentRecipes = [];
let currentScores = [];
let currentWhyRecommended = [];
let currentLang = 'en'; // default output language state (heuristic or filter)

// Elements
const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const messagesContainer = document.getElementById('messages-container');
const sendButton = document.getElementById('send-button');
const recipeModal = document.getElementById('recipe-modal');
const cardsList = document.getElementById('recipe-cards-list');
const resultsCount = document.getElementById('results-count');

// 1. Tab Switching Logic
function switchTab(tabId) {
    // Hide all tab panes
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
    });
    // Remove active class from all nav items
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });

    // Show selected tab pane
    const activePane = document.getElementById(`tab-${tabId}`);
    if (activePane) {
        activePane.classList.add('active');
    }
    // Set active nav item
    const activeNav = document.getElementById(`nav-${tabId}`);
    if (activeNav) {
        activeNav.classList.add('active');
    }
}
window.switchTab = switchTab;

// 2. Helper to format simple markdown to HTML (for chat logs)
function formatMarkdown(text) {
    if (!text) return '';
    let html = text;
    
    // Replace headers
    html = html.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
    html = html.replace(/^#### (.*?)$/gm, '<h4>$1</h4>');
    
    // Replace bold and italics
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    
    // Handle inline lists
    html = html.replace(/^-\s+(.*?)$/gm, '<li class="bullet-item">$1</li>');
    html = html.replace(/^\d+\.\s+(.*?)$/gm, '<li class="number-item">$1</li>');
    
    // Wrap sequences of bullet items in <ul> tags
    html = html.replace(/(<li class="bullet-item">.*?<\/li>\s*)+/g, match => {
        return '<ul>' + match.replace(/ class="bullet-item"/g, '') + '</ul>';
    });
    
    // Wrap sequences of number items in <ol> tags
    html = html.replace(/(<li class="number-item">.*?<\/li>\s*)+/g, match => {
        return '<ol>' + match.replace(/ class="number-item"/g, '') + '</ol>';
    });

    // Replace images ![alt](url)
    html = html.replace(/!\[(.*?)\]\((.*?)\)/g, '<img src="$2" alt="$1" loading="lazy" />');

    // Replace newlines with breaks for normal paragraphs (outside lists)
    html = html.split('\n\n').map(p => {
        p = p.trim();
        if (!p) return '';
        if (p.startsWith('<h') || p.startsWith('<ul') || p.startsWith('<ol') || p.startsWith('<li>')) {
            return p;
        }
        return `<p>${p.replace(/\n/g, '<br>')}</p>`;
    }).join('');

    return html;
}

// 3. Serialize all Filters from the Left Pane
function getFilterValues() {
    const ingredientsVal = document.getElementById('filter-ingredients').value.trim();
    const categoryVal = document.getElementById('filter-category').value;
    const timeVal = document.getElementById('filter-time').value;
    const difficultyVal = document.getElementById('filter-difficulty').value;
    const languageVal = document.getElementById('filter-language').value;

    const filters = {};

    if (ingredientsVal) {
        // Split ingredients by comma, remove whitespace, filter empty values
        filters.ingredients = ingredientsVal.split(',').map(s => s.trim()).filter(s => s.length > 0);
    }
    if (categoryVal && categoryVal !== 'all') {
        filters.category = categoryVal;
    }
    if (timeVal) {
        filters.timeLimit = parseInt(timeVal, 10);
    }
    if (difficultyVal) {
        filters.difficulty = parseInt(difficultyVal, 10);
    }
    if (languageVal) {
        filters.language = languageVal;
        currentLang = languageVal;
    } else {
        filters.language = null; // Auto-detect
    }

    return filters;
}

// 4. Update the Active UI Lang state based on query content
function detectDisplayLanguage(messageText, filterLang) {
    if (filterLang) return filterLang;
    // Simple heuristic to check if query contains Chinese
    if (/[\u4e00-\u9fff]/.test(messageText)) {
        return 'zh';
    }
    return 'en';
}

// 5. Render Compact Recipe Cards in the Center Column
function renderRecipeCards(recipes, scores, whyRecommended, userLang) {
    cardsList.innerHTML = '';
    
    if (!recipes || recipes.length === 0) {
        cardsList.innerHTML = `
            <div class="results-placeholder">
                <i class="fa-solid fa-utensils"></i>
                <p>No matching recipes found. Try adjusting ingredients or cooking time limit!</p>
            </div>
        `;
        resultsCount.innerText = '0 matched';
        return;
    }

    resultsCount.innerText = `${recipes.length} matched`;

    recipes.forEach((recipe, idx) => {
        const score = scores[idx] || { final_score: 0, ingredient_match: 0, semantic_similarity: 0, time_match: 0, difficulty_match: 0 };
        const why = whyRecommended[idx] || '';
        
        // Determine whether to use Chinese or English strings
        const isZh = (userLang === 'zh');
        const titleMain = isZh ? recipe.recipe_name_original : recipe.recipe_name_english;
        const titleSub = isZh ? recipe.recipe_name_english : recipe.recipe_name_original;
        
        const difficultyStars = '★'.repeat(recipe.difficulty || 1);
        
        // Ingredients display immediately
        const ingredientList = isZh ? recipe.ingredients_zh : recipe.ingredients_en;
        const cleanedIngredients = ingredientList.filter(ing => ing && ing.length > 0).slice(0, 6);
        const ingredientsPreview = cleanedIngredients.join(', ') + (ingredientList.length > 6 ? '...' : '');

        // Formulate compact card HTML structure without any image placeholders if image is missing
        const card = document.createElement('div');
        card.className = 'recipe-card-compact';
        
        // Optional thin banner if recipe has images
        let optionalImageBanner = '';
        if (recipe.images && recipe.images.length > 0) {
            const imgUrl = `/api/images?path=${encodeURIComponent(recipe.images[0])}`;
            // Small thin image banner on top of compact card
            optionalImageBanner = `<div style="height: 100px; width: 100%; border-radius: 8px; background: url('${imgUrl}') center center / cover; border: 1px solid var(--border-color); margin-bottom: 6px;"></div>`;
        }

        card.innerHTML = `
            ${optionalImageBanner}
            <div class="recipe-card-header-row">
                <div>
                    <h4 class="recipe-card-title">${titleMain}</h4>
                    <span style="font-size: 11px; color: var(--text-muted); display: block; margin-top: 2px;">${titleSub}</span>
                </div>
                <div class="score-badge">${score.final_score}% Match</div>
            </div>

            <div class="recipe-card-ingredients">
                <strong>${isZh ? '主要食材' : 'Main Ingredients'}:</strong>
                <span>${ingredientsPreview}</span>
            </div>

            <div class="recipe-card-meta-row">
                <span>
                    <i class="fa-solid fa-clock"></i> 
                    ${recipe.cooking_time_minutes}m 
                    ${recipe.time_is_estimated ? `<span class="est-tag">(est)</span>` : ''}
                </span>
                <span>
                    <i class="fa-solid fa-star"></i> 
                    ${difficultyStars} 
                    ${recipe.difficulty_is_estimated ? `<span class="est-tag">(est)</span>` : ''}
                </span>
                ${recipe.calories ? `
                    <span>
                        <i class="fa-solid fa-fire"></i> 
                        ${recipe.calories} kcal 
                        ${recipe.calories_is_estimated ? `<span class="est-tag">(est)</span>` : ''}
                    </span>
                ` : ''}
                <span><i class="fa-solid fa-tag"></i> ${recipe.category}</span>
            </div>

            <div class="why-recommended-box">
                ${why}
            </div>

            <div class="card-actions">
                <button class="btn btn-outline" onclick="triggerPreview(${idx})">
                    <i class="fa-solid fa-eye"></i> ${isZh ? '预览菜谱' : 'Preview'}
                </button>
                <button class="btn btn-primary" onclick="triggerSendToChat(${idx})">
                    <i class="fa-solid fa-paper-plane"></i> ${isZh ? '详细步骤' : 'Send to RAG'}
                </button>
            </div>
        `;

        cardsList.appendChild(card);
    });
}

// 6. Recipe Preview Modal Handlers
function triggerPreview(idx) {
    const recipe = currentRecipes[idx];
    const score = currentScores[idx];
    const why = currentWhyRecommended[idx];
    
    if (!recipe) return;

    const isZh = (currentLang === 'zh');

    // Populate modal titles
    document.getElementById('modal-recipe-title').innerText = isZh 
        ? `${recipe.recipe_name_original} (${recipe.recipe_name_english})` 
        : `${recipe.recipe_name_english} (${recipe.recipe_name_original})`;

    // Metadata badges
    document.getElementById('modal-badge-time').innerHTML = `<i class="fa-solid fa-clock"></i> ${recipe.cooking_time_minutes} mins ${recipe.time_is_estimated ? '(est)' : ''}`;
    document.getElementById('modal-badge-difficulty').innerHTML = `<i class="fa-solid fa-star"></i> ${'★'.repeat(recipe.difficulty)} ${recipe.difficulty_is_estimated ? '(est)' : ''}`;
    document.getElementById('modal-badge-calories').innerHTML = `<i class="fa-solid fa-fire"></i> ${recipe.calories ? recipe.calories + ' kcal' : 'Not specified'} ${recipe.calories_is_estimated ? '(est)' : ''}`;
    document.getElementById('modal-badge-category').innerHTML = `<i class="fa-solid fa-tag"></i> ${recipe.category}`;

    // Score banner
    document.getElementById('modal-match-score').innerText = `${score ? score.final_score : 100}%`;
    document.getElementById('modal-match-reason').innerText = why || 'Highly recommended match.';

    // Ingredients
    const ingsList = document.getElementById('modal-ingredients-list');
    ingsList.innerHTML = '';
    const ingredients = isZh ? recipe.ingredients_zh : recipe.ingredients_en;
    ingredients.filter(ing => ing).forEach(ing => {
        const li = document.createElement('li');
        li.innerText = ing;
        ingsList.appendChild(li);
    });

    // Cooking steps
    const stepsList = document.getElementById('modal-steps-list');
    stepsList.innerHTML = '';
    const steps = isZh ? recipe.steps_zh : recipe.steps_en;
    steps.filter(step => step).forEach(step => {
        const li = document.createElement('li');
        li.innerText = step;
        stepsList.appendChild(li);
    });

    // Notes and Source reference
    document.getElementById('modal-notes').innerText = isZh ? recipe.intro_zh : recipe.intro_en;
    document.getElementById('modal-source-file').innerHTML = `Source File: <a href="https://github.com/Anduin2017/HowToCook/blob/main/dishes/${recipe.file_path}" target="_blank">${recipe.file_path}</a>`;

    // Chat CTA in modal
    const chatBtn = document.getElementById('modal-btn-chat');
    chatBtn.onclick = () => {
        closeRecipeModal();
        triggerSendToChat(idx);
    };

    // Open Modal
    recipeModal.classList.add('open');
}
window.triggerPreview = triggerPreview;

function closeRecipeModal() {
    recipeModal.classList.remove('open');
}
window.closeRecipeModal = closeRecipeModal;

// Close modal when clicking backdrop
recipeModal.addEventListener('click', (e) => {
    if (e.target === recipeModal) {
        closeRecipeModal();
    }
});

// 7. Send Recipe Selection Details to RAG Chat Panel
function triggerSendToChat(idx) {
    const recipe = currentRecipes[idx];
    if (!recipe) return;

    const isZh = (currentLang === 'zh');
    const name = isZh ? recipe.recipe_name_original : recipe.recipe_name_english;
    const reqText = isZh ? `查看第 ${idx + 1} 道菜 (${name}) 的详细步骤` : `Show detailed steps for recipe ${idx + 1}: ${name}`;
    
    // Switch to search tab to ensure UI focus
    switchTab('search');
    sendMessage(reqText);
}
window.triggerSendToChat = triggerSendToChat;

// 8. Append Chat Message to Right Assistant Column
function appendChatMessage(sender, content) {
    const wrapper = document.createElement('div');
    wrapper.className = `message-wrapper ${sender}`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.innerHTML = sender === 'user' ? '<i class="fa-solid fa-user"></i>' : '<i class="fa-solid fa-robot"></i>';
    wrapper.appendChild(avatar);

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = formatMarkdown(content);

    wrapper.appendChild(bubble);
    messagesContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

// 9. Show/Hide Typing Loader Indicators
let typingIndicator = null;
function showTypingIndicator() {
    if (typingIndicator) return;
    
    const wrapper = document.createElement('div');
    wrapper.className = 'message-wrapper bot typing';
    
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.innerHTML = '<i class="fa-solid fa-robot"></i>';
    wrapper.appendChild(avatar);
    
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = `
        <div class="typing-indicator">
            <span></span>
            <span></span>
            <span></span>
        </div>
    `;
    wrapper.appendChild(bubble);
    
    messagesContainer.appendChild(wrapper);
    typingIndicator = wrapper;
    scrollToBottom();
}

function removeTypingIndicator() {
    if (typingIndicator) {
        typingIndicator.remove();
        typingIndicator = null;
    }
}

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// 10. Core Send Message Pipeline (POST Request to FastAPI Server)
async function sendMessage(text, explicitFilters = null) {
    if (!text.trim()) return;

    // Append user query message in UI
    appendChatMessage('user', text);
    userInput.value = '';
    
    // Disable inputs during processing
    userInput.disabled = true;
    sendButton.disabled = true;
    
    showTypingIndicator();

    // Determine query context parameters
    const activeFilters = explicitFilters || getFilterValues();
    currentLang = detectDisplayLanguage(text, activeFilters.language);
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: text,
                sessionId: sessionId,
                ingredients: activeFilters.ingredients || null,
                timeLimit: activeFilters.timeLimit || null,
                difficulty: activeFilters.difficulty || null,
                language: currentLang,
                category: activeFilters.category || null
            })
        });
        
        if (!response.ok) {
            throw new Error('Server error');
        }
        
        const data = await response.json();
        removeTypingIndicator();
        
        // Save current search state globally
        if (data.recipes && data.recipes.length > 0) {
            // Merge or replace depending on mode
            if (data.mode === 'list') {
                currentRecipes = data.recipes;
                currentScores = data.scores;
                currentWhyRecommended = data.why_recommended;
                
                // Update Card UI
                renderRecipeCards(currentRecipes, currentScores, currentWhyRecommended, currentLang);
            }
        }
        
        // Append response text to chat pane
        appendChatMessage('bot', data.reply);
        
    } catch (error) {
        console.error(error);
        removeTypingIndicator();
        appendChatMessage('bot', '⚠️ Sorry, something went wrong on our side. Please ensure the backend FastAPI server is running properly.');
    } finally {
        userInput.disabled = false;
        sendButton.disabled = false;
        userInput.focus();
    }
}

// 11. Custom Search Handler (Triggered by Filters Button)
async function applySearchFilters() {
    const filters = getFilterValues();
    
    // Switch to search tab to ensure layouts match
    switchTab('search');

    // Display search status in chat
    const filterDesc = [];
    if (filters.ingredients) filterDesc.push(`ingredients: [${filters.ingredients.join(', ')}]`);
    if (filters.category) filterDesc.push(`category: ${filters.category}`);
    if (filters.timeLimit) filterDesc.push(`max time: ${filters.timeLimit}m`);
    if (filters.difficulty) filterDesc.push(`max difficulty: ${filters.difficulty}★`);
    
    const queryMessageText = filters.ingredients 
        ? `Recommend recipes with: ${filters.ingredients.join(', ')}`
        : `Discover recipes under filters`;

    // Disable inputs
    showTypingIndicator();

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: queryMessageText,
                sessionId: sessionId,
                ingredients: filters.ingredients || null,
                timeLimit: filters.timeLimit || null,
                difficulty: filters.difficulty || null,
                language: filters.language || null,
                category: filters.category || null
            })
        });

        if (!response.ok) throw new Error('Search failed');
        
        const data = await response.json();
        removeTypingIndicator();

        // Update state
        currentRecipes = data.recipes || [];
        currentScores = data.scores || [];
        currentWhyRecommended = data.why_recommended || [];
        currentLang = detectDisplayLanguage(queryMessageText, filters.language);

        // Update center column cards
        renderRecipeCards(currentRecipes, currentScores, currentWhyRecommended, currentLang);

        // Print match results to RAG chat panel
        appendChatMessage('bot', data.reply);

    } catch (error) {
        console.error(error);
        removeTypingIndicator();
        cardsList.innerHTML = `<p style="color: #ef4444; padding: 20px; text-align: center;">Search request failed. Please check backend FastAPI connection.</p>`;
    }
}
window.applySearchFilters = applySearchFilters;

// 12. Suggestion Queries Loader Helper
function loadQuerySuggestion(ingredients, category, maxTime) {
    document.getElementById('filter-ingredients').value = ingredients;
    document.getElementById('filter-category').value = category;
    document.getElementById('filter-time').value = maxTime.toString();
    applySearchFilters();
}
window.loadQuerySuggestion = loadQuerySuggestion;

// 13. Clear Filters Handler
function clearFilters() {
    document.getElementById('filter-ingredients').value = '';
    document.getElementById('filter-category').value = 'all';
    document.getElementById('filter-time').value = '';
    document.getElementById('filter-difficulty').value = '';
    document.getElementById('filter-language').value = '';
    
    cardsList.innerHTML = `
        <div class="results-placeholder">
            <i class="fa-solid fa-utensils"></i>
            <p>Filters cleared. Enter parameters and search again.</p>
        </div>
    `;
    resultsCount.innerText = '0 matched';
}
window.clearFilters = clearFilters;

// 14. Reset Conversation Session Handler
window.resetSession = function() {
    sessionId = 'session_' + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('cook_chatbot_session_id', sessionId);
    messagesContainer.innerHTML = '';
    clearFilters();
    
    const welcomeHTML = `
        <p>👋 Hello! I am your AI Cooking RAG Assistant. Ask me to search or explain recipes from the <strong>HowToCook</strong> database.</p>
        <p>Any searches or card selections will generate a fully-grounded step-by-step cooking guide here.</p>
    `;
    
    const wrapper = document.createElement('div');
    wrapper.className = `message-wrapper bot`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.innerHTML = '<i class="fa-solid fa-robot"></i>';
    wrapper.appendChild(avatar);

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = welcomeHTML;
    wrapper.appendChild(bubble);
    messagesContainer.appendChild(wrapper);
};

// 15. Form Submit listener
chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(userInput.value);
});

// Initial tab select
switchTab('home');
