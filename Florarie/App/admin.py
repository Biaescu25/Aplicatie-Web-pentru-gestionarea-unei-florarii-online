from django.contrib import admin

# Register your models here.

from .models import Product, CartItem, Order, Flower, BouquetShape, Greenery, WrappingPaper, CustomBouquet

class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'image_preview')
    
    def image_preview(self, obj):
        return obj.image.url if obj.image else "(No Image)"
    
    image_preview.short_description = "Image"

class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'total_price', 'created_at', 'payment_status')
    

admin.site.register(Product, ProductAdmin)
admin.site.register(CartItem)
admin.site.register(Order, OrderAdmin)
admin.site.register(Flower)
admin.site.register(BouquetShape)
admin.site.register(Greenery)
admin.site.register(WrappingPaper)
admin.site.register(CustomBouquet)   

# admin.site.register(User)