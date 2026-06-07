// Generate a random session ID if not already stored
let sessionId = localStorage.getItem('cook_chatbot_session_id');
if (!sessionId) {
    sessionId = 'session_' + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('cook_chatbot_session_id', sessionId);
}

const chatForm = document.getElementById('chat-form');
const userInput = document.getElementById('user-input');
const messagesContainer = document.getElementById('messages-container');
const sendButton = document.getElementById('send-button');

// Helper to format simple markdown to HTML
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
    // Convert bulleted list items to class-marked list items
    html = html.replace(/^-\s+(.*?)$/gm, '<li class="bullet-item">$1</li>');
    // Convert numbered list items to class-marked list items
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

// Append message to chat container
function appendMessage(sender, content, recipes = [], mode = 'list') {
    const wrapper = document.createElement('div');
    wrapper.className = `message-wrapper ${sender}`;

    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.innerHTML = sender === 'user' ? '<i class="fa-solid fa-user"></i>' : '<i class="fa-solid fa-robot"></i>';
    wrapper.appendChild(avatar);

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = formatMarkdown(content);

    // If there are suggested recipes, append recipe cards
    if (recipes && recipes.length > 0) {
        const cardsContainer = document.createElement('div');
        cardsContainer.className = 'recipe-cards-container';

        recipes.forEach((recipe, idx) => {
            const card = document.createElement('div');
            card.className = 'recipe-card';
            
            // Handle language strings
            const isZh = sender === 'user' ? false : content.includes('查看第几道菜'); // heuristic detection
            const name = isZh ? recipe.name_zh : recipe.name_en;
            const intro = isZh ? recipe.intro_zh : recipe.intro_en;
            const diffStars = '★'.repeat(recipe.difficulty || 2);
            
            // Set image
            let imageStyle = '';
            if (recipe.images && recipe.images.length > 0) {
                const imgUrl = `/api/images?path=${encodeURIComponent(recipe.images[0])}`;
                imageStyle = `<div class="recipe-card-img" style="background-image: url('${imgUrl}')">
                                <div class="recipe-card-badge">${recipe.time_category}</div>
                              </div>`;
            } else {
                imageStyle = `<div class="recipe-card-placeholder">
                                <i class="fa-solid fa-utensils"></i>
                              </div>`;
            }

            card.innerHTML = `
                ${imageStyle}
                <div class="recipe-card-content">
                    <h4 class="recipe-card-title">${name}</h4>
                    <div class="recipe-card-meta">
                        <span><i class="fa-solid fa-clock"></i> ${recipe.cooking_time_minutes}m</span>
                        <span><i class="fa-solid fa-star"></i> ${diffStars}</span>
                        ${recipe.calories ? `<span><i class="fa-solid fa-fire"></i> ${recipe.calories} kcal</span>` : ''}
                    </div>
                    <button class="recipe-card-btn">
                        <i class="fa-solid fa-book-open"></i> ${isZh ? '查看详细步骤' : 'View Full Recipe'}
                    </button>
                </div>
            `;
            
            // Click to request steps
            card.addEventListener('click', () => {
                const reqText = isZh ? `查看第 ${idx + 1} 道菜的步骤` : `show steps for recipe ${idx + 1}`;
                sendMessage(reqText);
            });
            
            cardsContainer.appendChild(card);
        });
        
        bubble.appendChild(cardsContainer);
    }

    wrapper.appendChild(bubble);
    messagesContainer.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

// Show/Hide typing loader
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

// Handle communication
async function sendMessage(text) {
    if (!text.trim()) return;

    // Append user message
    appendMessage('user', text);
    userInput.value = '';
    
    // Disable inputs
    userInput.disabled = true;
    sendButton.disabled = true;
    
    showTypingIndicator();
    
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: text,
                sessionId: sessionId
            })
        });
        
        if (!response.ok) {
            throw new Error('Server error');
        }
        
        const data = await response.json();
        removeTypingIndicator();
        
        // Append bot response with suggestions (if mode is list)
        const recipes = data.mode === 'list' ? data.recipes : [];
        appendMessage('bot', data.reply, recipes, data.mode);
        
    } catch (error) {
        console.error(error);
        removeTypingIndicator();
        appendMessage('bot', '⚠️ Sorry, something went wrong on our side. Please make sure the backend server is running and try again.');
    } finally {
        userInput.disabled = false;
        sendButton.disabled = false;
        userInput.focus();
    }
}

// Sidebar suggestions handler
window.sendSuggestion = function(text) {
    sendMessage(text);
};

// Reset Session handler
window.resetSession = function() {
    sessionId = 'session_' + Math.random().toString(36).substring(2, 15);
    localStorage.setItem('cook_chatbot_session_id', sessionId);
    messagesContainer.innerHTML = '';
    
    // Add original greeting
    const welcomeHTML = `
        <p>👋 Hello! I am your AI Cooking Assistant. I can help you search and cook recipes from the <strong>HowToCook</strong> developer cooking guide.</p>
        <p>Tell me what ingredients you have, or how much time you want to spend, and I will recommend the best recipes!</p>
        <p class="zh-info">👋 你好！我是你的 AI 厨房助手。我能帮你查询 <strong>HowToCook</strong> (程序员在家做饭指南) 中的菜谱。告诉我你手头有的食材，或者限定的烹饪时间，我会为你量身推荐合适的菜谱！</p>
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

// Form event listener
chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(userInput.value);
});
