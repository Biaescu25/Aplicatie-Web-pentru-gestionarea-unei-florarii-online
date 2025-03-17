from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from .models import Product, CartItem

def home(request):
    products = Product.objects.all()
    return render(request, "home.html", {'products': products})

def load_more(request):
    return render(request, "partials/more_content.html")

def cart_view(request):
    if request.user.is_authenticated:
        cart_items = CartItem.objects.filter(user=request.user)
    else:
        session_id = request.session.session_key
        if not session_id:
            request.session.create()
            session_id = request.session.session_key
        cart_items = CartItem.objects.filter(session_id=session_id)
    
    context = {
        'cart_items': cart_items,
    }
    return render(request, 'cart.html', context)

def cart_count(request):
    if request.user.is_authenticated:
        count = CartItem.objects.filter(user=request.user).count()
    else:
        session_id = request.session.session_key
        if not session_id:
            request.session.create()
            session_id = request.session.session_key
        count = CartItem.objects.filter(session_id=session_id).count()
    return JsonResponse({'count': count})

def get_or_create_session_id(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key

def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if request.user.is_authenticated:
        cart_item, created = CartItem.objects.get_or_create(user=request.user, product=product)
    else:
        session_id = get_or_create_session_id(request)
        cart_item, created = CartItem.objects.get_or_create(session_id=session_id, product=product)

    if not created:
        cart_item.quantity += 1
        cart_item.save()

    cart_count = CartItem.objects.filter(user=request.user if request.user.is_authenticated else None,
                                         session_id=request.session.session_key if not request.user.is_authenticated else None).count()

    return JsonResponse({'count': cart_count})

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

def increment_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        cart_item.quantity += 1
        cart_item.save()
    return cart_view(request)

def decrement_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item and cart_item.quantity > 1:
        cart_item.quantity -= 1
        cart_item.save()
    return cart_view(request)

def products_by_category(request, category):
    sort_order = request.GET.get('sort', 'asc')
    min_price = request.GET.get('min_price', 10)
    max_price = request.GET.get('max_price', 400)
    
    if sort_order == 'desc':
        products = Product.objects.filter(category=category, price__gte=min_price, price__lte=max_price).order_by('-price')
    else:
        products = Product.objects.filter(category=category, price__gte=min_price, price__lte=max_price).order_by('price')
    
    context = {
        'products': products,
        'category': category,
        'sort_order': sort_order,
        'min_price': min_price,
        'max_price': max_price
    }
    return render(request, "products_by_category.html", context)
