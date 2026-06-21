/* =============================================
    History Page Scripts
============================================= */
document.addEventListener('DOMContentLoaded', function () {
    var searchInput = document.getElementById('history-search-input');
    
    // Target both containers
    var sidebarContainer = document.querySelector('.sidebar .chat-list');
    var mainHistoryContainer = document.getElementById('history-list'); 

    // Pull data from database when page opens
    fetchConversationHistory();

    async function fetchConversationHistory() {
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

            // Clear out static mock placeholders from both sections
            if (sidebarContainer) sidebarContainer.innerHTML = '';
            if (mainHistoryContainer) mainHistoryContainer.innerHTML = '';

            // Loop through entries and populate both sides
            records.forEach(function (record) {
                
                // Left sidebar panel
                if (sidebarContainer) {
                    var sidebarItem = document.createElement('a');
                    sidebarItem.href = './index.html?thread=' + encodeURIComponent(record.id);
                    sidebarItem.className = 'chat-item';
                    
                    var sidebarTitle = record.user_query.length > 25 ? record.user_query.substring(0, 25) + "..." : record.user_query;
                    
                    sidebarItem.innerHTML = `
                        <i class="fa-regular fa-message"></i>
                        <span>${sidebarTitle}</span>
                    `;
                    sidebarContainer.appendChild(sidebarItem);
                }

                // Main right history panel (Mobile list view)
                if (mainHistoryContainer) {
                    var mainItem = document.createElement('a');
                    mainItem.href = './index.html?thread=' + encodeURIComponent(record.id);
                    mainItem.className = 'history-item'; 

                    var mainTitle = record.user_query.length > 45 ? record.user_query.substring(0, 45) + "..." : record.user_query;
                    var mainPreview = record.ai_response.length > 120 ? record.ai_response.substring(0, 120) + "..." : record.ai_response;

                    mainItem.innerHTML = `
                        <div class="history-item-icon">
                            <i class="fa-regular fa-message"></i>
                        </div>
                        <div class="history-item-body">
                            <span class="history-item-title" style="font-weight: 600;">${mainTitle}</span>
                            <span class="history-item-preview">${mainPreview}</span>
                        </div>
                        <span class="history-item-time">Recent</span>
                    `;
                    mainHistoryContainer.appendChild(mainItem);
                }
            });

        } catch (error) {
            console.error("Failed to load historical data logs from database:", error);
        }
    }

    // Search input filtering logic (applies directly to main list view entries)
    if (searchInput) {
        searchInput.addEventListener('input', function () {
            var query = searchInput.value.toLowerCase().trim();
            var items = document.querySelectorAll('.history-item');
            var groups = document.querySelectorAll('.history-date-group');

            items.forEach(function (item) {
                var title = item.querySelector('.history-item-title');
                var preview = item.querySelector('.history-item-preview');
                var text = (title ? title.textContent : '') + ' ' + (preview ? preview.textContent : '');
                
                if (text.toLowerCase().indexOf(query) !== -1) {
                    item.style.display = ""; 
                } else {
                    item.style.display = "none";
                }
            });

            groups.forEach(function (group) {
                var visible = group.querySelectorAll('.history-item:not([style*="display: none"])');
                if (visible.length === 0) {
                    group.style.display = "none";
                } else {
                    group.style.display = "";
                }
            });
        });
    }
});