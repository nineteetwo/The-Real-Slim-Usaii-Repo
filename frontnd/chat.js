/* =============================================
   Chat Page Scripts
============================================= */
document.addEventListener('DOMContentLoaded', function () {
    var chatMessagesContainer = document.querySelector('.chat-messages'); 
    var sidebarContainer = document.querySelector('.sidebar .chat-list');

    // Display User Session Info into Sidebar UI 
    var savedName = localStorage.getItem('calhelpr_name');
    var sidebarNameSpan = document.getElementById('sidebar-name');
    var sidebarSigninText = document.getElementById('sidebar-signin-text');
    var topBarSignInLink = document.getElementById('sign-in-link');

    if (savedName) {
        if (sidebarNameSpan) sidebarNameSpan.innerText = savedName;
        if (sidebarSigninText) sidebarSigninText.style.display = 'none';
        if (topBarSignInLink) {
            topBarSignInLink.innerText = "Sign Out";
            topBarSignInLink.href = "#";
            topBarSignInLink.addEventListener('click', function(e) {
                e.preventDefault();
                
                if (chatMessagesContainer) {
                    chatMessagesContainer.innerHTML = '';
                }
                
                localStorage.clear(); 
                window.location.reload();
            });
        }
    }

    function createDraftThreadId() {
        if (window.crypto && window.crypto.randomUUID) {
            return "thread_" + window.crypto.randomUUID();
        }
        return "thread_" + Date.now() + "_" + Math.random().toString(16).slice(2);
    }

    // State variable to store our unique session thread identifier string.
    // New chats get a context immediately so uploads/artifacts do not bleed across chats.
    var startupThreadRowId = getActiveThreadRowId();
    var runtimeThreadSessionString = startupThreadRowId ? (localStorage.getItem("activeThreadId") || createDraftThreadId()) : createDraftThreadId();
    localStorage.setItem("activeThreadId", runtimeThreadSessionString);
    window.getCalhelprThreadId = function() {
        return runtimeThreadSessionString;
    };

    // If returning from a rejection event on the tracker page, restore context and greet the user
    var pendingRejection = localStorage.getItem("showRejectionGreeting");
    if (pendingRejection) {
        localStorage.removeItem("showRejectionGreeting");
        var rejectedProgram = localStorage.getItem("pendingRejectedProgram") || "a program";
        localStorage.removeItem("pendingRejectedProgram");

        var savedThread = localStorage.getItem("activeThreadId");
        if (savedThread) {
            runtimeThreadSessionString = savedThread;
        }

        if (chatMessagesContainer) {
            appendMessageBubble(
                `I see your application to ${rejectedProgram} was rejected. Would you like me to search for alternative community programs you may qualify for? Just say "yes" to proceed.`,
                'bot'
            );
        }
    }

    // Helper to get thread database row fallback ID from the live URL state
    function getActiveThreadRowId() {
        var urlParams = new URLSearchParams(window.location.search);
        return urlParams.get('thread');
    }

    if (sidebarContainer) {
        fetchSidebarHistory();
    }

    var initialRowId = getActiveThreadRowId();
    if (initialRowId && chatMessagesContainer) {
        loadSpecificThread(initialRowId);
    }

    async function fetchSidebarHistory() {
        try {
            var activeUserEmail = localStorage.getItem('calhelpr_email');
            var url = 'http://127.0.0.1:8000/api/history';
            
            if (activeUserEmail) {
                url += '?email=' + encodeURIComponent(activeUserEmail);
            }

            var response = await fetch(url);
            if (!response.ok) return;

            var data = await response.json();
            var records = data.history.reverse(); 

            if (sidebarContainer) {
                sidebarContainer.innerHTML = ''; 
                var currentActiveRowId = getActiveThreadRowId();

                records.forEach(function (record) {
                    var sidebarItem = document.createElement('a');
                    
                    sidebarItem.href = 'index.html?thread=' + encodeURIComponent(record.id);
                    sidebarItem.className = 'chat-item';
                    
                    if (currentActiveRowId && String(record.id) === String(currentActiveRowId)) {
                        sidebarItem.classList.add('active');
                        // Synchronize our session key track back onto runtime
                        runtimeThreadSessionString = record.thread_id;
                    }
                    
                    sidebarItem.addEventListener('click', function() {
                        // Clear attachment when switching to an old thread to prevent cross-session contamination
                        if (!currentActiveRowId || String(record.id) !== String(currentActiveRowId)) {
                            localStorage.removeItem('calhelpr_last_upload_path');
                            localStorage.removeItem('calhelpr_last_upload_name');
                        }
                    });
                    
                    var sidebarTitle = record.user_query.length > 25 ? record.user_query.substring(0, 25) + "..." : record.user_query;
                    
                    sidebarItem.innerHTML = `
                        <i class="fa-regular fa-message"></i>
                        <span>${sidebarTitle}</span>
                    `;
                    sidebarContainer.appendChild(sidebarItem);
                });
            }
        } catch (error) {
            console.error("Failed to load sidebar logs:", error);
        }
    }

    async function loadSpecificThread(rowId) {
        try {
            var response = await fetch('http://127.0.0.1:8000/api/thread?id=' + encodeURIComponent(rowId));
            
            // Handle if the HTTP request outright reports a 404
            if (response.status === 404) {
                clearStaleThreadState(rowId);
                return;
            }

            if (!response.ok) return;

            var data = await response.json();
            
            // If the backend returns an empty room gracefully with no thread_id, clear the stale URL param
            if (data.thread_id === null && (!data.messages || data.messages.length === 0)) {
                clearStaleThreadState(rowId);
                return;
            }

            runtimeThreadSessionString = data.thread_id; // Sync tracking string
            localStorage.setItem("activeThreadId", runtimeThreadSessionString);
            if (window.refreshChatWorkflow) window.refreshChatWorkflow();

            if (chatMessagesContainer && data.messages) {
                chatMessagesContainer.innerHTML = '';
                
                var welcomeText = document.querySelector('.welcome-text');
                var disclaimer = document.querySelector('.disclaimer');
                if (welcomeText) welcomeText.style.display = 'none';
                if (disclaimer) disclaimer.style.display = 'none';

                data.messages.forEach(function (msg) {
                    appendMessageBubble(msg.text, msg.sender);
                });
            }
        } catch (error) {
            console.error("Failed to recover conversation thread data:", error);
        }
    }

    // Helper to clear bad/empty query parameters from the address bar
    function clearStaleThreadState(rowId) {
        console.warn(`Thread ID ${rowId} not found or empty in database. Resetting to empty session.`);
        var cleanUrl = window.location.protocol + "//" + window.location.host + window.location.pathname;
        window.history.replaceState({}, '', cleanUrl);
        runtimeThreadSessionString = createDraftThreadId();
        localStorage.setItem("activeThreadId", runtimeThreadSessionString);
        if (window.refreshChatWorkflow) window.refreshChatWorkflow();
    }

    var attachedDocIndicator = document.getElementById('attached-document-indicator');
    var attachedDocName = document.getElementById('attached-document-name');
    var removeAttachedDocBtn = document.getElementById('remove-attached-doc');

    function updateAttachedDocUI() {
        var docName = localStorage.getItem('calhelpr_last_upload_name');
        if (docName && attachedDocIndicator) {
            attachedDocName.innerText = docName;
            attachedDocIndicator.style.display = 'flex';
        } else if (attachedDocIndicator) {
            attachedDocIndicator.style.display = 'none';
        }
    }

    if (removeAttachedDocBtn) {
        removeAttachedDocBtn.addEventListener('click', function() {
            localStorage.removeItem('calhelpr_last_upload_path');
            localStorage.removeItem('calhelpr_last_upload_name');
            updateAttachedDocUI();
        });
    }

    // Clear uploads when clicking New Chat
    var newChatBtn = document.getElementById('nav-new-chat');
    if (newChatBtn) {
        newChatBtn.addEventListener('click', function(e) {
            e.preventDefault();
            localStorage.removeItem('calhelpr_last_upload_path');
            localStorage.removeItem('calhelpr_last_upload_name');
            runtimeThreadSessionString = createDraftThreadId();
            localStorage.setItem("activeThreadId", runtimeThreadSessionString);
            var cleanUrl = window.location.protocol + "//" + window.location.host + window.location.pathname;
            window.history.pushState({}, '', cleanUrl);
            if (chatMessagesContainer) chatMessagesContainer.innerHTML = '';
            var welcomeText = document.querySelector('.welcome-text');
            var disclaimer = document.querySelector('.disclaimer');
            if (welcomeText) welcomeText.style.display = '';
            if (disclaimer) disclaimer.style.display = '';
            updateAttachedDocUI();
            if (window.refreshChatWorkflow) window.refreshChatWorkflow();
        });
    }

    updateAttachedDocUI();

    function appendMessageBubble(text, sender) {
        var msgDiv = document.createElement('div');
        msgDiv.className = 'message ' + sender; 

        var avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        avatar.innerHTML = sender === 'user' ? '<i class="fa-regular fa-user"></i>' : '<i class="bi bi-plus-lg"></i>';

        var contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';

        var textDiv = document.createElement('div');
        textDiv.className = 'message-text';
        textDiv.innerText = text;

        contentDiv.appendChild(textDiv);
        msgDiv.appendChild(avatar);
        msgDiv.appendChild(contentDiv);
        
        chatMessagesContainer.appendChild(msgDiv);
        chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;

        return msgDiv;
    }
    
});
