from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse

from .models import Product, CartItem, Order, Flower, BouquetShape, Greenery, WrappingPaper, CustomBouquet

class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'in_store','delete_link')
    #readonly_fields = ('get_readonly_fields')  # Make delete_link visible in the detail view

    def get_readonly_fields(self, request, obj=None):
        if obj and not obj.in_store:
            return super().get_readonly_fields(request, obj) + ('auction_manual', 'auction_start_time', 'auction_floor_price', 'auction_interval_minutes', 'auction_drop_amount')
        return super().get_readonly_fields(request, obj)

    def delete_link(self, obj):
        url = reverse('admin:App_product_delete', args=[obj.pk])  # Replace "appname" with your app's name
        return format_html('<a class="button" href="{}">Delete</a>', url)
    delete_link.short_description = 'Delete'
    delete_link.allow_tags = True

class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user','total_price', 'created_at', 'payment_status', 'linked_products')
    readonly_fields = ('linked_products_table',)  # Make linked_products visible in the detail view

    def linked_products(self, obj):
        return ", ".join([f"{item.quantity} x {item.product.name}" for item in obj.items.all()])
    
    linked_products.short_description = "Products"

    def linked_products_table(self, obj):
        rows = "".join(
            [
                f'<tr><td>{item.quantity}</td><td><a href="{reverse("admin:App_product_change", args=[item.product.id])}">{item.product.name}</a></td></tr>'
                for item in obj.items.all()
            ]
        )
        return format_html(
            f'<table style="border-collapse: collapse; width: 100%;">'
            f'<thead><tr><th style="border: 1px solid #ddd; padding: 8px;">Quantity</th>'
            f'<th style="border: 1px solid #ddd; padding: 8px;">Product</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
        )
    linked_products.short_description = "Products"    

class CustomBouquetAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'shape', 'wrapping', 'created_at', 'related_flowers')
    readonly_fields = ('related_flowers',)  # Display related flowers in the detail view

admin.site.register(Product, ProductAdmin)
admin.site.register(CartItem)
admin.site.register(Order, OrderAdmin)
admin.site.register(Flower)
admin.site.register(BouquetShape)
admin.site.register(Greenery)
admin.site.register(WrappingPaper)
admin.site.register(CustomBouquet, CustomBouquetAdmin)   

# admin.site.register(User)