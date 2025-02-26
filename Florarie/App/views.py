from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from .models import Product, CartItem

def home(request):
    products = Product.objects.all()
    return render(request, "home.html", {'products': products})

def load_more(request):
    return render(request, "partials/more_content.html")

def cart_view(request):
    cart_items = CartItem.objects.all()
    total_price = sum(item.total_price() for item in cart_items)
    if request.headers.get('HX-Request'):  # Check if it's an HTMX request
        return render(request, 'cart_partial.html', {'cart_items': cart_items})

    return render(request, 'home.html', {'cart_items': cart_items, 'total_price': total_price})

def cart_count(request):
    count = CartItem.objects.count()
    return JsonResponse({'count': count})

def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    cart_item, created = CartItem.objects.get_or_create(product=product)
    
    if not created:
        cart_item.quantity += 1
        cart_item.save()
    
    return JsonResponse({"success": True})

def remove_from_cart(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        cart_item.delete()
    return JsonResponse({"success": True})

def update_cart(request, product_id, quantity):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        cart_item.quantity = quantity
        cart_item.save()
    return JsonResponse({"success": True, "total_price": cart_item.total_price()})
