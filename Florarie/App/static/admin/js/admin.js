document.addEventListener('DOMContentLoaded', function () {
    const stockInput = document.querySelector('#id_stock');
    const auctionManualInput = document.querySelector('#id_auction_manual');

    // Only auction_manual field
    const auctionManualField = 'auction_manual';
    // All other auction fields
    const otherAuctionFields = [
        'auction_start_time',
        'auction_floor_price',
        'auction_interval_minutes',
        'auction_drop_amount'
    ];

    function toggleAuctionFields() {
        const stock = parseInt(stockInput ? stockInput.value : "0", 10);
        const showManual = stock > 0;

        // Show/hide auction_manual based on stock
        let manualInput = document.querySelector(`#id_${auctionManualField}`);
        let manualRow = manualInput ? manualInput.closest('.form-row') : document.querySelector(`.field-${auctionManualField}`);
        if (manualRow) {
            manualRow.style.display = showManual ? '' : 'none';
        }

        // Show/hide other auction fields based on auction_manual checked
        const showOthers = showManual && auctionManualInput && auctionManualInput.checked;
        otherAuctionFields.forEach(fieldName => {
            let input = document.querySelector(`#id_${fieldName}`);
            let row = input ? input.closest('.form-row') : document.querySelector(`.field-${fieldName}`);
            if (row) {
                row.style.display = showOthers ? '' : 'none';
            }
        });
    }

    if (stockInput) {
        toggleAuctionFields();
        stockInput.addEventListener('input', toggleAuctionFields);
    }
    if (auctionManualInput) {
        auctionManualInput.addEventListener('change', toggleAuctionFields);
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
