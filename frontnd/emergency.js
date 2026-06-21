/* =============================================
   Emergency Page Scripts
============================================= */
document.addEventListener('DOMContentLoaded', () => {
    const emergencyCards = document.querySelectorAll('.emergency-card-header');
    
    emergencyCards.forEach(cardHeader => {
        cardHeader.addEventListener('click', () => {
            const card = cardHeader.parentElement;
            const isExpanded = card.classList.contains('expanded');
            
            document.querySelectorAll('.emergency-card').forEach(c => {
                c.classList.remove('expanded');
            });
            
            if (!isExpanded) {
                card.classList.add('expanded');
            }
        });
    });
});
