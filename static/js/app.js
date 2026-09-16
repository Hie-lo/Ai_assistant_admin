// دستیار هوشمند کسب‌وکارهای مجازی — Web Panel JS
document.addEventListener('DOMContentLoaded', function() {
    // Auto-hide flash messages
    const flashes = document.querySelectorAll('.flash');
    flashes.forEach(flash => {
        setTimeout(() => {
            flash.style.opacity = '0';
            setTimeout(() => flash.remove(), 300);
        }, 5000);
    });

    // HTMX error handling
    document.body.addEventListener('htmx:responseError', function(event) {
        console.error('HTMX error:', event.detail);
    });

    // Confirm dangerous actions
    document.body.addEventListener('click', function(event) {
        const target = event.target;
        if (target.matches('[data-confirm]')) {
            if (!confirm(target.getAttribute('data-confirm'))) {
                event.preventDefault();
            }
        }
    });
});

// Utility: format Persian numbers
function toPersianNumber(n) {
    const persianDigits = '۰۱۲۳۴۵۶۷۸۹';
    return String(n).replace(/\d/g, d => persianDigits[d]);
}
