from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.contrib.admin.widgets import FilteredSelectMultiple
from .forms import ProductForm
from .models import Product, CartItem, Order, Flower, BouquetShape, Greenery, WrappingPaper, WrappingColor, WrappingPaperColor, CustomBouquet, ContactMessage, VisitorLog


class ProductAdmin(admin.ModelAdmin):
    form = ProductForm
    list_display = ('name', 'price',  'bid_submited', 'number_of_purcheses', 'stock', 'delete_link')
    list_filter = ('bid_submited', 'in_store', 'is_custom', 'category')
    readonly_fields = ('is_custom', 'before_auction_price', 'in_store', 'number_of_purcheses')

    class Media:
        js = ('admin/js/admin.js',)  
 
    def delete_link(self, obj):
        url = reverse('admin:App_product_delete', args=[obj.pk])
        return format_html('<a class="button" href="{}">Delete</a>', url)
    delete_link.short_description = 'Delete'


class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'delivery_type', 'desired_delivery_date', 'delivery_time_slot', 'total_price', 'delivery_fee', 'created_at', 'payment_status_display', 'linked_products')
    list_filter = ('delivery_type', 'payment_status', 'created_at', 'desired_delivery_date')
    readonly_fields = ('linked_products_table',)  # Make linked_products visible in the detail view

    def payment_status_display(self, obj):
        if obj.payment_method == 'cash':
            if obj.payment_status:
                return format_html('<span style="color: green;"> Plătit la livrare</span>')
            else:
                return format_html('<span style="color: orange;"> În așteptare (plata la livrare)</span>')
        else:  # card payment
            if obj.payment_status:
                return format_html('<span style="color: green;">Plătit cu cardul</span>')
            else:
                return format_html('<span style="color: red;"> Neplătit</span>')
    payment_status_display.short_description = 'Status plată'

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
    readonly_fields = ('related_flowers',)


class ContactMessageAdmin(admin.ModelAdmin):

    readonly_fields = ('name', 'email', 'message', 'submitted_at')

class WrappingPaperColorInline(admin.TabularInline):
    model = WrappingPaperColor
    extra = 1
    

class WrappingPaperAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'display_colors')
    inlines = [WrappingPaperColorInline]

    def display_colors(self, obj):
        colors = obj.color_variants.all()
        return ", ".join(
            f"{c.color.name}{' (✓)' if c.in_stock else ' (✗)'}"
            for c in colors
        )
    display_colors.short_description = "Culori (cu stoc)"


admin.site.site_header = "Florarie Admin"
admin.site.register(Product, ProductAdmin)
admin.site.register(CartItem)
admin.site.register(Order, OrderAdmin)
admin.site.register(Flower)
admin.site.register(BouquetShape)
admin.site.register(Greenery)
admin.site.register(WrappingColor)
admin.site.register(WrappingPaper, WrappingPaperAdmin)
admin.site.register(CustomBouquet, CustomBouquetAdmin)   
admin.site.register(ContactMessage, ContactMessageAdmin)
admin.site.register(VisitorLog)

# admin.site.register(User)