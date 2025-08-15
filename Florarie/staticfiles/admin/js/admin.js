document.addEventListener('DOMContentLoaded', function () {
    const inStoreCheckbox = document.querySelector('#id_in_store');

    const auctionFields = [
        'auction_manual',
        'auction_start_time',
        'auction_floor_price',
        'auction_interval_minutes',
        'auction_drop_amount'
    ];

    function toggleAuctionFields() {
        const show = inStoreCheckbox.checked;
    
        auctionFields.forEach(fieldName => {
            // First try a direct lookup by ID for regular inputs
            let input = document.querySelector(`#id_${fieldName}`);
            let row = null;
    
            if (input) {
                row = input.closest('.form-row');
            }
    
            // If not found, fall back to field-specific class (always present)
            if (!row) {
                row = document.querySelector(`.field-${fieldName}`);
            }
    
            if (row) {
                row.style.display = show ? '' : 'none';
            }
        });
    }
    

    if (inStoreCheckbox) {
        toggleAuctionFields();
        inStoreCheckbox.addEventListener('change', toggleAuctionFields);
    }
});


function previewImage(input) {
    const preview = document.getElementById('image-preview');
    if (input.files && input.files[0]) {
        const reader = new FileReader();
        reader.onload = function (e) {
            if (preview) {
                preview.src = e.target.result;
            }
        };
        reader.readAsDataURL(input.files[0]);
    }
}
